"""Task contract and lifecycle state."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Mapping

from .protocol import SCHEMA_VERSION


class TaskStatus(StrEnum):
    RUNNING = "running"
    RETRYING = "retrying"
    COMPLETED = "completed"
    DELIVERED_UNVERIFIED = "delivered-unverified"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class AssertionSpec:
    assertion_id: str
    path: str
    operator: str
    expected: Any = None
    required: bool = True
    recoverable: bool = True
    subject: Mapping[str, Any] = field(default_factory=dict)
    providers: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class TaskLimits:
    max_steps: int = 10
    max_retries: int = 1

    def __post_init__(self) -> None:
        if self.max_steps < 1 or self.max_retries < 0:
            raise ValueError("invalid task limits")


@dataclass(frozen=True, slots=True)
class TaskContract:
    task_id: str
    goal: str
    assertions: tuple[AssertionSpec, ...] = ()
    limits: TaskLimits = field(default_factory=TaskLimits)
    verification_profile: str = "default"
    steps: tuple[str, ...] = ()
    step_assertions: tuple[tuple[AssertionSpec, ...], ...] = ()
    schema_version: str = SCHEMA_VERSION


@dataclass(frozen=True, slots=True)
class TaskState:
    task_id: str
    status: TaskStatus = TaskStatus.RUNNING
    step: int = 0
    retries: int = 0
    completed_assertions: tuple[str, ...] = ()
    failed_assertions: tuple[str, ...] = ()
    plan_step: int = 0
    schema_version: str = SCHEMA_VERSION
