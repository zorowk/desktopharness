"""Read-only preflight checks for a configured AutoUI MCP environment."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, Request, build_opener

from .server_config import load_server_config


_DIRECT_OPENER = build_opener(ProxyHandler({}))


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Run read-only AutoUI MCP preflight checks")
    parser.add_argument("--config", required=True, help="path to the server JSON configuration")
    parser.add_argument(
        "--mcp-url",
        help="MCP streamable-HTTP endpoint; defaults to http://<host>:<port>/mcp",
    )
    args = parser.parse_args(argv)
    config = load_server_config(args.config)
    mcp_url = args.mcp_url or _default_mcp_url(config.effective_config()["transport"])
    result = {
        "effective_config": config.effective_config(),
        "treeland_debug": _treeland_tree_check(),
            "proposal_provider": {
                "kind": config.proposal_provider["kind"],
                "configured": bool(config.proposal_provider.get("base_url")),
        },
        "mcp_describe": _mcp_describe_check(mcp_url),
    }
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    if not result["treeland_debug"]["ok"] or not result["mcp_describe"]["ok"]:
        raise SystemExit(1)


def _treeland_tree_check() -> dict[str, object]:
    command = shutil.which("treeland-debug")
    if command is None:
        return {"ok": False, "reason": "treeland-debug is unavailable"}
    completed = subprocess.run(
        [command, "--json", "tree"], capture_output=True, text=True, timeout=10, check=False
    )
    try:
        tree = json.loads(completed.stdout) if completed.returncode == 0 else None
    except json.JSONDecodeError:
        tree = None
    return {"ok": isinstance(tree, dict), "returncode": completed.returncode}


def _default_mcp_url(transport: dict[str, object]) -> str:
    host = str(transport["host"])
    if host in {"0.0.0.0", "::"}:
        host = "127.0.0.1"
    return f"http://{host}:{transport['port']}/mcp"


def _mcp_describe_check(endpoint: str) -> dict[str, object]:
    initialization = {
        "jsonrpc": "2.0",
        "id": "autoui-smoke-initialize",
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "autoui-smoke", "version": "2"},
        },
    }
    try:
        _, session_id = _mcp_request(endpoint, initialization)
        response_payload, _ = _mcp_request(
            endpoint,
            {
                "jsonrpc": "2.0",
                "id": "autoui-smoke-describe",
                "method": "tools/call",
                "params": {"name": "gui_run", "arguments": {"operation": "describe"}},
            },
            session_id=session_id,
        )
    except (HTTPError, URLError, TimeoutError, ValueError, json.JSONDecodeError) as exc:
        return {"ok": False, "endpoint": endpoint, "reason": f"{type(exc).__name__}: {exc}"}
    if not isinstance(response_payload, dict):
        return {"ok": False, "endpoint": endpoint, "reason": "MCP response is not an object"}
    if "error" in response_payload:
        error = response_payload["error"]
        return {"ok": False, "endpoint": endpoint, "reason": str(error)}
    result = response_payload.get("result")
    if not isinstance(result, dict) or result.get("isError") is True:
        return {"ok": False, "endpoint": endpoint, "reason": "gui_run(describe) was rejected"}
    return {"ok": True, "endpoint": endpoint}


def _mcp_request(
    endpoint: str, payload: dict[str, object], *, session_id: str | None = None
) -> tuple[dict[str, object], str | None]:
    headers = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
    if session_id:
        headers["Mcp-Session-Id"] = session_id
    request = Request(
        endpoint,
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    with _DIRECT_OPENER.open(request, timeout=10) as response:  # noqa: S310 -- endpoint is explicit config
        value = _decode_mcp_response(response.read().decode("utf-8"))
        received_session = response.headers.get("Mcp-Session-Id")
    if not isinstance(value, dict):
        raise ValueError("MCP response is not an object")
    return value, received_session if isinstance(received_session, str) else None


def _decode_mcp_response(payload: str) -> dict[str, object]:
    """Decode either a JSON response or FastMCP's single-message SSE response."""
    stripped = payload.strip()
    if stripped.startswith("data:") or "\ndata:" in stripped:
        data = "\n".join(
            line[5:].lstrip() for line in stripped.splitlines() if line.startswith("data:")
        )
        return json.loads(data)
    return json.loads(stripped)
