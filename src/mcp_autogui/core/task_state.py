"""Deterministic task-state reducer; no provider or model calls live here."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace

from .evidence import AssertionResult, AssertionStatus
from .task import TaskContract, TaskState, TaskStatus


class TaskStateReducer:
    def validation_failure(
        self, contract: TaskContract, state: TaskState, *, retryable: bool
    ) -> TaskState:
        if not retryable:
            return replace(state, status=TaskStatus.FAILED)
        if state.retries < contract.limits.max_retries:
            return replace(state, status=TaskStatus.RETRYING, retries=state.retries + 1)
        return replace(state, status=TaskStatus.FAILED)

    @staticmethod
    def execution_failure(state: TaskState) -> TaskState:
        return replace(state, status=TaskStatus.FAILED)

    @staticmethod
    def delivered_unverified(state: TaskState) -> TaskState:
        return replace(state, status=TaskStatus.DELIVERED_UNVERIFIED)

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

        plan_pending = bool(contract.steps) and state.plan_step < len(contract.steps) - 1
        if plan_pending:
            status = (
                TaskStatus.FAILED
                if state.step >= contract.limits.max_steps
                else TaskStatus.RUNNING
            )
            retries = state.retries
            # Final postconditions may happen to be true transiently during an
            # earlier plan step.  Do not carry that observation forward as if
            # it had verified the completed workflow.
            completed = ()
            failed = ()
        elif required and all(item.assertion_id in completed for item in required):
            status = TaskStatus.COMPLETED
            retries = state.retries
        elif hard_failures or state.step >= contract.limits.max_steps:
            status = TaskStatus.FAILED
            retries = state.retries
        elif recoverable_failures:
            # A postcondition that is not true *yet* is the normal state of a
            # multi-step GUI task.  It must not consume the validation retry
            # budget on every observation; otherwise max_retries=1 permits
            # only two proposals even when max_steps is 10.  Keep planning in
            # recovery mode until the action-step budget is exhausted.  The
            # retry counter remains reserved for retryable validation failures
            # (for example a target moving between observation and injection).
            status = TaskStatus.RETRYING
            retries = state.retries
        elif unresolved:
            status = TaskStatus.RUNNING
            retries = state.retries
        else:
            status = TaskStatus.RUNNING
            retries = state.retries
        return TaskState(
            task_id=contract.task_id,
            status=status,
            step=state.step,
            retries=retries,
            completed_assertions=completed,
            failed_assertions=failed,
            plan_step=state.plan_step,
        )
