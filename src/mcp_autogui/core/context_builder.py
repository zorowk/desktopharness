"""Controlled projections from ledger-backed state into model context."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from .models import (
    AssertionResult,
    AssertionStatus,
    ExecutionReceipt,
    FrameReference,
    LedgerEvent,
    ModelContext,
    TaskContract,
    TaskState,
    new_id,
    to_primitive,
)


class ContextBuilder:
    STRATEGIES = frozenset({"compact", "visual-heavy", "recovery", "verification-focused", "planning-reset"})
    PROFILES = {
        "compact": {"events": 12, "frames": 1, "facts": 8, "feedback": 4},
        "visual-heavy": {"events": 8, "frames": 4, "facts": 4, "feedback": 2},
        "recovery": {"events": 24, "frames": 2, "facts": 12, "feedback": 8},
        "verification-focused": {"events": 20, "frames": 2, "facts": 16, "feedback": 12},
        "planning-reset": {"events": 12, "frames": 1, "facts": 12, "feedback": 6},
    }

    def build(
        self,
        contract: TaskContract,
        state: TaskState,
        events: Sequence[LedgerEvent],
        *,
        based_on_snapshot: str,
        frame: FrameReference | None = None,
        recent_receipt: ExecutionReceipt | None = None,
        assertion_results: Sequence[AssertionResult] = (),
        verified_facts: Sequence[dict[str, Any]] = (),
        spatial_projection: dict[str, Any] | None = None,
        primary_attribution: dict[str, Any] | None = None,
        strategy: str = "compact",
    ) -> ModelContext:
        if strategy not in self.STRATEGIES:
            raise ValueError(f"unknown context strategy: {strategy}")
        limits = self.PROFILES[strategy]
        pending = tuple(
            assertion.assertion_id
            for assertion in contract.assertions
            if assertion.assertion_id not in state.completed_assertions
        )
        feedback = tuple(
            {
                "assertion_id": result.assertion_id,
                "status": result.status.value,
                "evidence_refs": list(result.evidence_refs),
            }
            for result in assertion_results[-limits["feedback"]:]
            if result.status != AssertionStatus.PASSED
        )
        projected_events = self._project_events(events, strategy, limits["events"])
        recent_frames = tuple(
            event.object_ref
            for event in events
            if event.event_type == "frame.captured"
        )[-limits["frames"]:]
        return ModelContext(
            model_context_id=new_id("context"),
            task_id=contract.task_id,
            based_on_snapshot=based_on_snapshot,
            frame=frame,
            goal=contract.goal,
            current_step=state.step,
            pending_assertions=pending,
            verified_facts=tuple(verified_facts[-limits["facts"]:]),
            recent_execution_receipt=(to_primitive(recent_receipt) if recent_receipt else None),
            assertion_feedback=feedback,
            constraints={
                "single_action_only": True,
                "remaining_steps": max(0, contract.limits.max_steps - state.step),
                "policy_profile": contract.policy_profile,
            },
            ledger_event_refs=tuple(event.event_id for event in projected_events),
            spatial_projection=spatial_projection or {},
            strategy=strategy,
            recent_frame_refs=recent_frames,
            primary_attribution=primary_attribution if strategy == "recovery" else None,
            projection_limits=dict(limits),
        )

    @staticmethod
    def _project_events(
        events: Sequence[LedgerEvent], strategy: str, limit: int
    ) -> tuple[LedgerEvent, ...]:
        selected = list(events)
        if strategy == "planning-reset":
            selected = [
                event
                for event in selected
                if event.epistemic_type not in {"model_claim", "action_intent", "action_proposal"}
            ]
        elif strategy == "verification-focused":
            selected = [
                event
                for event in selected
                if event.epistemic_type
                in {"evidence", "assertion_result", "verified_fact", "state_transition", "execution_receipt"}
            ]
        elif strategy == "recovery":
            selected = [
                event
                for event in selected
                if event.epistemic_type
                in {
                    "action_proposal",
                    "policy_decision",
                    "execution_receipt",
                    "evidence",
                    "assertion_result",
                    "verified_fact",
                    "state_transition",
                    "attribution",
                }
            ]
        return tuple(selected[-limit:])
