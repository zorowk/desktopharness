"""Controlled projections from ledger-backed state into model context."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from .audit_models import LedgerEvent
from .context import ModelContext
from .desktop import FrameReference
from .evidence import AssertionResult, AssertionStatus
from .protocol import new_id, to_primitive
from .task import TaskContract, TaskState
from .transaction import ExecutionReceipt


class ContextBuilder:
    STRATEGIES = frozenset({"compact", "recovery"})
    PROFILES = {
        "compact": {"events": 12, "frames": 1, "feedback": 4},
        "recovery": {"events": 24, "frames": 2, "feedback": 8},
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
        spatial_projection: dict[str, Any] | None = None,
        primary_attribution: dict[str, Any] | None = None,
        strategy: str = "compact",
    ) -> ModelContext:
        if strategy not in self.STRATEGIES:
            raise ValueError(f"unknown context strategy: {strategy}")
        limits = self.PROFILES[strategy]
        final_plan_step = not contract.steps or state.plan_step >= len(contract.steps) - 1
        pending = tuple(
            assertion.assertion_id
            for assertion in contract.assertions
            if assertion.assertion_id not in state.completed_assertions
        ) if final_plan_step else ()
        # Assertion IDs alone are not actionable for a visual agent.  In
        # particular, a task whose success is signalled by a window title must
        # tell the agent both the title and that it must be *active*.  Keep the
        # complete, controller-authoritative requirement in the projection so
        # the provider can plan a final verification/focus step rather than
        # merely executing the prose goal.
        completion_requirements = tuple(
            {
                "assertion_id": assertion.assertion_id,
                "path": assertion.path,
                "operator": assertion.operator,
                "expected": assertion.expected,
                "required": assertion.required,
            }
            for assertion in contract.assertions
            if assertion.assertion_id in pending
        )
        step_completion_requirements = tuple(
            {
                "assertion_id": assertion.assertion_id,
                "path": assertion.path,
                "operator": assertion.operator,
                "expected": assertion.expected,
                "required": assertion.required,
            }
            for assertion in (
                contract.step_assertions[state.plan_step]
                if contract.step_assertions else ()
            )
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
            recent_execution_receipt=(to_primitive(recent_receipt) if recent_receipt else None),
            assertion_feedback=feedback,
            constraints={
                "ordered_action_sequence": True,
                "observe_after_proposal": True,
                "remaining_steps": max(0, contract.limits.max_steps - state.step),
                "completion_requirements": completion_requirements,
                "plan": {
                    "overall_goal": contract.goal,
                    "steps": list(contract.steps),
                    "current_index": state.plan_step,
                    "current_step": (
                        contract.steps[state.plan_step] if contract.steps else contract.goal
                    ),
                    "is_final_step": final_plan_step,
                    "step_completion_requirements": step_completion_requirements,
                },
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
        if strategy == "recovery":
            selected = [
                event
                for event in selected
                if event.event_type
                in {
                    "proposal.created", "execution.completed",
                    "evidence.collected", "assertion.evaluated",
                    "task.transitioned", "attribution.recorded",
                }
            ]
        return tuple(selected[-limit:])
