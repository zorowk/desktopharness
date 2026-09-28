"""Immutable description of components selected by the composition root."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
import json
from typing import Any

from .core.protocol import to_primitive
from .ports.compositor import CompositorPort
from .ports.evidence import EvidenceProvider
from .ports.executor import ActionExecutor
from .ports.frame import FrameProvider
from .ports.proposal import ProposalProvider


@dataclass(frozen=True, slots=True)
class RuntimeDescription:
    """Read-only snapshot used only by the protocol describe operation."""

    _serialized: str

    @classmethod
    def from_components(
        cls,
        *,
        compositor: CompositorPort,
        executor: ActionExecutor | None,
        proposal_provider: ProposalProvider | None,
        frame_provider: FrameProvider | None,
        evidence_providers: Sequence[EvidenceProvider],
        denied_actions: Iterable[object],
        context_strategies: Iterable[str],
        effective_config: Mapping[str, Any] | None = None,
        recording: Mapping[str, Any] | None = None,
        transport: Mapping[str, Any] | None = None,
    ) -> RuntimeDescription:
        descriptor = compositor.descriptor
        description = {
            "protocol_version": 2,
            "schema_version": "2",
            "schema_revision": "2.2",
            "adapter": to_primitive(descriptor),
            "capabilities": {
                "pointer": executor is not None,
                "keyboard": executor is not None,
                "window_geometry": descriptor.capabilities.desktop_geometry,
                "frame": frame_provider is not None,
                "child_control_semantics": descriptor.capabilities.child_controls,
            },
            "providers": {
                "proposal": _component_id(proposal_provider, "provider_id"),
                "frame": _component_id(frame_provider, "provider_id"),
                "evidence": [
                    {
                        "provider_id": provider.provider_id,
                        "fact_paths": sorted(provider.fact_paths),
                    }
                    for provider in evidence_providers
                ],
                "executor": _component_id(executor, "executor_id"),
            },
            "context_strategies": sorted(context_strategies),
            "deployment": {
                "denied_actions": sorted(str(getattr(item, "value", item)) for item in denied_actions),
            },
            "transport": _public_transport(transport),
            "recording": to_primitive(
                recording or {"audit": False, "diagnostic": False}
            ),
            "proposal_model": {
                "actions": "ordered-sequence",
                "validation_scope": "proposal",
                "observation_boundary": "after-proposal",
                "atomic_receipts": True,
            },
        }
        if effective_config is not None:
            description["effective_config"] = to_primitive(effective_config)
        return cls(json.dumps(description, ensure_ascii=False, sort_keys=True))

    def to_dict(self) -> dict[str, Any]:
        return json.loads(self._serialized)


def _component_id(component: object | None, attribute: str) -> str | None:
    if component is None:
        return None
    return str(getattr(component, attribute, type(component).__name__))


def _public_transport(transport: Mapping[str, Any] | None) -> dict[str, Any]:
    """Expose only connection mode data; tokens and unrelated settings stay private."""
    source = transport or {}
    auth = source.get("auth") if isinstance(source.get("auth"), Mapping) else {}
    result = {"mode": str(source.get("mode") or "unconfigured"), "auth": {
        "mode": str(auth.get("mode") or "unconfigured"),
    }}
    if "host" in source:
        result["host"] = str(source["host"])
    if "port" in source:
        result["port"] = source["port"]
    return result
