"""Compact MCP-facing facade for the v2 core."""

from __future__ import annotations

from typing import Any

from .core.desktop import Point
from .core.protocol import OperationFailure, ReasonCode, new_id, to_primitive
from .core.proposal_validator import ValidationFailure
from .core.task import AssertionSpec, TaskContract, TaskLimits, TaskStatus
from .core.transaction import (
    Action,
    ActionProposal,
    ActionType,
    ExecutionReceipt,
    ExecutionStatus,
)
from .core.orchestrator import CoreOrchestrator
from .protocol_response import diagnostic_response, reduce_public_response
from .runtime_description import RuntimeDescription


_PUBLIC_OPERATIONS = frozenset({"describe", "run", "status", "reset"})
_DIAGNOSTIC_OPERATIONS = frozenset(
    {"describe", "observe", "propose", "prepare", "execute", "evaluate", "trace"}
)


class AutoUIFacade:
    def __init__(
        self,
        runtime: CoreOrchestrator,
        runtime_description: RuntimeDescription,
    ) -> None:
        self.runtime = runtime
        self._runtime_description = runtime_description

    def handle(self, operation: str, **kwargs: Any) -> dict[str, Any]:
        """Handle the compact task-lifecycle API."""
        normalized = _normalize_operation(operation)
        if normalized not in _PUBLIC_OPERATIONS:
            return self._public_failure(
                normalized,
                ReasonCode.UNSUPPORTED_OPERATION,
                "unsupported public gui_run operation; use gui_diagnostic for controller internals",
                "call-run-status-or-reset",
            )
        try:
            return self._with_recording_status(
                self._handle_public(normalized, **kwargs)
            )
        except KeyError as exc:
            return self._public_failure(
                normalized, ReasonCode.OBJECT_NOT_FOUND, str(exc), "describe-or-create-task"
            )
        except OperationFailure as exc:
            return self._public_failure(
                normalized,
                exc.reason_code,
                str(exc),
                exc.required_action,
                retry=exc.retry,
            )
        except (ValueError, PermissionError, RuntimeError) as exc:
            return self._public_failure(
                normalized,
                ReasonCode.CONTROLLER_TASK_CONTRACT_INVALID,
                str(exc),
                "correct-request",
                debug_ref=getattr(exc, "debug_ref", None),
            )

    def handle_diagnostic(self, operation: str, **kwargs: Any) -> dict[str, Any]:
        """Handle explicit controller diagnostics without reducing their facts."""
        normalized = _normalize_operation(operation)
        if normalized not in _DIAGNOSTIC_OPERATIONS:
            return diagnostic_response(
                normalized,
                "failed",
                error={
                    "code": ReasonCode.UNSUPPORTED_OPERATION,
                    "message": "unsupported gui_diagnostic operation",
                    "retry": False,
                    "required_action": (
                        "call-describe-observe-propose-prepare-execute-evaluate-or-trace"
                    ),
                },
            )
        try:
            return self._with_recording_status(
                self._handle_diagnostic(normalized, **kwargs)
            )
        except KeyError as exc:
            return diagnostic_response(
                normalized,
                "failed",
                error={"code": ReasonCode.OBJECT_NOT_FOUND, "message": str(exc)},
            )
        except OperationFailure as exc:
            return diagnostic_response(
                normalized,
                "failed",
                error={
                    "code": exc.reason_code,
                    "message": str(exc),
                    "retry": exc.retry,
                    "required_action": exc.required_action,
                },
            )
        except (ValueError, PermissionError, RuntimeError) as exc:
            return diagnostic_response(
                normalized,
                "failed",
                error={"code": ReasonCode.CONTROLLER_TASK_CONTRACT_INVALID, "message": str(exc)},
            )

    def _handle_public(
        self,
        operation: str,
        *,
        task_id: str = "",
        task_contract: dict[str, Any] | None = None,
        proposal_id: str = "",
        strategy: str = "compact",
        max_iterations: int | None = None,
        intent: str = "",
    ) -> dict[str, Any]:
        if operation == "describe":
            description = self._description()
            reference = (
                self.runtime.store_runtime_object(description, prefix="description")
                if self.runtime.diagnostic_enabled
                else None
            )
            response = reduce_public_response(
                operation,
                task_state=None,
                object_ref=reference,
            )
            response["object"] = description
            return response

        resolved_task = self._prepare_task(task_id, task_contract)
        if operation == "run":
            value = self.runtime.run(
                resolved_task,
                strategy=strategy,
                max_iterations=max_iterations,
                intent=intent,
            )
            ref = (
                self.runtime.store_runtime_object(
                    value, task_id=resolved_task, prefix="run-result"
                )
                if self.runtime.diagnostic_enabled
                else None
            )
            return reduce_public_response(
                operation,
                task_state=value["state"].status.value,
                object_ref=ref,
                retry=value.get("retry"),
            )
        if operation == "status":
            state = self.runtime.status(resolved_task)
            ref = (
                self.runtime.store_runtime_object(
                    state, task_id=resolved_task, prefix="task-state"
                )
                if self.runtime.diagnostic_enabled
                else None
            )
            return reduce_public_response(
                operation,
                task_state=state.status.value,
                object_ref=ref,
            )
        if operation == "reset":
            self.runtime.reset(resolved_task)
            return reduce_public_response(operation, task_state=None)
        raise ValueError(f"unsupported gui_run operation: {operation}")

    def _handle_diagnostic(
        self,
        operation: str,
        *,
        task_id: str = "",
        task_contract: dict[str, Any] | None = None,
        proposal: dict[str, Any] | None = None,
        proposal_id: str = "",
        strategy: str = "compact",
        object_ref: str = "",
        max_iterations: int | None = None,
    ) -> dict[str, Any]:
        if operation == "describe":
            return self._diagnostic_response(
                operation,
                "ok",
                self._store_description(),
            )
        if operation == "trace" and object_ref:
            value = self.runtime.store.require(object_ref)
            expanded = (
                {"type": "binary-artifact", "size": len(value)}
                if isinstance(value, bytes)
                else to_primitive(value)
            )
            return {
                "protocol_version": 2,
                "operation": "trace",
                "status": "ok",
                "object_ref": object_ref,
                "object": expanded,
            }
        if operation == "trace":
            resolved_task = task_id.strip()
            if not resolved_task:
                raise ValueError("task_id is required")
            events = self.runtime.ledger.events(resolved_task)
            if not events:
                raise KeyError(f"unknown task: {resolved_task}")
            return {
                "protocol_version": 2,
                "operation": "trace",
                "status": "ok",
                "events": [to_primitive(item) for item in events],
                "attributions": [
                    to_primitive(item)
                    for item in self.runtime.attributions(resolved_task)
                ],
            }

        resolved_task = self._prepare_task(task_id, task_contract)
        if operation == "observe":
            value = self.runtime.observe(resolved_task)
            return self._diagnostic_response(
                operation, "ok", value.snapshot_id, resolved_task
            )
        if operation == "propose":
            if proposal is None:
                value = self.runtime.propose(resolved_task, strategy=strategy)
            else:
                current = (
                    self.runtime.latest_snapshot(resolved_task)
                    or self.runtime.observe(resolved_task)
                )
                value = self.runtime.submit_proposal(
                    resolved_task,
                    parse_action_proposal(proposal, current.snapshot_id),
                )
            return self._diagnostic_response(
                operation,
                TaskStatus.RUNNING.value,
                value.proposal_id,
                resolved_task,
            )
        if operation == "prepare":
            value = self.runtime.prepare(proposal_id.strip())
            if isinstance(value, ValidationFailure):
                return diagnostic_response(
                    operation,
                    self.runtime.status(resolved_task).status.value,
                    error=_validation_error(value),
                    retry=_recovery_for(value.reason_code),
                )
            return diagnostic_response(operation, TaskStatus.RUNNING.value)
        if operation == "execute":
            value = self.runtime.execute(proposal_id.strip())
            if isinstance(value, ValidationFailure):
                return diagnostic_response(
                    operation,
                    self.runtime.status(resolved_task).status.value,
                    error=_validation_error(value),
                    retry=_recovery_for(value.reason_code),
                )
            response = self._diagnostic_response(
                operation,
                _diagnostic_receipt_status(value),
                value.execution_id,
                resolved_task,
            )
            error, retry = _execution_failure(value)
            if error is not None:
                response["error"] = error
                response["retry"] = retry
            return response
        if operation == "evaluate":
            _, _, state = self.runtime.evaluate(resolved_task)
            ref = self._last_object_ref(resolved_task, "task.transitioned")
            return self._diagnostic_response(
                operation,
                state.status.value,
                ref,
                resolved_task,
            )
        raise ValueError(f"unsupported gui_diagnostic operation: {operation}")

    def _prepare_task(
        self,
        task_id: str,
        task_contract: dict[str, Any] | None,
    ) -> str:
        resolved_task = (
            task_id.strip()
            or str((task_contract or {}).get("task_id") or "").strip()
        )
        if not resolved_task:
            raise ValueError("task_id is required")
        if task_contract is None:
            return resolved_task
        parsed_contract = parse_task_contract(task_contract)
        if parsed_contract.task_id != resolved_task:
            raise ValueError("task_id does not match task_contract.task_id")
        if self.runtime.has_task(resolved_task):
            if self.runtime.task_contract(resolved_task) != parsed_contract:
                raise ValueError("task_contract cannot change after task creation")
        else:
            self.runtime.register_task(parsed_contract)
        return resolved_task

    def _store_description(self) -> str:
        return self.runtime.store_runtime_object(
            self._description(), prefix="description"
        )

    def _description(self) -> dict[str, Any]:
        description = self._runtime_description.to_dict()
        description["actions"] = [item.value for item in ActionType]
        description["operations"] = sorted(_PUBLIC_OPERATIONS)
        description["diagnostic_operations"] = (
            sorted(_DIAGNOSTIC_OPERATIONS)
            if self.runtime.diagnostic_enabled
            else []
        )
        return description

    def _diagnostic_response(
        self,
        operation: str,
        status: str,
        ref: str,
        task_id: str | None = None,
    ) -> dict[str, Any]:
        response = diagnostic_response(operation, status, object_ref=ref)
        response["task_id"] = task_id
        response["task_state"] = (
            self.runtime.status(task_id).status.value if task_id is not None else None
        )
        response["object"] = to_primitive(self.runtime.store.require(ref))
        if task_id is not None:
            response["attribution_refs"] = [
                event.object_ref
                for event in self.runtime.ledger.events(task_id)
                if event.event_type == "attribution.recorded"
            ]
        return response

    def _public_failure(
        self,
        operation: str,
        code: ReasonCode,
        message: str,
        required_action: str,
        *,
        retry: bool = False,
        debug_ref: str | None = None,
    ) -> dict[str, Any]:
        response = reduce_public_response(
            operation,
            task_state=None,
            error={
                "code": code,
                "message": message,
                "retry": retry,
                "required_action": required_action,
            },
        )
        if debug_ref and self.runtime.diagnostic_enabled:
            response["debug_ref"] = debug_ref
        return response

    def _last_object_ref(self, task_id: str, event_type: str) -> str:
        return next(
            event.object_ref
            for event in reversed(self.runtime.ledger.events(task_id))
            if event.event_type == event_type
        )

    def _with_recording_status(self, response: dict[str, Any]) -> dict[str, Any]:
        if self.runtime.recording_errors:
            response["recording"] = {
                "status": "failed",
                "message": self.runtime.recording_errors[-1],
                "retry": False,
            }
        return response


