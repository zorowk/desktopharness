"""In-memory indexes for active task transactions."""

from __future__ import annotations

from threading import RLock
from typing import Callable

from .desktop import CanonicalSnapshot
from .evidence import AssertionResult
from .task import TaskContract, TaskState
from .transaction import ActionProposal, ExecutionReceipt


class TaskRepository:
    """Own runtime-only task indexes; persisted facts remain in the audit store."""

    def __init__(self) -> None:
        self._lock = RLock()
        self._contracts: dict[str, TaskContract] = {}
        self._states: dict[str, TaskState] = {}
        self._latest_snapshots: dict[str, CanonicalSnapshot] = {}
        self._snapshots: dict[str, CanonicalSnapshot] = {}
        self._snapshot_tasks: dict[str, str] = {}
        self._proposal_tasks: dict[str, str] = {}
        self._proposals: dict[str, ActionProposal] = {}
        self._provider_proposals: set[str] = set()
        self._provider_finalized: set[str] = set()
        self._latest_receipts: dict[str, ExecutionReceipt] = {}
        self._terminal_receipts: dict[str, ExecutionReceipt] = {}
        self._latest_results: dict[str, tuple[AssertionResult, ...]] = {}

    def register(self, contract: TaskContract) -> TaskState:
        with self._lock:
            if contract.task_id in self._contracts:
                raise ValueError(f"task already exists: {contract.task_id}")
            state = TaskState(task_id=contract.task_id)
            self._contracts[contract.task_id] = contract
            self._states[contract.task_id] = state
            return state

    def has_task(self, task_id: str) -> bool:
        return task_id in self._contracts

    def contract(self, task_id: str) -> TaskContract:
        try:
            return self._contracts[task_id]
        except KeyError as exc:
            raise KeyError(f"unknown task: {task_id}") from exc

    def state(self, task_id: str) -> TaskState:
        with self._lock:
            self.contract(task_id)
            return self._states[task_id]

    def set_state(self, state: TaskState) -> None:
        self.update_state(state.task_id, lambda _current: state)

    def update_state(
        self, task_id: str, update: Callable[[TaskState], TaskState]
    ) -> TaskState:
        with self._lock:
            self.contract(task_id)
            state = update(self._states[task_id])
            if state.task_id != task_id:
                raise ValueError("state update must preserve task_id")
            self._states[task_id] = state
            return state

    def snapshot(self, task_id: str) -> CanonicalSnapshot | None:
        return self._latest_snapshots.get(task_id)

    def set_snapshot(self, task_id: str, snapshot: CanonicalSnapshot) -> None:
        self._latest_snapshots[task_id] = snapshot
        self._snapshots[snapshot.snapshot_id] = snapshot
        self._snapshot_tasks[snapshot.snapshot_id] = task_id

    def snapshot_by_id(self, snapshot_id: str) -> CanonicalSnapshot | None:
        return self._snapshots.get(snapshot_id)

    def latest_receipt(self, task_id: str) -> ExecutionReceipt | None:
        return self._latest_receipts.get(task_id)

    def recent_results(self, task_id: str) -> tuple[AssertionResult, ...]:
        return self._latest_results.get(task_id, ())

    def set_results(self, task_id: str, results: tuple[AssertionResult, ...]) -> None:
        self._latest_results[task_id] = results

    def submit_proposal(
        self,
        task_id: str,
        proposal: ActionProposal,
        *,
        provider_owned: bool = False,
    ) -> None:
        with self._lock:
            if proposal.proposal_id in self._proposal_tasks:
                raise ValueError("proposal already submitted")
            self._proposal_tasks[proposal.proposal_id] = task_id
            self._proposals[proposal.proposal_id] = proposal
            if provider_owned:
                self._provider_proposals.add(proposal.proposal_id)

    def proposal(self, proposal_id: str) -> ActionProposal:
        try:
            return self._proposals[proposal_id]
        except KeyError as exc:
            raise KeyError(proposal_id) from exc

    def task_for_proposal(self, proposal_id: str) -> str | None:
        return self._proposal_tasks.get(proposal_id)

    def provider_owns(self, proposal_id: str) -> bool:
        return proposal_id in self._provider_proposals

    def finalized(self, proposal_id: str) -> bool:
        return proposal_id in self._provider_finalized

    def finalize(self, proposal_id: str) -> None:
        self._provider_finalized.add(proposal_id)

    def terminal_receipt(self, proposal_id: str) -> ExecutionReceipt | None:
        return self._terminal_receipts.get(proposal_id)

    def record_receipt(self, task_id: str, receipt: ExecutionReceipt, *, terminal: bool) -> None:
        with self._lock:
            self._latest_receipts[task_id] = receipt
            if terminal:
                self._terminal_receipts[receipt.proposal_id] = receipt

    def clear_task(self, task_id: str) -> None:
        with self._lock:
            self._clear_task(task_id)

    def _clear_task(self, task_id: str) -> None:
        self._contracts.pop(task_id, None)
        self._states.pop(task_id, None)
        self._latest_snapshots.pop(task_id, None)
        self._latest_receipts.pop(task_id, None)
        self._latest_results.pop(task_id, None)
        for proposal_id, owner in tuple(self._proposal_tasks.items()):
            if owner != task_id:
                continue
            self._proposal_tasks.pop(proposal_id, None)
            self._proposals.pop(proposal_id, None)
            self._provider_proposals.discard(proposal_id)
            self._provider_finalized.discard(proposal_id)
            self._terminal_receipts.pop(proposal_id, None)
        for snapshot_id, owner in tuple(self._snapshot_tasks.items()):
            if owner == task_id:
                self._snapshot_tasks.pop(snapshot_id, None)
                self._snapshots.pop(snapshot_id, None)
