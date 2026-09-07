"""Optional lightweight persistence wiring for audit-only v2 data."""

from __future__ import annotations

import os
from typing import Mapping

from .ledger import CsvAuditEventLedger, EventLedger
from .store import JsonAuditObjectStore, ObjectStore


def audit_components_from_environment() -> tuple[ObjectStore, EventLedger]:
    directory = os.environ.get("GUI_AUDIT_DIR", "").strip()
    return _audit_components(
        directory,
        retention_days=_positive_int("GUI_AUDIT_RETENTION_DAYS", 7),
        max_gib=_positive_int("GUI_AUDIT_MAX_GIB", 16),
    )


def audit_components_from_config(config: Mapping[str, object] | None) -> tuple[ObjectStore, EventLedger]:
    """Create audit persistence from v2 config without process-wide state."""
    if config is None:
        return audit_components_from_environment()
    directory = str(config.get("directory") or "").strip()
    return _audit_components(
        directory,
        retention_days=int(config.get("retention_days") or 7),
        max_gib=int(config.get("max_gib") or 16),
    )


def _audit_components(
    directory: str,
    *,
    retention_days: int,
    max_gib: int,
) -> tuple[ObjectStore, EventLedger]:
    if not directory:
        return ObjectStore(), EventLedger()
    return (
        JsonAuditObjectStore(
            directory,
            retention_days=retention_days,
            max_total_bytes=max_gib * 1024 * 1024 * 1024,
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
