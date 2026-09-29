"""Proposal and executor transaction facts."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Mapping

from .desktop import Point
from .protocol import ReasonCode, SCHEMA_VERSION


class ActionType(StrEnum):
    WAIT = "wait"
    POINTER_MOVE = "pointer.move"
    POINTER_CLICK = "pointer.click"
    POINTER_DOUBLE_CLICK = "pointer.double_click"
    POINTER_DRAG = "pointer.drag"
    POINTER_SCROLL = "pointer.scroll"
    KEYBOARD_KEY = "keyboard.key"
    KEYBOARD_SHORTCUT = "keyboard.shortcut"
    KEYBOARD_TEXT = "keyboard.text"
    PLATFORM_INVOKE = "platform.invoke"
    APPLICATION_LAUNCH = "application.launch"
    DONE = "done"


class ExecutionStatus(StrEnum):
    DELIVERED = "delivered"
    FAILED = "failed"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class Action:
    type: ActionType
    coordinate: Point | None = None
    coordinate_space: str | None = None
    parameters: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        coordinate_actions = {
            ActionType.POINTER_MOVE,
            ActionType.POINTER_CLICK,
            ActionType.POINTER_DOUBLE_CLICK,
            ActionType.POINTER_DRAG,
        }
        if self.type in coordinate_actions and self.coordinate is None:
            raise ValueError(f"{self.type.value} requires a coordinate")
        if self.coordinate is not None and not self.coordinate_space:
            raise ValueError("coordinate actions must declare coordinate_space")


@dataclass(frozen=True, slots=True)
class ActionProposal:
    proposal_id: str
    source: str
    based_on_snapshot: str
    actions: tuple[Action, ...]
    claimed_intent: str | None = None
    debug_ref: str | None = None
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        if not self.actions:
            raise ValueError("proposal.actions must not be empty")

    @property
    def action_sequence(self) -> tuple[Action, ...]:
        """Return the ordered actions proposed for one execution boundary."""
        return self.actions


@dataclass(frozen=True, slots=True)
class AtomicActionReceipt:
    action_index: int
    action: Action
    status: ExecutionStatus
    started_at: str
    finished_at: str
    executor_execution_id: str | None = None
    error_code: ReasonCode | None = None
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.action_index < 0:
            raise ValueError("action_index must not be negative")
        if self.error_code is not None and not isinstance(self.error_code, ReasonCode):
            raise TypeError("AtomicActionReceipt.error_code must be a ReasonCode or None")


@dataclass(frozen=True, slots=True)
class ExecutionReceipt:
    execution_id: str
    proposal_id: str
    status: ExecutionStatus
    executed_action: Action | None
    started_at: str
    finished_at: str
    error_code: ReasonCode | None = None
    debug_ref: str | None = None
    executed_actions: tuple[Action, ...] = ()
    action_receipts: tuple[AtomicActionReceipt, ...] = ()
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.error_code is not None and not isinstance(self.error_code, ReasonCode):
            raise TypeError("ExecutionReceipt.error_code must be a ReasonCode or None")
        if self.executed_actions and self.executed_action != self.executed_actions[0]:
            raise ValueError("executed_action must equal the first executed action")