def _diagnostic_receipt_status(receipt: ExecutionReceipt) -> str:
    if receipt.status == ExecutionStatus.DELIVERED:
        return TaskStatus.RUNNING.value
    return TaskStatus.FAILED.value


def _execution_failure(
    receipt: ExecutionReceipt,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    if receipt.error_code is None:
        return None, None
    recovery = _recovery_for(receipt.error_code)
    return (
        {
            "code": receipt.error_code,
            "message": "The proposed action was not delivered",
            **recovery,
        },
        recovery,
    )


def _validation_error(failure: ValidationFailure) -> dict[str, Any]:
    recovery = _recovery_for(failure.reason_code)
    return {
        "code": failure.reason_code,
        "message": "The proposed action failed validation before input injection",
        **recovery,
    }


def parse_task_contract(value: dict[str, Any]) -> TaskContract:
    if not isinstance(value, dict):
        raise ValueError("task_contract must be an object")
    allowed = {
        "task_id", "goal", "steps", "step_assertions", "assertions", "limits",
        "verification_profile",
    }
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise ValueError(f"task_contract has unknown fields: {', '.join(unknown)}")
    def parse_assertions(items: Any) -> tuple[AssertionSpec, ...]:
        if not isinstance(items, list):
            raise ValueError("assertions must be a list")
        return tuple(
        AssertionSpec(
            assertion_id=str(item["assertion_id"]),
            path=str(item["path"]),
            operator=str(item["operator"]),
            expected=item.get("expected"),
            required=bool(item.get("required", True)),
            recoverable=bool(item.get("recoverable", True)),
            subject=dict(item.get("subject") or {}),
            providers=tuple(str(provider) for provider in item.get("providers", [])),
        )
            for item in items
        )
    assertions = parse_assertions(value.get("assertions", []))
    limits = value.get("limits") or {}
    raw_steps = value.get("steps", [])
    if not isinstance(raw_steps, list) or not all(
        isinstance(step, str) and step.strip() for step in raw_steps
    ):
        raise ValueError("task_contract.steps must be a list of non-empty strings")
    raw_step_assertions = value.get("step_assertions", [])
    if not isinstance(raw_step_assertions, list) or (
        raw_step_assertions and len(raw_step_assertions) != len(raw_steps)
    ):
        raise ValueError("task_contract.step_assertions must match steps length")
    step_assertions = tuple(parse_assertions(items) for items in raw_step_assertions)
    return TaskContract(
        task_id=str(value.get("task_id") or "").strip(),
        goal=str(value.get("goal") or "").strip(),
        assertions=assertions,
        limits=TaskLimits(int(limits.get("max_steps", 10)), int(limits.get("max_retries", 1))),
        verification_profile=str(value.get("verification_profile") or "default"),
        steps=tuple(step.strip() for step in raw_steps),
        step_assertions=step_assertions,
    )


def _normalize_operation(operation: str) -> str:
    return operation.strip().lower()


def _recovery_for(code: ReasonCode) -> dict[str, Any]:
    if code in {
        ReasonCode.COORDINATE_SPACE_CHANGED,
        ReasonCode.TARGET_DISAPPEARED,
        ReasonCode.TARGET_IDENTITY_CHANGED,
        ReasonCode.TARGET_GEOMETRY_INVALIDATED,
        ReasonCode.TARGET_OCCLUDED,
        ReasonCode.HIT_TEST_CHANGED,
        ReasonCode.CURSOR_ORIGIN_CHANGED,
    }:
        return {"retry": True, "required_action": "capture-new-frame"}
    if code == ReasonCode.CAPABILITY_UNAVAILABLE:
        return {"retry": False, "required_action": "install-or-configure-provider"}
    if code in {ReasonCode.ACTION_RESTRICTED, ReasonCode.INVALID_ACTION_PARAMETERS}:
        return {"retry": False, "required_action": "correct-proposal"}
    return {"retry": True, "required_action": "retry-execution"}


def parse_action_proposal(value: dict[str, Any], default_snapshot: str) -> ActionProposal:
    if not isinstance(value, dict):
        raise ValueError("proposal must be an object")
    def parse_action(action_value: Any) -> Action:
        if not isinstance(action_value, dict):
            raise ValueError("proposal action must be an object")
        requested_type = str(action_value.get("type"))
        action_type = ActionType(requested_type)
        coordinate_value = action_value.get("coordinate")
        point = None
        space = None
        if coordinate_value is not None:
            if not isinstance(coordinate_value, dict):
                raise ValueError("action.coordinate must be an object")
            point = Point(float(coordinate_value["x"]), float(coordinate_value["y"]))
            space = str(
                coordinate_value.get("space")
                or action_value.get("coordinate_space")
                or ""
            )
        parameters = dict(action_value.get("parameters") or {})
        for key, item in action_value.items():
            if key not in {"type", "coordinate", "coordinate_space", "parameters"}:
                parameters.setdefault(key, item)
        return Action(action_type, point, space, parameters)

    if "action" in value:
        raise ValueError("proposal.action is not supported; use proposal.actions")
    raw_actions = value.get("actions")
    if not isinstance(raw_actions, list) or not raw_actions:
        raise ValueError("proposal.actions must be a non-empty list")
    actions = tuple(parse_action(item) for item in raw_actions)
    claimed_intent = value.get("claimed_intent")
    if "semantic_intent" in value:
        raise ValueError("semantic_intent is not supported; use claimed_intent")
    if "expected_effect" in value:
        raise ValueError("expected_effect is not supported; define completion with task assertions")
    return ActionProposal(
        proposal_id=str(value.get("proposal_id") or new_id("proposal")),
        source=str(value.get("source") or "controller"),
        based_on_snapshot=str(value.get("based_on_snapshot") or default_snapshot),
        actions=actions,
        claimed_intent=claimed_intent,
        debug_ref=value.get("debug_ref"),
    )
