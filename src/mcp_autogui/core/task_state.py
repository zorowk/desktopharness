"""Deterministic task-state reducer; no provider or model calls live here."""

from __future__ import annotations

from collections.abc import Sequence

from .models import AssertionResult, AssertionStatus, TaskContract, TaskState, TaskStatus


class TaskStateReducer:
    def reduce(
        self,
        contract: TaskContract,
        state: TaskState,
        results: Sequence[AssertionResult],
    ) -> TaskState:
        by_id = {result.assertion_id: result for result in results}
        required = [item for item in contract.assertions if item.required]
        completed = tuple(
            item.assertion_id
            for item in contract.assertions
            if by_id.get(item.assertion_id) is not None
            and by_id[item.assertion_id].status == AssertionStatus.PASSED
        )
        failed = tuple(
            item.assertion_id
            for item in contract.assertions
            if by_id.get(item.assertion_id) is not None
            and by_id[item.assertion_id].status == AssertionStatus.FAILED
        )
        unresolved = [
            item
            for item in required
            if by_id.get(item.assertion_id) is None
            or by_id[item.assertion_id].status in {AssertionStatus.UNKNOWN, AssertionStatus.CONFLICT}
        ]
        hard_failures = [item for item in required if item.assertion_id in failed and not item.recoverable]
        recoverable_failures = [item for item in required if item.assertion_id in failed and item.recoverable]

        if required and all(item.assertion_id in completed for item in required):
            status = TaskStatus.COMPLETED
            retries = state.retries
        elif hard_failures or state.step >= contract.limits.max_steps:
            status = TaskStatus.FAILED
            retries = state.retries
        elif recoverable_failures:
            if state.retries < contract.limits.max_retries:
                status = TaskStatus.RETRY
                retries = state.retries + 1
            else:
                status = TaskStatus.FAILED
                retries = state.retries
        elif unresolved:
            status = TaskStatus.NEEDS_EVIDENCE
            retries = state.retries
        else:
            status = TaskStatus.CONTINUE
            retries = state.retries
        verified = tuple(f"assertion:{item}" for item in completed)
        return TaskState(
            task_id=contract.task_id,
            status=status,
            step=state.step,
            retries=retries,
            completed_assertions=completed,
            failed_assertions=failed,
            verified_facts=verified,
        )

