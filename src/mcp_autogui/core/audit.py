"""Optional lightweight persistence wiring for audit-only v2 data."""

from __future__ import annotations

import os

from .ledger import CsvAuditEventLedger, EventLedger
from .store import JsonAuditObjectStore, ObjectStore


def audit_components_from_environment() -> tuple[ObjectStore, EventLedger]:
    directory = os.environ.get("GUI_AUDIT_DIR", "").strip()
    if not directory:
        return ObjectStore(), EventLedger()
    return (
        JsonAuditObjectStore(
            directory,
            retention_days=_positive_int("GUI_AUDIT_RETENTION_DAYS", 7),
            max_total_bytes=_positive_int("GUI_AUDIT_MAX_GIB", 16) * 1024 * 1024 * 1024,
        ),
        CsvAuditEventLedger(directory),
    )


def _positive_int(name: str, default: int) -> int:
    value = os.environ.get(name)
    if value is None or not value.strip():
        return default
    try:
        parsed = int(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be a positive integer") from exc
    if parsed < 1:
        raise ValueError(f"{name} must be a positive integer")
    return parsed
