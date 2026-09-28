"""Server JSON configuration for the v2 MCP service."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
from typing import Any

from .desktop_backend import DEFAULT_DESKTOP_BACKEND, available_desktop_backends
from .provider_registry import (
    validate_evidence_provider,
    validate_proposal_provider,
)
from .core.transaction import ActionType


@dataclass(frozen=True)
class ServerConfig:
    path: Path
    transport_mode: str
    transport_host: str
    transport_port: int
    transport_auth_mode: str
    transport_token_env: str | None
    desktop_backend: str
    proposal_provider: dict[str, Any]
    deployment_denied_actions: frozenset[ActionType]
    evidence_providers: dict[str, Any]
    recording: dict[str, Any]

    def effective_config(self) -> dict[str, Any]:
        """Return the active non-secret configuration for logs and discovery."""
        provider = dict(self.proposal_provider)
        provider.pop("api_key", None)
        return {
            "config_path": str(self.path),
            "transport": {
                "mode": self.transport_mode,
                "host": self.transport_host,
                "port": self.transport_port,
                "auth": {"mode": self.transport_auth_mode},
            },
            "desktop_backend": self.desktop_backend,
            "proposal_provider": provider,
            "deployment": {
                "denied_actions": sorted(item.value for item in self.deployment_denied_actions),
            },
            "evidence_providers": self.evidence_providers,
            "recording": self.recording,
        }


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
        {
            "schema_version", "transport", "desktop_backend", "proposal_provider",
            "deployment", "evidence_providers", "recording",
        },
        "MCP config",
    )
    if raw.get("schema_version") != 2:
        raise ValueError("MCP config schema_version must be 2")

    transport = _object(raw, "transport")
    _only_keys(transport, {"mode", "host", "port", "auth"}, "transport")
    mode = _string(transport, "mode")
    if mode not in {"sse", "streamable-http"}:
        raise ValueError("transport.mode must be 'sse' or 'streamable-http'")
    host = _string(transport, "host")
    port = _positive_int(transport, "port")
    auth = _object(transport, "auth", default={"mode": "loopback"})
    _only_keys(auth, {"mode", "token_env"}, "transport.auth")
    auth_mode = _string(auth, "mode")
    if auth_mode not in {"loopback", "trusted-proxy", "bearer-token"}:
        raise ValueError(
            "transport.auth.mode must be 'loopback', 'trusted-proxy', or 'bearer-token'"
        )
    loopback_hosts = {"127.0.0.1", "::1"}
    token_env: str | None = None
    if auth_mode in {"loopback", "trusted-proxy"} and host not in loopback_hosts:
        raise ValueError(f"transport.auth.mode={auth_mode} requires a loopback host")
    if auth_mode == "bearer-token":
        token_env = _string(auth, "token_env")
        if not os.getenv(token_env, "").strip():
            raise ValueError(f"transport bearer token environment variable is empty: {token_env}")
    elif "token_env" in auth:
        raise ValueError("transport.auth.token_env is only valid for bearer-token mode")

    backend = _object(raw, "desktop_backend", default={"kind": DEFAULT_DESKTOP_BACKEND})
    _only_keys(backend, {"kind"}, "desktop_backend")
    backend_id = _string(backend, "kind")
    if backend_id not in available_desktop_backends():
        choices = ", ".join(available_desktop_backends())
        raise ValueError(f"desktop_backend.kind must be one of: {choices}")

    proposal_provider = _object(raw, "proposal_provider")
    validate_proposal_provider(proposal_provider, "proposal_provider")

    deployment = _object(raw, "deployment", default={})
    _only_keys(deployment, {"denied_actions"}, "deployment")
    denied_raw = deployment.get("denied_actions", [])
    if not isinstance(denied_raw, list) or any(not isinstance(item, str) for item in denied_raw):
        raise ValueError("deployment.denied_actions must be an array of action strings")
    try:
        denied = [ActionType(item) for item in denied_raw]
    except ValueError as exc:
        raise ValueError("deployment.denied_actions contains an unknown action") from exc
    if len(denied) != len(set(denied)):
        raise ValueError("deployment.denied_actions must not contain duplicates")
    evidence_providers = _object(raw, "evidence_providers", default={})
    recording = _object(raw, "recording", default={})
    for provider_id, provider_config in evidence_providers.items():
        if not isinstance(provider_config, dict):
            raise ValueError(f"evidence_providers.{provider_id} must be an object")
        validate_evidence_provider(provider_id, provider_config, f"evidence_providers.{provider_id}")
    _only_keys(
        recording,
        {"audit", "diagnostic", "directory", "retention_days", "max_gib"},
        "recording",
    )
    _optional_bool(recording, "audit")
    _optional_bool(recording, "diagnostic")
    _optional_string(recording, "directory")
    _optional_positive_int(recording, "retention_days")
    _optional_positive_int(recording, "max_gib")
    audit_enabled = bool(recording.get("audit", False))
    diagnostic_enabled = bool(recording.get("diagnostic", False))
    if diagnostic_enabled and not audit_enabled:
        raise ValueError("recording.diagnostic=true requires recording.audit=true")
    recording = {
        "audit": audit_enabled,
        "diagnostic": diagnostic_enabled,
        "directory": str(recording.get("directory") or ".autoui-audit"),
        "retention_days": int(recording.get("retention_days") or 7),
        "max_gib": int(recording.get("max_gib") or 16),
    }
    return ServerConfig(
        path=config_path,
        transport_mode=mode,
        transport_host=host,
        transport_port=port,
        transport_auth_mode=auth_mode,
        transport_token_env=token_env,
        desktop_backend=backend_id,
        proposal_provider=proposal_provider,
        deployment_denied_actions=frozenset(denied),
        evidence_providers=evidence_providers,
        recording=recording,
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


def _optional_minimum_int(mapping: dict[str, Any], name: str, minimum: int) -> None:
    if name not in mapping:
        return
    value = mapping[name]
    if not isinstance(value, int) or isinstance(value, bool) or value < minimum:
        raise ValueError(f"{name} must be an integer of at least {minimum}")


def _optional_unit_interval(mapping: dict[str, Any], name: str) -> None:
    if name not in mapping:
        return
    value = mapping[name]
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not 0 <= value <= 1:
        raise ValueError(f"{name} must be a number from 0 to 1")


def _positive_int(mapping: dict[str, Any], name: str) -> int:
    value = mapping.get(name)
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ValueError(f"{name} must be a positive integer")
    return value
