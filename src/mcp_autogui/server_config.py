"""Server JSON configuration and compatibility wiring for legacy settings."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
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
    if raw.get("schema_version") != 1:
        raise ValueError("MCP config schema_version must be 1")

    transport = _object(raw, "transport")
    mode = _string(transport, "mode")
    if mode not in {"sse", "streamable-http"}:
        raise ValueError("transport.mode must be 'sse' or 'streamable-http'")
    host = _string(transport, "host")
    port = _positive_int(transport, "port")

    backend = _object(raw, "desktop_backend", default={"kind": DEFAULT_DESKTOP_BACKEND})
    backend_id = _string(backend, "kind")
    if backend_id not in available_desktop_backends():
        choices = ", ".join(available_desktop_backends())
        raise ValueError(f"desktop_backend.kind must be one of: {choices}")

    proposal_provider = _object(raw, "proposal_provider")
    if _string(proposal_provider, "kind") != "qwen-cua":
        raise ValueError("proposal_provider.kind must be 'qwen-cua'")
    if _string(proposal_provider, "mode") not in {"embedded", "http"}:
        raise ValueError("proposal_provider.mode must be 'embedded' or 'http'")

    evidence_providers = _object(raw, "evidence_providers", default={})
    audit = _object(raw, "audit", default={})
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


def apply_server_config(config: ServerConfig) -> None:
    """Apply JSON values before legacy components are constructed.

    This is an internal transition bridge: callers configure JSON, while older
    components continue to read the process settings until their constructors
    accept typed configuration directly.
    """
    _set("MCP_TRANSPORT", config.transport_mode)
    _set("SSE_HOST", config.transport_host)
    _set("SSE_PORT", config.transport_port)

    proposal = config.proposal_provider
    _set("CUA_BACKEND_MODE", proposal["mode"])
    _set_optional("CUA_MODEL", proposal.get("model"))
    _set_optional("CUA_MODEL_BASE_URL", proposal.get("base_url"))
    _set_optional("CUA_MODEL_TIMEOUT", proposal.get("timeout_seconds"))
    _set_optional("CUA_MODEL_TLS_VERIFY", _bool_string(proposal.get("tls_verify")))

    omni = _object(config.evidence_providers, "omniparser", default={})
    _set("GUI_OMNIPARSER_ENABLED", _bool_string(omni.get("enabled", False)))
    _set_optional("OMNI_PARSER_SERVER", omni.get("endpoint"))

    _set_optional("GUI_AUDIT_DIR", config.audit.get("directory"))
    _set_optional("GUI_AUDIT_RETENTION_DAYS", config.audit.get("retention_days"))
    _set_optional("GUI_AUDIT_MAX_GIB", config.audit.get("max_gib"))


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


def _positive_int(mapping: dict[str, Any], name: str) -> int:
    value = mapping.get(name)
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ValueError(f"{name} must be a positive integer")
    return value


def _bool_string(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, bool):
        raise ValueError("configured boolean value must be true or false")
    return "1" if value else "0"


def _set(name: str, value: str | int) -> None:
    os.environ[name] = str(value)


def _set_optional(name: str, value: Any) -> None:
    if value is None or value == "":
        os.environ.pop(name, None)
        return
    _set(name, value)
