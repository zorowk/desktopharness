"""Append-only event ledger containing references, never large payloads."""

from __future__ import annotations

from collections import defaultdict
from threading import RLock

from .models import LedgerEvent, new_id, utc_now


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

