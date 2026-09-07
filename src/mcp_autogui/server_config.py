"""Server JSON configuration for the v2 MCP service."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any

from .desktop_backend import DEFAULT_DESKTOP_BACKEND, available_desktop_backends


@dataclass(frozen=True)
class ServerConfig:
    path: Path
    transport_mode: str
    transport_host: str
    transport_port: int
    desktop_backend: str
    proposal_provider: dict[str, Any]
    evidence_providers: dict[str, Any]
    audit: dict[str, Any]


def load_server_config(path: str | Path) -> ServerConfig:
    config_path = Path(path).expanduser().resolve()
    try:
        raw = json.loads(config_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"MCP config file does not exist: {config_path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"MCP config file is not valid JSON: {config_path}: {exc.msg}") from exc
    if not isinstance(raw, dict):
        raise ValueError("MCP config root must be an object")
    _only_keys(
        raw,
        {"schema_version", "transport", "desktop_backend", "proposal_provider", "evidence_providers", "audit"},
        "MCP config",
    )
    if raw.get("schema_version") != 1:
        raise ValueError("MCP config schema_version must be 1")

    transport = _object(raw, "transport")
    _only_keys(transport, {"mode", "host", "port"}, "transport")
    mode = _string(transport, "mode")
    if mode not in {"sse", "streamable-http"}:
        raise ValueError("transport.mode must be 'sse' or 'streamable-http'")
    host = _string(transport, "host")
    port = _positive_int(transport, "port")

    backend = _object(raw, "desktop_backend", default={"kind": DEFAULT_DESKTOP_BACKEND})
    _only_keys(backend, {"kind"}, "desktop_backend")
    backend_id = _string(backend, "kind")
    if backend_id not in available_desktop_backends():
        choices = ", ".join(available_desktop_backends())
        raise ValueError(f"desktop_backend.kind must be one of: {choices}")

    proposal_provider = _object(raw, "proposal_provider")
    _only_keys(
        proposal_provider,
        {"kind", "mode", "model", "base_url", "timeout_seconds", "tls_verify"},
        "proposal_provider",
    )
    if _string(proposal_provider, "kind") != "qwen-cua":
        raise ValueError("proposal_provider.kind must be 'qwen-cua'")
    if _string(proposal_provider, "mode") not in {"embedded", "http"}:
        raise ValueError("proposal_provider.mode must be 'embedded' or 'http'")
    _optional_string(proposal_provider, "model")
    _optional_string(proposal_provider, "base_url")
    _optional_positive_int(proposal_provider, "timeout_seconds")
    _optional_bool(proposal_provider, "tls_verify")

    evidence_providers = _object(raw, "evidence_providers", default={})
    audit = _object(raw, "audit", default={})
    _only_keys(evidence_providers, {"compositor_window", "omniparser"}, "evidence_providers")
    compositor = _object(evidence_providers, "compositor_window", default={})
    _only_keys(compositor, {"enabled"}, "evidence_providers.compositor_window")
    _optional_bool(compositor, "enabled")
    omni = _object(evidence_providers, "omniparser", default={})
    _only_keys(omni, {"enabled", "endpoint"}, "evidence_providers.omniparser")
    _optional_bool(omni, "enabled")
    _optional_string(omni, "endpoint")
    _only_keys(audit, {"directory", "retention_days", "max_gib"}, "audit")
    _optional_string(audit, "directory")
    _optional_positive_int(audit, "retention_days")
    _optional_positive_int(audit, "max_gib")
    return ServerConfig(
        path=config_path,
        transport_mode=mode,
        transport_host=host,
        transport_port=port,
        desktop_backend=backend_id,
        proposal_provider=proposal_provider,
        evidence_providers=evidence_providers,
        audit=audit,
    )


def _object(mapping: dict[str, Any], name: str, *, default: dict[str, Any] | None = None) -> dict[str, Any]:
    value = mapping.get(name, default)
    if not isinstance(value, dict):
        raise ValueError(f"{name} must be an object")
    return value


def _string(mapping: dict[str, Any], name: str) -> str:
    value = mapping.get(name)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value.strip()


def _only_keys(mapping: dict[str, Any], allowed: set[str], name: str) -> None:
    unknown = sorted(set(mapping) - allowed)
    if unknown:
        raise ValueError(f"{name} has unknown fields: {', '.join(unknown)}")


def _optional_string(mapping: dict[str, Any], name: str) -> None:
    if name in mapping and not isinstance(mapping[name], str):
        raise ValueError(f"{name} must be a string")


def _optional_bool(mapping: dict[str, Any], name: str) -> None:
    if name in mapping and not isinstance(mapping[name], bool):
        raise ValueError(f"{name} must be true or false")


def _optional_positive_int(mapping: dict[str, Any], name: str) -> None:
    if name not in mapping:
        return
    value = mapping[name]
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ValueError(f"{name} must be a positive integer")


def _positive_int(mapping: dict[str, Any], name: str) -> int:
    value = mapping.get(name)
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ValueError(f"{name} must be a positive integer")
    return value
