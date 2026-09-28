#!/usr/bin/env python3
"""Bounded DesktopHarness MCP verification; input injection is opt-in and caller supplied."""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

_session_id: str | None = None
_direct_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

def call(endpoint: str, method: str, params: dict) -> dict:
    global _session_id
    payload = {
        "jsonrpc": "2.0",
        "id": f"probe-{time.time_ns()}",
        "method": method,
        "params": params,
    }
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    if _session_id:
        headers["Mcp-Session-Id"] = _session_id
    request = urllib.request.Request(
        endpoint,
        data=json.dumps(payload).encode(),
        method="POST",
        headers=headers,
    )
    with _direct_opener.open(request, timeout=12) as response:  # explicit user-supplied endpoint
        _session_id = response.headers.get("Mcp-Session-Id", _session_id)
        value = json.loads(response.read().decode())
    if not isinstance(value, dict) or "error" in value:
        raise RuntimeError(str(value.get("error", value)))
    return value


def tool(endpoint: str, name: str, arguments: dict) -> dict:
    return call(endpoint, "tools/call", {"name": name, "arguments": arguments})


def tool_names(endpoint: str) -> set[str]:
    result = call(endpoint, "tools/list", {}).get("result", {})
    tools = result.get("tools", []) if isinstance(result, dict) else []
    return {
        item["name"] for item in tools
        if isinstance(item, dict) and isinstance(item.get("name"), str)
    }


def structured(value: dict) -> dict:
    """Accept both FastMCP structuredContent and JSON text content responses."""
    result = value.get("result", {})
    if isinstance(result.get("structuredContent"), dict):
        return result["structuredContent"]
    for item in result.get("content", []):
        if item.get("type") == "text":
            decoded = json.loads(item.get("text", ""))
            if isinstance(decoded, dict):
                return decoded
    raise RuntimeError("MCP tool result has no structured object")


def run_input_probe(endpoint: str, probe: dict) -> tuple[bool, str]:
    for required in ("task_contract", "proposal"):
        if required not in probe:
            raise ValueError(f"input probe lacks {required}")
    proposed = structured(tool(endpoint, "gui_diagnostic", {"operation": "propose", **probe}))
    proposal_id = proposed.get("object_ref", "")
    if not proposal_id:
        raise RuntimeError("input proposal did not return an object_ref")
    executed = structured(tool(endpoint, "gui_diagnostic", {
        "operation": "execute", "task_id": probe["task_contract"]["task_id"],
        "proposal_id": proposal_id,
    }))
    if executed.get("status") != "running":
        raise RuntimeError("input action was not delivered")
    return True, str(probe["proposal"].get("action", {}).get("type", ""))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--endpoint", required=True)
    parser.add_argument(
        "--input-probe",
        type=Path,
        help="Approved JSON with task_contract and proposal",
    )
    args = parser.parse_args()
    result = {"endpoint": args.endpoint, "mcp_reachable": False, "diagnostics_enabled": False,
              "observe": False, "screenshot": False, "pointer": False, "keyboard": False}
    try:
        call(args.endpoint, "initialize", {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "desktop-harness-provisioner", "version": "1"},
        })
        tool(args.endpoint, "gui_run", {"operation": "describe"})
        result["mcp_reachable"] = True
        result["diagnostics_enabled"] = "gui_diagnostic" in tool_names(args.endpoint)
        if result["diagnostics_enabled"]:
            contract = {"task_id": "provision-observe", "goal": "Read the current desktop only.",
                        "limits": {"max_steps": 1, "max_retries": 0}}
            observed = structured(
                tool(
                    args.endpoint,
                    "gui_diagnostic",
                    {"operation": "observe", "task_contract": contract},
                )
            )
            result["observe"] = observed.get("status") == "ok" and bool(observed.get("object"))
            # A successful observe response is the server's supported screenshot/frame evidence probe.
            result["screenshot"] = result["observe"]
        if args.input_probe:
            if not result["diagnostics_enabled"]:
                raise ValueError("--input-probe requires recording.diagnostic=true")
            probes = json.loads(args.input_probe.read_text(encoding="utf-8")).get("probes", [])
            if not isinstance(probes, list) or len(probes) != 2:
                raise ValueError("input probe must contain exactly two approved probes")
            for probe in probes:
                ok, action = run_input_probe(args.endpoint, probe)
                result["pointer"] |= ok and action.startswith("pointer.")
                result["keyboard"] |= ok and action.startswith("keyboard.")
    except (
        OSError,
        ValueError,
        KeyError,
        RuntimeError,
        urllib.error.URLError,
        json.JSONDecodeError,
    ) as exc:
        result["error"] = type(exc).__name__
        print(json.dumps(result, ensure_ascii=False))
        return 1
    print(json.dumps(result, ensure_ascii=False))
    if not result["mcp_reachable"]:
        return 1
    if result["diagnostics_enabled"] and not (result["observe"] and result["screenshot"]):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
