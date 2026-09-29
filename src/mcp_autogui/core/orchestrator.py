"""Thin coordinator for v2 proposal transactions."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from threading import RLock
from typing import Any

from ..ports.compositor import CompositorPort
from ..ports.evidence import EvidenceProvider
from ..ports.executor import ActionExecutor
from ..ports.frame import FrameProvider
from ..ports.proposal import ProposalProvider
from .audit_models import (
    Attribution,
    AttributionEventKind,
    AttributionEvidenceStatus,
    AttributionOwner,
    AttributionStage,
)
from .audit_recorder import AuditRecorder
from .assertion_evaluator import AssertionEvaluator, SUPPORTED_OPERATORS
from .context_builder import ContextBuilder
from .desktop import CanonicalSnapshot
from .evidence import AssertionResult, AssertionStatus, EvidenceRecord
from .facts import require_standard_fact_path
from .ledger import EventLedger
from .protocol import OperationFailure, ReasonCode, new_id, to_primitive, utc_now
from .proposal_validator import PreparedProposal, ProposalValidator, ValidationFailure
from .store import ObjectStore
from .task import TaskContract, TaskState, TaskStatus
from .transaction import (
    Action,
    ActionProposal,
    ActionType,
    AtomicActionReceipt,
    ExecutionReceipt,
    ExecutionStatus,
)
from .task_repository import TaskRepository
from .task_state import TaskStateReducer


class CoreOrchestrator:
    """Coordinates ports without allowing adapters to call one another."""

    def __init__(
        self,
        compositor: CompositorPort,
        executor: ActionExecutor,
        *,
        proposal_provider: ProposalProvider | None = None,
        frame_provider: FrameProvider | None = None,
        evidence_providers: Sequence[EvidenceProvider] = (),
        denied_actions: frozenset[ActionType] = frozenset(),
        store: ObjectStore | None = None,
        ledger: EventLedger | None = None,
        audit_store: ObjectStore | None = None,
        audit_ledger: EventLedger | None = None,
        diagnostic_enabled: bool = False,
    ) -> None:
        self.compositor = compositor
        self.executor = executor
        self.proposal_provider = proposal_provider
        self.frame_provider = frame_provider
        self.evidence_providers = tuple(evidence_providers)
        provider_ids = [provider.provider_id for provider in self.evidence_providers]
        if len(provider_ids) != len(set(provider_ids)):
            raise ValueError("evidence provider IDs must be unique")
        for provider in self.evidence_providers:
            for path in provider.fact_paths:
                require_standard_fact_path(path)
        self._audit = AuditRecorder(
            store,
            ledger,
            audit_store=audit_store,
            audit_ledger=audit_ledger,
            diagnostic_enabled=diagnostic_enabled,
        )
        self.store = self._audit.store
        self.ledger = self._audit.ledger
        self.diagnostic_enabled = diagnostic_enabled
        self.validator = ProposalValidator(
            compositor.descriptor,
            compositor.hit_test,
            denied_actions=denied_actions,
            action_validator=getattr(executor, "validate_action", None),
        )
        self.evaluator = AssertionEvaluator()
        self.reducer = TaskStateReducer()
        self.context_builder = ContextBuilder()
        self._tasks = TaskRepository()
        self._execution_lock = RLock()

    def register_task(self, contract: TaskContract) -> TaskState:
        if not contract.task_id or not contract.goal:
            raise ValueError("task_id and goal must not be empty")
        if len(contract.steps) > 32 or any(
            not isinstance(step, str) or not step.strip() for step in contract.steps
        ):
            raise ValueError("task steps must contain at most 32 non-empty strings")
        assertion_ids = [assertion.assertion_id for assertion in contract.assertions]
        if len(assertion_ids) != len(set(assertion_ids)) or any(
            not item for item in assertion_ids
        ):
            raise ValueError("assertion IDs must be non-empty and unique within a task")
        for assertion in contract.assertions:
            require_standard_fact_path(assertion.path)
            if assertion.operator not in SUPPORTED_OPERATORS:
                raise ValueError(f"unsupported assertion operator: {assertion.operator}")
        state = self._tasks.register(contract)
        contract_ref = self._audit.put(
            contract.task_id, contract, prefix="task-contract"
        )
        self._append_event(contract.task_id, "task.created", contract_ref)
        return state

    def observe(self, task_id: str) -> CanonicalSnapshot:
        self._require_task(task_id)
        return self.adopt_snapshot(task_id, self.compositor.observe())

    def adopt_snapshot(self, task_id: str, snapshot: CanonicalSnapshot) -> CanonicalSnapshot:
        """Record a canonical observation captured by an adapter-aware facade."""
        self._require_task(task_id)
        self._tasks.set_snapshot(task_id, snapshot)
        self._audit.put(task_id, snapshot, object_ref=snapshot.snapshot_id)
        self._append_event(
            task_id,
            "snapshot.created",
            snapshot.snapshot_id,
            snapshot_id=snapshot.snapshot_id,
            artifact_refs=(snapshot.raw_artifact_ref,) if snapshot.raw_artifact_ref else (),
        )
        return snapshot

    def propose(self, task_id: str, *, strategy: str = "compact") -> ActionProposal:
        if self.proposal_provider is None:
            raise OperationFailure(
                ReasonCode.CAPABILITY_UNAVAILABLE,
                "proposal provider is unavailable",
                retry=False,
                required_action="install-or-configure-provider",
            )
        contract = self._require_task(task_id)
        state = self._tasks.state(task_id)
        snapshot = self._tasks.snapshot(task_id) or self.observe(task_id)
        frame = self.frame_provider.capture_frame() if self.frame_provider is not None else None
        if frame is not None:
            self._audit.put(task_id, frame, object_ref=frame.frame_id)
            self._append_event(
                task_id,
                "frame.captured",
                frame.frame_id,
                caused_by=self._causes_for(snapshot.snapshot_id),
                snapshot_id=snapshot.snapshot_id,
                artifact_refs=(frame.image_ref,),
            )
        context = self.context_builder.build(
            contract,
            state,
            self.ledger.events(task_id),
            based_on_snapshot=snapshot.snapshot_id,
            frame=frame,
            recent_receipt=self._tasks.latest_receipt(task_id),
            assertion_results=self._tasks.recent_results(task_id),
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
            primary_attribution=(
                to_primitive(self._audit.primary_attribution(task_id))
                if self._audit.primary_attribution_ref(task_id) is not None
                else None
            ),
            strategy=strategy,
        )
        self._audit.put(task_id, context, object_ref=context.model_context_id)
        try:
            proposal = self.proposal_provider.propose(context)
        except Exception as exc:
            debug_ref = getattr(exc, "debug_ref", None)
            if isinstance(debug_ref, str) and debug_ref:
                diagnostic = self._append_event(
                    task_id,
                    "model_diagnostic.recorded",
                    debug_ref,
                    caused_by=self._causes_for(snapshot.snapshot_id),
                    snapshot_id=snapshot.snapshot_id,
                    debug_ref=debug_ref,
                )
                self._record_attribution(
                    task_id,
                    AttributionEventKind.ERROR,
                    AttributionStage.PLANNING,
                    AttributionOwner.MODEL,
                    ReasonCode.MODEL_PLANNING_INVALID,
                    str(exc),
                    evidence_refs=(diagnostic.event_id, debug_ref),
                )
            raise
        if proposal.based_on_snapshot != snapshot.snapshot_id:
            raise ValueError("proposal must reference the current snapshot")
        self.submit_proposal(task_id, proposal, provider_owned=True)
        return proposal

    def submit_proposal(
        self,
        task_id: str,
        proposal: ActionProposal,
        *,
        caused_by: tuple[str, ...] = (),
        provider_owned: bool = False,
    ) -> ActionProposal:
        self._require_task(task_id)
        self._tasks.submit_proposal(task_id, proposal, provider_owned=provider_owned)
        self._audit.put(task_id, proposal, object_ref=proposal.proposal_id)
        causal_events = caused_by or self._causes_for(proposal.based_on_snapshot)
        if proposal.debug_ref:
            diagnostic = self._append_event(
                task_id,
                "model_diagnostic.recorded",
                proposal.debug_ref,
                caused_by=causal_events,
                snapshot_id=proposal.based_on_snapshot,
                debug_ref=proposal.debug_ref,
            )
            causal_events = (*causal_events, diagnostic.event_id)
        self._append_event(
            task_id,
            "proposal.created",
            proposal.proposal_id,
            caused_by=causal_events,
            snapshot_id=proposal.based_on_snapshot,
            debug_ref=proposal.debug_ref,
        )
        return proposal

    def prepare(self, proposal_id: str) -> PreparedProposal | ValidationFailure:
        task_id = self._tasks.task_for_proposal(proposal_id)
        if task_id is None:
            raise KeyError(proposal_id)
        proposal = self._tasks.proposal(proposal_id)
        snapshot = self._tasks.snapshot_by_id(proposal.based_on_snapshot)
        if snapshot is None:
            return ValidationFailure(ReasonCode.SNAPSHOT_UNAVAILABLE, False)
        return self.validator.prepare(snapshot, proposal)

    def execute(
        self,
        proposal_id: str,
        *,
        current_snapshot: CanonicalSnapshot | None = None,
    ) -> ValidationFailure | ExecutionReceipt:
        # Observation, dependency recheck, and input injection are one critical
        # section across tasks; otherwise another task could alter the desktop
        # between validation and the side effect.
        with self._execution_lock:
            return self._execute(proposal_id, current_snapshot=current_snapshot)

    def _execute(
        self,
        proposal_id: str,
        *,
        current_snapshot: CanonicalSnapshot | None = None,
    ) -> ValidationFailure | ExecutionReceipt:
        task_id = self._tasks.task_for_proposal(proposal_id)
        if task_id is None:
            raise KeyError(proposal_id)
        existing_receipt = self._tasks.terminal_receipt(proposal_id)
        if existing_receipt is not None:
            return existing_receipt
        proposal = self._tasks.proposal(proposal_id)
        prepared = self.prepare(proposal_id)
        if isinstance(prepared, ValidationFailure):
            self._record_validation_failure(task_id, proposal, prepared)
            return prepared
        if all(action.type == ActionType.DONE for action in proposal.actions):
            failure = ValidationFailure(ReasonCode.MODEL_PROTOCOL_INVALID, False)
            self._record_validation_failure(task_id, proposal, failure)
            return failure
        if self._tasks.state(task_id).step >= self._tasks.contract(task_id).limits.max_steps:
            failure = ValidationFailure(ReasonCode.MODEL_PLANNING_INVALID, False)
            self._record_validation_failure(task_id, proposal, failure)
            return failure

        latest = (
            self.adopt_snapshot(task_id, current_snapshot)
            if current_snapshot is not None
            else self.observe(task_id)
        )
        rechecked = self.validator.recheck(prepared, latest)
        if isinstance(rechecked, ValidationFailure):
            self._record_validation_failure(task_id, proposal, rechecked)
            return rechecked

        executable = replace(
            proposal,
            actions=tuple(action for action in proposal.actions if action.type != ActionType.DONE),
        )
        self._transition_state(
            task_id,
            replace(
                self._tasks.state(task_id),
                step=self._tasks.state(task_id).step + 1,
                status=TaskStatus.RUNNING,
            ),
            caused_by=self._causes_for(proposal.proposal_id),
            snapshot_id=latest.snapshot_id,
        )
        receipt = self._execute_action_sequence(executable)
        self._record_receipt(task_id, receipt)
        return receipt

    def _execute_action_sequence(self, proposal: ActionProposal) -> ExecutionReceipt:
        """Execute one model proposal as an ordered, fail-fast action sequence."""
        atomic: list[AtomicActionReceipt] = []
        delivered: list[Action] = []
        sequence_started = utc_now()
        aggregate_status = ExecutionStatus.DELIVERED
        aggregate_error = None

        for action_index, action in enumerate(proposal.action_sequence):
            single = replace(proposal, actions=(action,))
            try:
                result = self.executor.execute(single)
            except Exception:
                result = ExecutionReceipt(
                    execution_id=new_id("execution"),
                    proposal_id=proposal.proposal_id,
                    status=ExecutionStatus.FAILED,
                    executed_action=None,
                    started_at=utc_now(),
                    finished_at=utc_now(),
                    error_code=ReasonCode.EXECUTOR_ACTION_FAILED,
                )
            status = result.status
            error = result.error_code
            if result.proposal_id != proposal.proposal_id or (
                status == ExecutionStatus.DELIVERED and result.executed_action != action
            ):
                status = ExecutionStatus.FAILED
                error = ReasonCode.EXECUTOR_ACTION_MISMATCH
            atomic.append(
                AtomicActionReceipt(
                    action_index=action_index,
                    action=action,
                    status=status,
                    started_at=result.started_at,
                    finished_at=result.finished_at,
                    executor_execution_id=result.execution_id,
                    error_code=error,
                )
            )
            if status == ExecutionStatus.DELIVERED:
                delivered.append(action)
                continue
            aggregate_status = status
            aggregate_error = error or ReasonCode.EXECUTOR_ACTION_FAILED
            break

        finished_at = atomic[-1].finished_at if atomic else utc_now()
        if len(proposal.action_sequence) == 1 and atomic:
            execution_id = atomic[0].executor_execution_id or new_id("execution")
        else:
            execution_id = new_id("execution")
        return ExecutionReceipt(
            execution_id=execution_id,
            proposal_id=proposal.proposal_id,
            status=aggregate_status,
            executed_action=delivered[0] if delivered else None,
            started_at=atomic[0].started_at if atomic else sequence_started,
            finished_at=finished_at,
            error_code=aggregate_error,
            executed_actions=tuple(delivered),
            action_receipts=tuple(atomic),
        )

    def evaluate(
        self, task_id: str
    ) -> tuple[tuple[EvidenceRecord, ...], tuple[AssertionResult, ...], TaskState]:
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
                    ReasonCode.EVIDENCE_COLLECTION_FAILED,
                    f"{provider.provider_id}: {type(exc).__name__}",
                )
                continue
            for record in records:
                if not set(record.facts) <= set(provider.fact_paths):
                    raise ValueError(f"provider emitted undeclared facts: {provider.provider_id}")
                self._audit.put(task_id, record, object_ref=record.evidence_id)
                event = self._append_event(
                    task_id,
                    "evidence.collected",
                    record.evidence_id,
                    snapshot_id=snapshot.snapshot_id,
                    caused_by=self._latest_execution_causes(task_id),
                    artifact_refs=(record.artifact_ref,) if record.artifact_ref else (),
                )
                evidence_events.append(event.event_id)
                evidence.append(record)
        results = tuple(
            self.evaluator.evaluate(assertion, evidence, snapshot)
            for assertion in contract.assertions
        )
        result_events = []
        for result in results:
            result_ref = self._audit.put(
                task_id, result, prefix="assertion-result"
            )
            event = self._append_event(
                task_id,
                "assertion.evaluated",
                result_ref,
                caused_by=tuple(evidence_events),
                snapshot_id=snapshot.snapshot_id,
            )
            result_events.append(event.event_id)
        state = self.reducer.reduce(contract, self._tasks.state(task_id), results)
        self._tasks.set_results(task_id, results)
        self._transition_state(
            task_id,
            state,
            caused_by=tuple(result_events),
            snapshot_id=snapshot.snapshot_id,
        )
        if state.status in {TaskStatus.RETRYING, TaskStatus.FAILED}:
            failed_refs = tuple(
                ref
                for result in results
                if result.status == AssertionStatus.FAILED
                for ref in result.evidence_refs
            )
            self._record_attribution(
                task_id,
                AttributionEventKind.ERROR,
                AttributionStage.OUTCOME,
                AttributionOwner.UNKNOWN,
                (
                    ReasonCode.OUTCOME_POSTCONDITION_FAILED
                    if failed_refs
                    else ReasonCode.ROOT_CAUSE_UNRESOLVED
                ),
                "One or more required task assertions did not pass",
                evidence_refs=failed_refs,
                evidence_status=(
                    AttributionEvidenceStatus.CONFIRMED
                    if failed_refs
                    else AttributionEvidenceStatus.INSUFFICIENT
                ),
            )
        elif any(
            result.status in {AssertionStatus.UNKNOWN, AssertionStatus.CONFLICT}
            for result in results
        ):
            self._record_attribution(
                task_id,
                AttributionEventKind.INSUFFICIENT_EVIDENCE,
                AttributionStage.OUTCOME,
                AttributionOwner.UNKNOWN,
                ReasonCode.INSUFFICIENT_GROUND_TRUTH,
                "Required assertions lack applicable current evidence",
                evidence_status=AttributionEvidenceStatus.INSUFFICIENT,
            )
        return tuple(evidence), results, state

    def run_step(
        self,
        task_id: str,
        *,
        strategy: str = "compact",
    ) -> dict[str, Any]:
        self.observe(task_id)
        proposal = self.propose(task_id, strategy=strategy)
        has_done = proposal.actions[-1].type == ActionType.DONE
        executable = any(action.type != ActionType.DONE for action in proposal.actions)
        if not executable:
            self._finalize_provider_outcome(
                task_id, proposal, "partial", ReasonCode.INSUFFICIENT_GROUND_TRUTH
            )
            if self._has_pending_plan_step(task_id):
                state = self._advance_plan_step(
                    task_id,
                    caused_by=self._causes_for(proposal.proposal_id),
                    snapshot_id=proposal.based_on_snapshot,
                )
                return {
                    "proposal": proposal, "validation": None, "receipt": None,
                    "evidence": (), "assertion_results": (), "state": state,
                }
            if self._tasks.contract(task_id).assertions:
                evidence, results, state = self.evaluate(task_id)
                return {
                    "proposal": proposal, "validation": None, "receipt": None,
                    "evidence": evidence, "assertion_results": results, "state": state,
                }
            latest_receipt = self._tasks.latest_receipt(task_id)
            if (
                latest_receipt is None
                or latest_receipt.status != ExecutionStatus.DELIVERED
            ):
                state = self._transition_state(
                    task_id,
                    replace(self._tasks.state(task_id), status=TaskStatus.FAILED),
                    caused_by=self._causes_for(proposal.proposal_id),
                    snapshot_id=proposal.based_on_snapshot,
                )
                return {"proposal": proposal, "validation": None, "receipt": None, "state": state}
            state = self.reducer.delivered_unverified(self._tasks.state(task_id))
            self._transition_state(
                task_id,
                state,
                caused_by=self._causes_for(proposal.proposal_id),
                snapshot_id=proposal.based_on_snapshot,
            )
            return {"proposal": proposal, "validation": None, "receipt": None, "state": state}

        execution = self.execute(proposal.proposal_id)
        if isinstance(execution, ValidationFailure):
            return {
                "proposal": proposal,
                "validation": execution,
                "receipt": None,
                "state": self._tasks.state(task_id),
            }
        receipt = execution
        if receipt.status != ExecutionStatus.DELIVERED:
            return {
                "proposal": proposal,
                "validation": None,
                "receipt": receipt,
                "state": self._tasks.state(task_id),
            }
        if has_done and self._has_pending_plan_step(task_id):
            state = self._advance_plan_step(
                task_id,
                caused_by=self._causes_for(receipt.execution_id),
                snapshot_id=proposal.based_on_snapshot,
            )
            return {
                "proposal": proposal, "validation": None, "receipt": receipt,
                "evidence": (), "assertion_results": (), "state": state,
            }
        if has_done and not self._tasks.contract(task_id).assertions:
            state = self.reducer.delivered_unverified(self._tasks.state(task_id))
            self._transition_state(
                task_id,
                state,
                caused_by=self._causes_for(receipt.execution_id),
                snapshot_id=proposal.based_on_snapshot,
            )
            return {"proposal": proposal, "validation": None, "receipt": receipt, "state": state}
        evidence, results, state = self.evaluate(task_id)
        return {
            "proposal": proposal,
            "validation": None,
            "receipt": receipt,
            "evidence": evidence,
            "assertion_results": results,
            "state": state,
        }

    def run(
        self,
        task_id: str,
        *,
        strategy: str = "compact",
        max_iterations: int | None = None,
    ) -> dict[str, Any]:
        """Run bounded Proposal transactions until the task blocks or terminates."""
        contract = self._require_task(task_id)
        remaining = max(0, contract.limits.max_steps - self._tasks.state(task_id).step)
        limit = remaining if max_iterations is None else min(remaining, max_iterations)
        if limit < 1:
            raise ValueError("no execution iterations remain")
        outcomes: list[dict[str, Any]] = []
        repeated_without_progress = 0
        previous_signature: object = None
        active_strategy = strategy
        for _ in range(limit):
            before = set(self._tasks.state(task_id).completed_assertions)
            before_plan_step = self._tasks.state(task_id).plan_step
            outcome = self.run_step(task_id, strategy=active_strategy)
            proposal = outcome["proposal"]
            validation = outcome.get("validation")
            receipt = outcome.get("receipt")
            state = outcome["state"]
            outcomes.append(
                {
                    "proposal_ref": proposal.proposal_id,
                    "validation_failure": (
                        validation.reason_code.value if validation is not None else None
                    ),
                    "execution_ref": receipt.execution_id if receipt is not None else None,
                    "task_status": state.status.value,
                }
            )
            if validation is not None:
                return {"state": state, "iterations": tuple(outcomes)}
            if receipt is not None and receipt.status != ExecutionStatus.DELIVERED:
                return {"state": state, "iterations": tuple(outcomes)}
            if state.status in {
                TaskStatus.COMPLETED, TaskStatus.DELIVERED_UNVERIFIED, TaskStatus.FAILED
            }:
                return {"state": state, "iterations": tuple(outcomes)}

            signature = to_primitive(proposal.action_sequence)
            progressed = (
                bool(set(state.completed_assertions) - before)
                or state.plan_step > before_plan_step
            )
            if not progressed and signature == previous_signature:
                repeated_without_progress += 1
            else:
                repeated_without_progress = 0
            previous_signature = signature
            if repeated_without_progress >= 1:
                resetter = getattr(self.proposal_provider, "reset", None)
                if callable(resetter):
                    resetter(task_id)
                self._record_attribution(
                    task_id,
                    AttributionEventKind.INCOMPLETE,
                    AttributionStage.PLANNING,
                    AttributionOwner.MODEL,
                    ReasonCode.MODEL_PLANNING_INVALID,
                    "The proposal provider repeated an action without verified progress",
                    evidence_status=AttributionEvidenceStatus.INFERRED,
                )
                return {
                    "state": state,
                    "iterations": tuple(outcomes),
                    "retry": {"retry": False, "required_action": "ask-controller"},
                }
            active_strategy = (
                "recovery" if state.status == TaskStatus.RETRYING else strategy
            )
        return {
            "state": self._tasks.state(task_id),
            "iterations": tuple(outcomes),
            "retry": {"retry": True, "required_action": "continue-run"},
        }

    def status(self, task_id: str) -> TaskState:
        self._require_task(task_id)
        return self._tasks.state(task_id)

    def mark_delivered_unverified(self, task_id: str) -> TaskState:
        contract = self._require_task(task_id)
        if any(assertion.required for assertion in contract.assertions):
            raise ValueError("tasks with required assertions must be evaluated")
        receipt = self._tasks.latest_receipt(task_id)
        if receipt is None or receipt.status != ExecutionStatus.DELIVERED:
            raise ValueError("delivered-unverified requires a fully delivered action sequence")
        state = self.reducer.delivered_unverified(self._tasks.state(task_id))
        self._transition_state(
            task_id,
            state,
            caused_by=self._causes_for(receipt.execution_id),
            snapshot_id=self._tasks.snapshot(task_id).snapshot_id,
        )
        return state

    def has_task(self, task_id: str) -> bool:
        return self._tasks.has_task(task_id)

    def task_contract(self, task_id: str) -> TaskContract:
        return self._require_task(task_id)

    def latest_snapshot(self, task_id: str) -> CanonicalSnapshot | None:
        self._require_task(task_id)
        return self._tasks.snapshot(task_id)

    def reset(self, task_id: str) -> None:
        with self._execution_lock:
            self._require_task(task_id)
            resetter = getattr(self.proposal_provider, "reset", None)
            if callable(resetter):
                resetter(task_id)
            reset_ref = self._audit.put(
                task_id,
                {
                    "task_id": task_id,
                    "previous_state": to_primitive(self._tasks.state(task_id)),
                    "reason": "controller-reset",
                },
                prefix="task-reset",
            )
            self._append_event(
                task_id,
                "task.reset",
                reset_ref,
                caused_by=tuple(event.event_id for event in self.ledger.events(task_id)[-1:]),
            )
            self._tasks.clear_task(task_id)
            self._audit.clear_task(task_id)

    def _has_pending_plan_step(self, task_id: str) -> bool:
        contract = self._tasks.contract(task_id)
        return bool(contract.steps) and self._tasks.state(task_id).plan_step < len(contract.steps) - 1

    def _advance_plan_step(
        self,
        task_id: str,
        *,
        caused_by: tuple[str, ...],
        snapshot_id: str,
    ) -> TaskState:
        contract = self._tasks.contract(task_id)
        current = self._tasks.state(task_id)
        if not contract.steps or current.plan_step >= len(contract.steps) - 1:
            raise ValueError("task has no pending plan step")
        next_index = current.plan_step + 1
        plan_ref = self._audit.put(
            task_id,
            {
                "task_id": task_id,
                "from_index": current.plan_step,
                "to_index": next_index,
                "next_step": contract.steps[next_index],
            },
            prefix="task-plan-step",
        )
        event = self._append_event(
            task_id,
            "task.plan_step_advanced",
            plan_ref,
            caused_by=caused_by,
            snapshot_id=snapshot_id or None,
        )
        resetter = getattr(self.proposal_provider, "reset", None)
        if callable(resetter):
            resetter(task_id)
        return self._transition_state(
            task_id,
            replace(current, status=TaskStatus.RUNNING, plan_step=next_index),
            caused_by=(event.event_id,),
            snapshot_id=snapshot_id,
        )

    def _require_task(self, task_id: str) -> TaskContract:
        try:
            return self._tasks.contract(task_id)
        except KeyError:
            raise

    def _record_validation_failure(
        self,
        task_id: str,
        proposal: ActionProposal,
        failure: ValidationFailure,
    ) -> None:
        state = self.reducer.validation_failure(
            self._tasks.contract(task_id), self._tasks.state(task_id),
            retryable=failure.retryable,
        )
        self._transition_state(
            task_id,
            state,
            caused_by=self._causes_for(proposal.proposal_id),
            snapshot_id=proposal.based_on_snapshot,
        )
        self._finalize_provider_outcome(
            task_id, proposal, "rejected", failure.reason_code
        )
        self._record_attribution(
            task_id,
            AttributionEventKind.SAFE_REFUSAL,
            "environment" if failure.retryable else "protocol",
            "environment" if failure.retryable else "unknown",
            failure.reason_code,
            "Proposal validation failed before input injection",
        )

    def _finalize_provider_outcome(
        self, task_id: str, proposal: ActionProposal, status: str, reason: ReasonCode
    ) -> None:
        if self._tasks.finalized(proposal.proposal_id):
            return
        if self._tasks.provider_owns(proposal.proposal_id):
            callback = getattr(self.proposal_provider, "record_outcome", None)
            notified = True
            if callable(callback):
                try:
                    callback(
                        task_id,
                        status=status,
                        execution={
                            "proposal": to_primitive(proposal),
                            "delivered": False,
                        },
                        reason=reason,
                    )
                except Exception:
                    notified = False
            if not notified:
                self._record_attribution(
                    task_id,
                    AttributionEventKind.ERROR,
                    "protocol",
                    "unknown",
                    ReasonCode.MODEL_PROTOCOL_INVALID,
                    "Proposal provider rejected terminal feedback",
                )
        self._tasks.finalize(proposal.proposal_id)

    def _record_receipt(
        self,
        task_id: str,
        receipt: ExecutionReceipt,
        *,
        terminal: bool = True,
        notify_provider: bool = True,
    ) -> None:
        self._tasks.record_receipt(task_id, receipt, terminal=terminal)
        self._audit.put(task_id, receipt, object_ref=receipt.execution_id)
        self._append_event(
            task_id,
            "execution.completed",
            receipt.execution_id,
            caused_by=self._causes_for(receipt.proposal_id),
        )
        proposal_owner = self._tasks.task_for_proposal(receipt.proposal_id)
        if (
            notify_provider
            and proposal_owner == task_id
            and self._tasks.provider_owns(receipt.proposal_id)
            and not self._tasks.finalized(receipt.proposal_id)
        ):
            callback = getattr(self.proposal_provider, "record_execution", None)
            notified = True
            if callable(callback):
                try:
                    callback(task_id, receipt)
                except Exception:
                    notified = False
            if not notified:
                # The receipt remains authoritative. Feedback failure affects
                # only the model adapter's future context and is diagnostic.
                self._record_attribution(
                    task_id,
                    AttributionEventKind.ERROR,
                    "protocol",
                    "unknown",
                    ReasonCode.MODEL_PROTOCOL_INVALID,
                    "Proposal provider rejected execution feedback",
                )
            self._tasks.finalize(receipt.proposal_id)
        if receipt.status != ExecutionStatus.DELIVERED:
            self._transition_state(
                task_id,
                self.reducer.execution_failure(self._tasks.state(task_id)),
                caused_by=self._causes_for(receipt.execution_id),
                snapshot_id=(
                    self._tasks.snapshot(task_id).snapshot_id
                    if self._tasks.snapshot(task_id) is not None
                    else ""
                ),
            )
        if receipt.status == ExecutionStatus.FAILED:
            self._record_attribution(
                task_id,
                AttributionEventKind.ERROR,
                "execution",
                "executor",
                (
                    ReasonCode.EXECUTOR_ACTION_MISMATCH
                    if receipt.error_code == ReasonCode.EXECUTOR_ACTION_MISMATCH
                    else ReasonCode.EXECUTOR_ACTION_FAILED
                ),
                receipt.error_code or "Executor failed to deliver the approved action",
            )

    def _record_attribution(
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
        return self._audit.record_attribution(
            task_id,
            event_kind,
            stage,
            owner,
            code,
            summary,
            evidence_refs=evidence_refs,
            evidence_status=evidence_status,
        )

    def _transition_state(
        self,
        task_id: str,
        state: TaskState,
        *,
        caused_by: tuple[str, ...],
        snapshot_id: str,
    ) -> TaskState:
        self._tasks.set_state(state)
        reference = self._audit.put(task_id, state, prefix="task-state")
        self._append_event(
            task_id,
            "task.transitioned",
            reference,
            caused_by=caused_by,
            snapshot_id=snapshot_id or None,
        )
        return state

    def store_runtime_object(
        self, value: Any, *, task_id: str | None = None, prefix: str = "object"
    ) -> str:
        return self._audit.put_diagnostic(
            value, task_id=task_id, prefix=prefix
        )

    @property
    def recording_errors(self) -> tuple[str, ...]:
        return self._audit.recording_errors

    def attributions(self, task_id: str) -> tuple[Attribution, ...]:
        """Return current or historical audit attribution facts for a task."""
        return self._audit.attributions(task_id)

    def primary_attribution(self, task_id: str) -> Attribution | None:
        self._require_task(task_id)
        return self._audit.primary_attribution(task_id)

    def _append_event(self, task_id: str, event_type: str, object_ref: str, **kwargs):
        return self._audit.append(task_id, event_type, object_ref, **kwargs)

    def _causes_for(self, object_ref: str) -> tuple[str, ...]:
        return self._audit.causes_for(object_ref)

    def _latest_execution_causes(self, task_id: str) -> tuple[str, ...]:
        return self._audit.latest_execution_causes(task_id)
