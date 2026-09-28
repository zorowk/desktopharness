"""Optional audit recording that never owns or drives runtime state."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from .audit_models import (
    Attribution,
    AttributionEventKind,
    AttributionEvidenceStatus,
    AttributionOwner,
    AttributionStage,
    LedgerEvent,
)
from .evidence import AssertionResult
from .ledger import EventLedger
from .protocol import ReasonCode, new_id
from .store import ObjectStore
from .transaction import ActionProposal


_AUDIT_EVENTS = frozenset(
    {
        "task.created",
        "proposal.created",
        "execution.completed",
        "assertion.evaluated",
        "task.transitioned",
        "attribution.recorded",
        "task.reset",
    }
)


class AuditRecorder:
    """Keep live diagnostics in memory and optionally persist selected facts."""

    def __init__(
        self,
        store: ObjectStore | None = None,
        ledger: EventLedger | None = None,
        *,
        audit_store: ObjectStore | None = None,
        audit_ledger: EventLedger | None = None,
        diagnostic_enabled: bool = False,
    ) -> None:
        self.store = store or ObjectStore()
        self.ledger = ledger or EventLedger()
        self.audit_store = audit_store
        self.audit_ledger = audit_ledger
        self.diagnostic_enabled = diagnostic_enabled
        self._object_events: dict[str, str] = {}
        self._primary_attribution: dict[str, str] = {}
        self._attribution_keys: set[tuple[str, str, ReasonCode]] = set()
        self._task_refs: dict[str, set[str]] = {}
        self._recording_errors: list[str] = []
        self._persisted_event_ids: set[str] = set()

    @property
    def audit_enabled(self) -> bool:
        return self.audit_store is not None and self.audit_ledger is not None

    @property
    def recording_errors(self) -> tuple[str, ...]:
        return tuple(self._recording_errors)

    def remember(self, task_id: str, *references: str | None) -> None:
        selected = self._task_refs.setdefault(task_id, set())
        selected.update(reference for reference in references if reference)

    def put(
        self,
        task_id: str,
        value: Any,
        *,
        prefix: str = "object",
        object_ref: str | None = None,
    ) -> str:
        reference = self.store.put(value, prefix=prefix, object_ref=object_ref)
        self.remember(task_id, reference)
        return reference

    def put_diagnostic(
        self,
        value: Any,
        *,
        task_id: str | None = None,
        prefix: str = "object",
    ) -> str:
        reference = self.store.put(value, prefix=prefix)
        if task_id is not None:
            self.remember(task_id, reference)
        if self.audit_enabled and self.diagnostic_enabled:
            try:
                self.audit_store.put(value, object_ref=reference)
            except Exception as exc:
                self._recording_errors.append(f"{type(exc).__name__}: {exc}")
        return reference

    def append(self, task_id: str, event_type: str, object_ref: str, **kwargs: Any):
        event = self.ledger.append(task_id, event_type, object_ref, **kwargs)
        self._object_events[object_ref] = event.event_id
        self.remember(task_id, object_ref, *event.artifact_refs, event.debug_ref)
        if self.audit_enabled and (
            event_type in _AUDIT_EVENTS or self.diagnostic_enabled
        ):
            self._persist(event)
        return event

    def _persist(self, event: LedgerEvent) -> None:
        try:
            value = self.store.get(event.object_ref)
            if value is not None:
                self.audit_store.put(
                    self._audit_projection(value), object_ref=event.object_ref
                )
            artifact_refs = event.artifact_refs if self.diagnostic_enabled else ()
            debug_ref = event.debug_ref if self.diagnostic_enabled else None
            if self.diagnostic_enabled:
                for reference in (*artifact_refs, debug_ref):
                    if not reference:
                        continue
                    artifact = self.store.get(reference)
                    if artifact is not None:
                        self.audit_store.put(artifact, object_ref=reference)
            self.audit_ledger.record(
                replace(
                    event,
                    caused_by=tuple(
                        reference
                        for reference in event.caused_by
                        if reference in self._persisted_event_ids
                    ),
                    artifact_refs=artifact_refs,
                    debug_ref=debug_ref,
                )
            )
            self._persisted_event_ids.add(event.event_id)
        except Exception as exc:  # recording cannot invalidate a delivered action
            self._recording_errors.append(f"{type(exc).__name__}: {exc}")

    def _audit_projection(self, value: Any) -> Any:
        if self.diagnostic_enabled:
            return value
        if isinstance(value, ActionProposal):
            return replace(value, debug_ref=None)
        if isinstance(value, AssertionResult):
            return replace(value, evidence_refs=(), excluded_evidence=())
        if isinstance(value, Attribution):
            return replace(value, evidence_refs=())
        return value

    def causes_for(self, object_ref: str) -> tuple[str, ...]:
        event_id = self._object_events.get(object_ref)
        return (event_id,) if event_id else ()

    def latest_execution_causes(self, task_id: str) -> tuple[str, ...]:
        selected: list[str] = []
        for event in reversed(self.ledger.events(task_id)):
            if event.event_type in {"execution.completed", "snapshot.created"}:
                if event.event_type not in {item.split(":", 1)[0] for item in selected}:
                    selected.append(f"{event.event_type}:{event.event_id}")
                if len(selected) == 2:
                    break
        return tuple(item.split(":", 1)[1] for item in reversed(selected))

    def record_attribution(
        self,
        task_id: str,
        event_kind: AttributionEventKind,
        stage: AttributionStage | str,
        owner: AttributionOwner | str,
        code: ReasonCode,
        summary: str,
        *,
        evidence_refs: tuple[str, ...] = (),
        evidence_status: AttributionEvidenceStatus = AttributionEvidenceStatus.CONFIRMED,
    ) -> Attribution:
        stage = AttributionStage(stage)
        owner = AttributionOwner(owner)
        key = (task_id, event_kind.value, code)
        if key in self._attribution_keys:
            return next(
                item
                for item in reversed(self.attributions(task_id))
                if item.event_kind == event_kind and item.code == code
            )
        is_primary = (
            task_id not in self._primary_attribution
            and event_kind in {AttributionEventKind.ERROR, AttributionEventKind.INCOMPLETE}
            and evidence_status != AttributionEvidenceStatus.INSUFFICIENT
        )
        attribution = Attribution(
            attribution_id=new_id("attribution"),
            event_kind=event_kind,
            stage=stage,
            owner=owner,
            code=code,
            evidence_status=evidence_status,
            primary=is_primary,
            summary=summary,
            evidence_refs=evidence_refs,
        )
        self.put(task_id, attribution, object_ref=attribution.attribution_id)
        self._attribution_keys.add(key)
        if is_primary:
            self._primary_attribution[task_id] = attribution.attribution_id
        self.append(
            task_id,
            "attribution.recorded",
            attribution.attribution_id,
            caused_by=tuple(
                self._object_events[reference]
                for reference in evidence_refs
                if reference in self._object_events
            ),
        )
        return attribution

    def attributions(self, task_id: str) -> tuple[Attribution, ...]:
        return tuple(
            self.store.require(event.object_ref)
            for event in self.ledger.events(task_id)
            if event.event_type == "attribution.recorded"
        )

    def primary_attribution(self, task_id: str) -> Attribution | None:
        reference = self._primary_attribution.get(task_id)
        return self.store.require(reference) if reference else None

    def primary_attribution_ref(self, task_id: str) -> str | None:
        return self._primary_attribution.get(task_id)

    def clear_task(self, task_id: str) -> None:
        self._primary_attribution.pop(task_id, None)
        self._attribution_keys = {
            item for item in self._attribution_keys if item[0] != task_id
        }
        references = self._task_refs.pop(task_id, set())
        self._object_events = {
            reference: event_id
            for reference, event_id in self._object_events.items()
            if reference not in references
        }
        for reference in references:
            self.store.discard(reference)
        self.ledger.clear(task_id)
