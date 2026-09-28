"""Optional persistence wiring for audit and diagnostic recording."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from .ledger import CsvAuditEventLedger, EventLedger
from .store import JsonAuditObjectStore, ObjectStore


@dataclass(frozen=True, slots=True)
class RecordingComponents:
    runtime_store: ObjectStore
    runtime_ledger: EventLedger
    audit_store: JsonAuditObjectStore | None
    audit_ledger: CsvAuditEventLedger | None
    audit_enabled: bool
    diagnostic_enabled: bool


def recording_components_from_config(
    config: Mapping[str, object] | None,
) -> RecordingComponents:
    """Create isolated runtime storage and optional persistent recording."""
    settings = config or {}
    audit_enabled = bool(settings.get("audit", False))
    diagnostic_enabled = bool(settings.get("diagnostic", False))
    if diagnostic_enabled and not audit_enabled:
        raise ValueError("recording.diagnostic=true requires recording.audit=true")
    runtime_store = ObjectStore()
    runtime_ledger = EventLedger()
    if not audit_enabled:
        return RecordingComponents(
            runtime_store, runtime_ledger, None, None, False, False
        )
    directory = str(settings.get("directory") or ".autoui-audit").strip()
    audit_store, audit_ledger = _audit_components(
        directory,
        retention_days=int(settings.get("retention_days") or 7),
        max_gib=int(settings.get("max_gib") or 16),
    )
    return RecordingComponents(
        runtime_store,
        runtime_ledger,
        audit_store,
        audit_ledger,
        True,
        diagnostic_enabled,
    )


def _audit_components(
    directory: str,
    *,
    retention_days: int,
    max_gib: int,
) -> tuple[JsonAuditObjectStore, CsvAuditEventLedger]:
    return (
        JsonAuditObjectStore(
            directory,
            retention_days=retention_days,
            max_total_bytes=max_gib * 1024 * 1024 * 1024,
        ),
        CsvAuditEventLedger(directory),
    )
