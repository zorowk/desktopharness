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
        strategy: str = "compact",
    ) -> ModelContext:
        if strategy not in self.STRATEGIES:
            raise ValueError(f"unknown context strategy: {strategy}")
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
            for result in assertion_results
            if result.status != AssertionStatus.PASSED
        )
        return ModelContext(
            model_context_id=new_id("context"),
            task_id=contract.task_id,
            based_on_snapshot=based_on_snapshot,
            frame=frame,
            goal=contract.goal,
            current_step=state.step,
            pending_assertions=pending,
            verified_facts=tuple(verified_facts),
            recent_execution_receipt=(to_primitive(recent_receipt) if recent_receipt else None),
            assertion_feedback=feedback,
            constraints={
                "single_action_only": True,
                "remaining_steps": max(0, contract.limits.max_steps - state.step),
                "policy_profile": contract.policy_profile,
            },
            ledger_event_refs=tuple(event.event_id for event in events[-20:]),
            spatial_projection=spatial_projection or {},
            strategy=strategy,
        )
