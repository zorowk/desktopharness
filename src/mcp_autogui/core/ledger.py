"""Append-only event ledgers containing references, never large payloads."""

from __future__ import annotations

from collections import defaultdict
import csv
import json
import os
from pathlib import Path
from threading import RLock

from .models import LedgerEvent, new_id, utc_now
from .store import _private_directory


class EventLedger:
    def __init__(self) -> None:
        self._events: dict[str, list[LedgerEvent]] = defaultdict(list)
        self._lock = RLock()

    def append(
        self,
        task_id: str,
        event_type: str,
        epistemic_type: str,
        object_ref: str,
        *,
        caused_by: tuple[str, ...] = (),
        snapshot_id: str | None = None,
        artifact_refs: tuple[str, ...] = (),
        debug_ref: str | None = None,
    ) -> LedgerEvent:
        with self._lock:
            event = LedgerEvent(
                event_id=new_id("event"),
                task_id=task_id,
                sequence=len(self._events[task_id]) + 1,
                occurred_at=utc_now(),
                event_type=event_type,
                epistemic_type=epistemic_type,
                object_ref=object_ref,
                caused_by=caused_by,
                snapshot_id=snapshot_id,
                artifact_refs=artifact_refs,
                debug_ref=debug_ref,
            )
            self._events[task_id].append(event)
            return event

    def events(self, task_id: str) -> tuple[LedgerEvent, ...]:
        with self._lock:
            return tuple(self._events.get(task_id, ()))

    def clear(self, task_id: str) -> None:
        with self._lock:
            self._events.pop(task_id, None)


class CsvAuditEventLedger(EventLedger):
    """A private, append-oriented CSV projection of the v2 event ledger."""

    FIELDS = (
        "event_id", "task_id", "sequence", "occurred_at", "event_type", "epistemic_type",
        "object_ref", "caused_by", "snapshot_id", "artifact_refs", "debug_ref", "schema_version",
    )

    def __init__(self, directory: str | Path) -> None:
        super().__init__()
        root = _private_directory(directory)
        self.path = root / "ledger.csv"
        self._load()

    def append(self, *args, **kwargs) -> LedgerEvent:
        event = super().append(*args, **kwargs)
        self._append_csv(event)
        return event

    def clear(self, task_id: str) -> None:
        super().clear(task_id)
        self._rewrite()

    def _load(self) -> None:
        if not self.path.exists():
            return
        with self.path.open("r", encoding="utf-8", newline="") as handle:
            for row in csv.DictReader(handle):
                event = LedgerEvent(
                    event_id=row["event_id"], task_id=row["task_id"], sequence=int(row["sequence"]),
                    occurred_at=row["occurred_at"], event_type=row["event_type"],
                    epistemic_type=row["epistemic_type"], object_ref=row["object_ref"],
                    caused_by=tuple(json.loads(row["caused_by"])), snapshot_id=row["snapshot_id"] or None,
                    artifact_refs=tuple(json.loads(row["artifact_refs"])), debug_ref=row["debug_ref"] or None,
                    schema_version=row["schema_version"],
                )
                self._events[event.task_id].append(event)

    def _append_csv(self, event: LedgerEvent) -> None:
        new_file = not self.path.exists()
        with self.path.open("a", encoding="utf-8", newline="") as handle:
            os.chmod(self.path, 0o600)
            writer = csv.DictWriter(handle, fieldnames=self.FIELDS)
            if new_file:
                writer.writeheader()
            writer.writerow(self._row(event))
            handle.flush()
            os.fsync(handle.fileno())

    def _rewrite(self) -> None:
        temporary = self.path.with_suffix(".csv.tmp")
        with temporary.open("w", encoding="utf-8", newline="") as handle:
            os.chmod(temporary, 0o600)
            writer = csv.DictWriter(handle, fieldnames=self.FIELDS)
            writer.writeheader()
            for events in self._events.values():
                writer.writerows(self._row(event) for event in events)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, self.path)

    @staticmethod
    def _row(event: LedgerEvent) -> dict[str, str | int]:
        return {
            "event_id": event.event_id, "task_id": event.task_id, "sequence": event.sequence,
            "occurred_at": event.occurred_at, "event_type": event.event_type,
            "epistemic_type": event.epistemic_type, "object_ref": event.object_ref,
            "caused_by": json.dumps(event.caused_by), "snapshot_id": event.snapshot_id or "",
            "artifact_refs": json.dumps(event.artifact_refs), "debug_ref": event.debug_ref or "",
            "schema_version": event.schema_version,
        }
