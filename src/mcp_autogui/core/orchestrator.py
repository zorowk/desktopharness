"""Thin coordinator for the v2 single-action transaction."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from threading import RLock
from typing import Any, Mapping

from ..ports.application_launcher import ApplicationLauncher
from ..ports.compositor import CompositorAdapter
from ..ports.evidence import EvidenceProvider
from ..ports.executor import InputExecutor
from ..ports.frame import FrameProvider
from ..ports.policy import PolicyProvider
from ..ports.proposal import ProposalProvider
from .action_gate import ActionGate
from .assertion_evaluator import AssertionEvaluator, SUPPORTED_OPERATORS
from .context_builder import ContextBuilder
from .facts import require_standard_fact_path
from .ledger import EventLedger
from .models import (
    ActionProposal,
    ActionType,
    AssertionResult,
    AssertionStatus,
    Attribution,
    AttributionEventKind,
    AttributionEvidenceStatus,
    CanonicalSnapshot,
    EvidenceRecord,
    ExecutionReceipt,
    ExecutionStatus,
    PolicyDecision,
    PolicyStatus,
    ProposalGuard,
    SemanticTag,
    TaskContract,
    TaskState,
    TaskStatus,
    new_id,
    utc_now,
)
from .store import ObjectStore
from .task_state import TaskStateReducer


class CoreOrchestrator:
    """Coordinates ports without allowing adapters to call one another."""

    def __init__(
        self,
        compositor: CompositorAdapter,
        executor: InputExecutor,
        *,
        proposal_provider: ProposalProvider | None = None,
        frame_provider: FrameProvider | None = None,
        application_launcher: ApplicationLauncher | None = None,
        evidence_providers: Sequence[EvidenceProvider] = (),
        policy_providers: Sequence[PolicyProvider] = (),
        policy_profiles: Mapping[str, Mapping[str, str]] | None = None,
        store: ObjectStore | None = None,
        ledger: EventLedger | None = None,
    ) -> None:
        self.compositor = compositor
        self.executor = executor
        self.proposal_provider = proposal_provider
        self.frame_provider = frame_provider
        self.application_launcher = application_launcher
        self.evidence_providers = tuple(evidence_providers)
        self.policy_providers = tuple(policy_providers)
        provider_ids = [provider.provider_id for provider in self.evidence_providers]
        if len(provider_ids) != len(set(provider_ids)):
            raise ValueError("evidence provider IDs must be unique")
        for provider in self.evidence_providers:
            for path in provider.fact_paths:
                require_standard_fact_path(path)
        self.store = store or ObjectStore()
        self.ledger = ledger or EventLedger()
        self.gate = ActionGate(
            compositor.descriptor, compositor.hit_test, policy_profiles=policy_profiles
        )
        self.evaluator = AssertionEvaluator()
        self.reducer = TaskStateReducer()
        self.context_builder = ContextBuilder()
        self._contracts: dict[str, TaskContract] = {}
        self._states: dict[str, TaskState] = {}
        self._latest_snapshot: dict[str, CanonicalSnapshot] = {}
        self._latest_frame: dict[str, Any] = {}
        self._proposal_task: dict[str, str] = {}
        self._provider_proposals: set[str] = set()
        self._decisions: dict[str, PolicyDecision] = {}
        self._decision_refs: dict[str, str] = {}
        self._guards: dict[str, ProposalGuard] = {}
        self._latest_receipt: dict[str, ExecutionReceipt] = {}
        self._terminal_receipts: dict[str, ExecutionReceipt] = {}
        self._latest_results: dict[str, tuple[AssertionResult, ...]] = {}
        self._verified_facts: dict[str, tuple[dict[str, Any], ...]] = {}
        self._object_events: dict[str, str] = {}
        self._execution_lock = RLock()

    def register_task(self, contract: TaskContract) -> TaskState:
        if not contract.task_id or not contract.goal:
            raise ValueError("task_id and goal must not be empty")
        if contract.policy_profile not in self.gate.policy_profiles:
            raise ValueError(f"unknown policy profile: {contract.policy_profile}")
        if any(value not in {"allow", "confirm", "deny"} for value in contract.policy_overrides.values()):
            raise ValueError("policy overrides must be allow, confirm, or deny")
        assertion_ids = [assertion.assertion_id for assertion in contract.assertions]
        if len(assertion_ids) != len(set(assertion_ids)) or any(not item for item in assertion_ids):
            raise ValueError("assertion IDs must be non-empty and unique within a task")
        for assertion in contract.assertions:
            require_standard_fact_path(assertion.path)
            if assertion.operator not in SUPPORTED_OPERATORS:
                raise ValueError(f"unsupported assertion operator: {assertion.operator}")
        if contract.task_id in self._contracts:
            raise ValueError(f"task already exists: {contract.task_id}")
        self._contracts[contract.task_id] = contract
        self._states[contract.task_id] = TaskState(task_id=contract.task_id)
        contract_ref = self.store.put(contract, prefix="task-contract")
        self._append_event(contract.task_id, "task.created", "controller_contract", contract_ref)
        return self._states[contract.task_id]

    def observe(self, task_id: str) -> CanonicalSnapshot:
        self._require_task(task_id)
        snapshot = self.compositor.observe()
        self._latest_snapshot[task_id] = snapshot
        self.store.put(snapshot, object_ref=snapshot.snapshot_id)
        self._append_event(
            task_id,
            "snapshot.created",
            "verified_fact",
            snapshot.snapshot_id,
            snapshot_id=snapshot.snapshot_id,
            artifact_refs=(snapshot.raw_artifact_ref,) if snapshot.raw_artifact_ref else (),
        )
        return snapshot

    def propose(self, task_id: str, *, strategy: str = "compact") -> ActionProposal:
        if self.proposal_provider is None:
            raise RuntimeError("proposal provider is unavailable")
        contract = self._require_task(task_id)
        state = self._states[task_id]
        snapshot = self._latest_snapshot.get(task_id) or self.observe(task_id)
        frame = self.frame_provider.capture_frame() if self.frame_provider is not None else None
        if frame is not None:
            self._latest_frame[task_id] = frame
            self.store.put(frame, object_ref=frame.frame_id)
        context = self.context_builder.build(
            contract,
            state,
            self.ledger.events(task_id),
            based_on_snapshot=snapshot.snapshot_id,
            frame=frame,
            recent_receipt=self._latest_receipt.get(task_id),
            assertion_results=self._latest_results.get(task_id, ()),
            verified_facts=tuple(
                fact
                for fact in self._verified_facts.get(task_id, ())
                if not fact.get("expires_on_environment_change")
                or fact.get("environment_version") == snapshot.environment_version
            ),
            spatial_projection={
                "snapshot_id": snapshot.snapshot_id,
                "coordinate_space": {
                    "id": snapshot.coordinate_space.id,
                    "bounds": {
                        "x": snapshot.coordinate_space.bounds.x,
                        "y": snapshot.coordinate_space.bounds.y,
                        "width": snapshot.coordinate_space.bounds.width,
                        "height": snapshot.coordinate_space.bounds.height,
                    },
                },
                "active_window": (
                    {
                        "window_id": snapshot.active_window().window_id,
                        "app_id": snapshot.active_window().app_id,
                        "title": snapshot.active_window().title,
                    }
                    if snapshot.active_window() is not None
                    else None
                ),
            },
            strategy=strategy,
        )
        self.store.put(context, object_ref=context.model_context_id)
        proposal = self.proposal_provider.propose(context)
        if proposal.based_on_snapshot != snapshot.snapshot_id:
            raise ValueError("proposal must reference the current snapshot")
        self._provider_proposals.add(proposal.proposal_id)
        self.submit_proposal(task_id, proposal)
        return proposal

    def submit_proposal(
        self, task_id: str, proposal: ActionProposal, *, caused_by: tuple[str, ...] = ()
    ) -> ActionProposal:
        self._require_task(task_id)
        if proposal.proposal_id in self._proposal_task:
            raise ValueError("proposal already submitted")
        self._proposal_task[proposal.proposal_id] = task_id
        self.store.put(proposal, object_ref=proposal.proposal_id)
        causal_events = caused_by or self._causes_for(proposal.based_on_snapshot)
        if proposal.debug_ref:
            diagnostic = self._append_event(
                task_id,
                "model_diagnostic.recorded",
                "model_claim",
                proposal.debug_ref,
                caused_by=causal_events,
                snapshot_id=proposal.based_on_snapshot,
                debug_ref=proposal.debug_ref,
            )
            causal_events = (*causal_events, diagnostic.event_id)
        self._append_event(
            task_id,
            "proposal.created",
            "action_proposal",
            proposal.proposal_id,
            caused_by=causal_events,
            snapshot_id=proposal.based_on_snapshot,
            debug_ref=proposal.debug_ref,
        )
        return proposal

    def decide(self, proposal_id: str) -> PolicyDecision:
        task_id = self._proposal_task.get(proposal_id)
        if task_id is None:
            raise KeyError(proposal_id)
        proposal = self.store.require(proposal_id)
        tags: list[SemanticTag] = []
        for provider in self.policy_providers:
            tags.extend(provider.independent_tags(proposal, self._contracts[task_id]))
        snapshot = self.store.get(proposal.based_on_snapshot)
        if not isinstance(snapshot, CanonicalSnapshot):
            resolution = self.gate.resolve_semantics(proposal, tags)
            decision = PolicyDecision(
                proposal_id=proposal.proposal_id,
                status=PolicyStatus.INVALID,
                reason_code="SNAPSHOT_UNAVAILABLE",
                semantic_resolution_ref=resolution.semantic_resolution_id,
            )
            guard = None
        else:
            decision, guard, resolution = self.gate.decide(
                proposal, self._contracts[task_id], snapshot, tags
            )
        self.store.put(resolution, object_ref=resolution.semantic_resolution_id)
        if guard is not None:
            self._guards[guard.guard_id] = guard
            self.store.put(guard, object_ref=guard.guard_id)
        decision_ref = self.store.put(decision, prefix="policy-decision")
        self._decisions[proposal_id] = decision
        self._decision_refs[proposal_id] = decision_ref
        self._append_event(
            task_id,
            "decision.created",
            "policy_decision",
            decision_ref,
            caused_by=self._causes_for(proposal_id),
            snapshot_id=snapshot.snapshot_id if isinstance(snapshot, CanonicalSnapshot) else proposal.based_on_snapshot,
            debug_ref=decision.debug_ref,
        )
        return decision

    def execute(self, proposal_id: str, *, confirmed: bool = False) -> ExecutionReceipt:
        # Observation, guard recheck, and input injection are one critical
        # section across tasks; otherwise another task could alter the desktop
        # between validation and the side effect.
        with self._execution_lock:
            return self._execute(proposal_id, confirmed=confirmed)

    def _execute(self, proposal_id: str, *, confirmed: bool = False) -> ExecutionReceipt:
        task_id = self._proposal_task.get(proposal_id)
        if task_id is None:
            raise KeyError(proposal_id)
        existing_receipt = self._terminal_receipts.get(proposal_id)
        if existing_receipt is not None:
            return existing_receipt
        proposal: ActionProposal = self.store.require(proposal_id)
        decision = self._decisions.get(proposal_id) or self.decide(proposal_id)
        if decision.status == PolicyStatus.CONFIRM and confirmed:
            previous_ref = self._decision_refs[proposal_id]
            decision = replace(decision, status=PolicyStatus.ALLOW, reason_code="USER_CONFIRMED")
            decision_ref = self.store.put(decision, prefix="policy-decision")
            self._decisions[proposal_id] = decision
            self._decision_refs[proposal_id] = decision_ref
            self._append_event(
                task_id,
                "decision.created",
                "policy_decision",
                decision_ref,
                caused_by=self._causes_for(previous_ref),
                snapshot_id=proposal.based_on_snapshot,
            )
        permitted = decision.status == PolicyStatus.ALLOW
        if not permitted:
            pending_confirmation = decision.status == PolicyStatus.CONFIRM
            return self._rejected_receipt(
                task_id,
                proposal,
                decision.reason_code,
                terminal=not pending_confirmation,
                notify_provider=not pending_confirmation,
            )

        latest = self.observe(task_id)
        guard = self._guards.get(decision.guard_ref or "")
        if guard is not None:
            guard_error = self.gate.recheck(guard, latest)
            if guard_error is not None:
                return self._rejected_receipt(task_id, proposal, guard_error)

        if proposal.action.type == ActionType.APPLICATION_LAUNCH:
            if self.application_launcher is None:
                return self._rejected_receipt(task_id, proposal, "CAPABILITY_UNAVAILABLE")
            receipt = self.application_launcher.launch(proposal)
        else:
            receipt = self.executor.execute(proposal)
        if (
            receipt.proposal_id != proposal.proposal_id
            or (
                receipt.status == ExecutionStatus.DELIVERED
                and receipt.executed_action != proposal.action
            )
        ):
            receipt = replace(
                receipt,
                proposal_id=proposal.proposal_id,
                status=ExecutionStatus.FAILED,
                error_code="EXECUTOR_ACTION_MISMATCH",
            )
        self._record_receipt(task_id, receipt)
        if receipt.status == ExecutionStatus.DELIVERED:
            self._states[task_id] = replace(
                self._states[task_id], step=self._states[task_id].step + 1
            )
        return receipt

    def evaluate(self, task_id: str) -> tuple[tuple[EvidenceRecord, ...], tuple[AssertionResult, ...], TaskState]:
        contract = self._require_task(task_id)
        snapshot = self.observe(task_id)
        evidence: list[EvidenceRecord] = []
        evidence_events: list[str] = []
        for provider in self.evidence_providers:
            try:
                records = provider.collect(contract.assertions, snapshot)
            except Exception as exc:
                self._record_attribution(
                    task_id,
                    AttributionEventKind.ERROR,
                    "evidence-collection",
                    "evidence-provider",
                    "EVIDENCE_COLLECTION_FAILED",
                    f"{provider.provider_id}: {type(exc).__name__}",
                )
                continue
            for record in records:
                if not set(record.facts) <= set(provider.fact_paths):
                    raise ValueError(f"provider emitted undeclared facts: {provider.provider_id}")
                self.store.put(record, object_ref=record.evidence_id)
                event = self._append_event(
                    task_id,
                    "evidence.collected",
                    "evidence",
                    record.evidence_id,
                    snapshot_id=snapshot.snapshot_id,
                    caused_by=self._latest_execution_causes(task_id),
                    artifact_refs=(record.raw_artifact_ref,) if record.raw_artifact_ref else (),
                )
                evidence_events.append(event.event_id)
                evidence.append(record)
        results = tuple(
            self.evaluator.evaluate(assertion, evidence, snapshot)
            for assertion in contract.assertions
        )
        result_events = []
        for result in results:
            result_ref = self.store.put(result, prefix="assertion-result")
            event = self._append_event(
                task_id,
                "assertion.evaluated",
                "assertion_result",
                result_ref,
                caused_by=tuple(evidence_events),
                snapshot_id=snapshot.snapshot_id,
            )
            result_events.append(event.event_id)
        state = self.reducer.reduce(contract, self._states[task_id], results)
        self._states[task_id] = state
        self._latest_results[task_id] = results
        state_ref = self.store.put(state, prefix="task-state")
        self._append_event(
            task_id,
            "task.transitioned",
            "state_transition",
            state_ref,
            caused_by=tuple(result_events),
            snapshot_id=snapshot.snapshot_id,
        )
        for result in results:
            if result.status == AssertionStatus.PASSED:
                selected_evidence = [
                    item for item in evidence if item.evidence_id in result.evidence_refs
                ]
                accepted_facts = list(self._verified_facts.get(task_id, ()))
                for item in selected_evidence:
                    if result.expression.path in item.facts:
                        accepted_facts.append(
                            {
                                "path": result.expression.path,
                                "value": item.facts[result.expression.path],
                                "source": item.provider,
                                "evidence_ref": item.evidence_id,
                                "freshness": "current",
                                "environment_version": snapshot.environment_version,
                                "expires_on_environment_change": item.expires_on_environment_change,
                            }
                        )
                self._verified_facts[task_id] = tuple(accepted_facts[-50:])
                fact_ref = self.store.put(
                    {"assertion_id": result.assertion_id, "evidence_refs": result.evidence_refs},
                    prefix="verified-fact",
                )
                result_event = next(
                    event for event in reversed(self.ledger.events(task_id))
                    if event.event_type == "assertion.evaluated"
                    and self.store.require(event.object_ref).assertion_id == result.assertion_id
                )
                self._append_event(
                    task_id,
                    "verified_fact.accepted",
                    "verified_fact",
                    fact_ref,
                    caused_by=tuple(
                        [self._object_events[ref] for ref in result.evidence_refs if ref in self._object_events]
                        + [result_event.event_id]
                    ),
                    snapshot_id=snapshot.snapshot_id,
                )
        return tuple(evidence), results, state

    def run_step(self, task_id: str, *, confirmed: bool = False, strategy: str = "compact") -> dict[str, Any]:
        self.observe(task_id)
        proposal = self.propose(task_id, strategy=strategy)
        decision = self.decide(proposal.proposal_id)
        if decision.status not in {PolicyStatus.ALLOW, PolicyStatus.CONFIRM}:
            return {"proposal": proposal, "decision": decision, "receipt": None, "state": self._states[task_id]}
        if decision.status == PolicyStatus.CONFIRM and not confirmed:
            return {"proposal": proposal, "decision": decision, "receipt": None, "state": self._states[task_id]}
        receipt = self.execute(proposal.proposal_id, confirmed=confirmed)
        evidence, results, state = self.evaluate(task_id)
        return {
            "proposal": proposal,
            "decision": decision,
            "receipt": receipt,
            "evidence": evidence,
            "assertion_results": results,
            "state": state,
        }

    def status(self, task_id: str) -> TaskState:
        self._require_task(task_id)
        return self._states[task_id]

    def has_task(self, task_id: str) -> bool:
        return task_id in self._contracts

    def task_contract(self, task_id: str) -> TaskContract:
        return self._require_task(task_id)

    def latest_snapshot(self, task_id: str) -> CanonicalSnapshot | None:
        self._require_task(task_id)
        return self._latest_snapshot.get(task_id)

    def reset(self, task_id: str) -> None:
        self._require_task(task_id)
        resetter = getattr(self.proposal_provider, "reset", None)
        if callable(resetter):
            resetter(task_id)
        self.ledger.clear(task_id)
        self._contracts.pop(task_id, None)
        self._states.pop(task_id, None)
        self._latest_snapshot.pop(task_id, None)
        self._latest_frame.pop(task_id, None)
        self._latest_receipt.pop(task_id, None)
        self._latest_results.pop(task_id, None)
        self._verified_facts.pop(task_id, None)
        for proposal_id, owner in tuple(self._proposal_task.items()):
            if owner == task_id:
                self._proposal_task.pop(proposal_id, None)
                self._provider_proposals.discard(proposal_id)
                self._decisions.pop(proposal_id, None)
                self._decision_refs.pop(proposal_id, None)
                self._terminal_receipts.pop(proposal_id, None)

    def _require_task(self, task_id: str) -> TaskContract:
        try:
            return self._contracts[task_id]
        except KeyError as exc:
            raise KeyError(f"unknown task: {task_id}") from exc

    def _rejected_receipt(
        self,
        task_id: str,
        proposal: ActionProposal,
        reason_code: str,
        *,
        terminal: bool = True,
        notify_provider: bool = True,
    ) -> ExecutionReceipt:
        now = utc_now()
        receipt = ExecutionReceipt(
            execution_id=new_id("execution"),
            proposal_id=proposal.proposal_id,
            status=ExecutionStatus.REJECTED,
            executed_action=None,
            started_at=now,
            finished_at=now,
            error_code=reason_code,
        )
        self._record_receipt(
            task_id, receipt, terminal=terminal, notify_provider=notify_provider
        )
        environment_codes = {
            "COORDINATE_SPACE_CHANGED",
            "TARGET_DISAPPEARED",
            "TARGET_IDENTITY_CHANGED",
            "TARGET_GEOMETRY_INVALIDATED",
            "TARGET_OCCLUDED",
            "HIT_TEST_CHANGED",
            "CURSOR_ORIGIN_CHANGED",
        }
        if reason_code in environment_codes:
            self._record_attribution(
                task_id,
                AttributionEventKind.SAFE_REFUSAL,
                "environment",
                "environment",
                reason_code,
                "Proposal guard detected a relevant environment change",
            )
        elif reason_code in {"SEMANTIC_POLICY_DENIED", "MECHANICAL_PERMISSION_DENIED", "CONFIRMATION_REQUIRED"}:
            self._record_attribution(
                task_id,
                AttributionEventKind.POLICY_DECISION,
                "policy",
                "controller-policy",
                "POLICY_DENIED" if reason_code != "CONFIRMATION_REQUIRED" else "CONFIRMATION_REQUIRED",
                "Controller policy did not authorize automatic execution",
            )
        return receipt

    def _record_receipt(
        self,
        task_id: str,
        receipt: ExecutionReceipt,
        *,
        terminal: bool = True,
        notify_provider: bool = True,
    ) -> None:
        self._latest_receipt[task_id] = receipt
        if terminal:
            self._terminal_receipts[receipt.proposal_id] = receipt
        self.store.put(receipt, object_ref=receipt.execution_id)
        self._append_event(
            task_id,
            "execution.completed",
            "execution_receipt",
            receipt.execution_id,
            caused_by=self._causes_for(self._decision_refs.get(receipt.proposal_id, receipt.proposal_id)),
        )
        recorder = getattr(self.proposal_provider, "record_execution", None)
        proposal_owner = self._proposal_task.get(receipt.proposal_id)
        if (
            notify_provider
            and callable(recorder)
            and proposal_owner == task_id
            and receipt.proposal_id in self._provider_proposals
        ):
            try:
                recorder(task_id, receipt)
            except Exception:
                # The receipt remains authoritative. Feedback failure affects
                # only the model adapter's future context and is diagnostic.
                self._record_attribution(
                    task_id,
                    AttributionEventKind.ERROR,
                    "protocol",
                    "unknown",
                    "MODEL_PROTOCOL_INVALID",
                    "Proposal provider rejected execution feedback",
                )
        if receipt.status == ExecutionStatus.FAILED:
            self._record_attribution(
                task_id,
                AttributionEventKind.ERROR,
                "execution",
                "executor",
                (
                    "EXECUTOR_ACTION_MISMATCH"
                    if receipt.error_code == "EXECUTOR_ACTION_MISMATCH"
                    else "EXECUTOR_ACTION_FAILED"
                ),
                receipt.error_code or "Executor failed to deliver the approved action",
            )

    def _record_attribution(
        self,
        task_id: str,
        event_kind: AttributionEventKind,
        stage: str,
        owner: str,
        code: str,
        summary: str,
        *,
        evidence_refs: tuple[str, ...] = (),
    ) -> Attribution:
        attribution = Attribution(
            attribution_id=new_id("attribution"),
            event_kind=event_kind,
            stage=stage,
            owner=owner,
            code=code,
            evidence_status=AttributionEvidenceStatus.CONFIRMED,
            primary=False,
            summary=summary,
            evidence_refs=evidence_refs,
        )
        self.store.put(attribution, object_ref=attribution.attribution_id)
        self._append_event(
            task_id,
            "attribution.recorded",
            "attribution",
            attribution.attribution_id,
            caused_by=tuple(self._object_events[ref] for ref in evidence_refs if ref in self._object_events),
        )
        return attribution

    def _append_event(self, task_id: str, event_type: str, epistemic_type: str, object_ref: str, **kwargs):
        event = self.ledger.append(task_id, event_type, epistemic_type, object_ref, **kwargs)
        self._object_events[object_ref] = event.event_id
        return event

    def _causes_for(self, object_ref: str) -> tuple[str, ...]:
        event_id = self._object_events.get(object_ref)
        return (event_id,) if event_id else ()

    def _latest_execution_causes(self, task_id: str) -> tuple[str, ...]:
        selected: list[str] = []
        for event in reversed(self.ledger.events(task_id)):
            if event.event_type in {"execution.completed", "snapshot.created"}:
                if event.event_type not in {item.split(":", 1)[0] for item in selected}:
                    selected.append(f"{event.event_type}:{event.event_id}")
                if len(selected) == 2:
                    break
        return tuple(item.split(":", 1)[1] for item in reversed(selected))


def response_envelope(
    operation: str,
    status: str,
    *,
    object_ref: str | None = None,
    error: dict[str, Any] | None = None,
    retry: dict[str, Any] | None = None,
    debug_ref: str | None = None,
) -> dict[str, Any]:
    return {
        "protocol_version": 2,
        "operation": operation,
        "status": status,
        "object_ref": object_ref,
        "error": error,
        "retry": retry,
        "debug_ref": debug_ref,
    }
