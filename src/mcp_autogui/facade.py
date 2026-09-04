"""Compact MCP-facing facade for the v2 core."""

from __future__ import annotations

from typing import Any

from .core.models import (
    Action,
    ActionProposal,
    ActionType,
    AssertionSpec,
    Point,
    PolicyStatus,
    TaskContract,
    TaskLimits,
    TaskPermissions,
    TaskStatus,
    new_id,
    to_primitive,
)
from .core.orchestrator import CoreOrchestrator, response_envelope


_ACTION_ALIASES = {
    "keyboard.text_input": "keyboard.text",
    "keyboard.keys": "keyboard.key",
    "keyboard.shortcuts": "keyboard.shortcut",
}


class GuiRunFacade:
    def __init__(self, runtime: CoreOrchestrator) -> None:
        self.runtime = runtime

    def handle(self, operation: str, **kwargs: Any) -> dict[str, Any]:
        try:
            return self._handle(operation, **kwargs)
        except KeyError as exc:
            return response_envelope(
                operation,
                "failed",
                error={
                    "code": "OBJECT_NOT_FOUND",
                    "message": str(exc),
                    "retry": False,
                    "required_action": "describe-or-create-task",
                },
            )
        except (ValueError, PermissionError, RuntimeError) as exc:
            message = str(exc)
            if "SNAPSHOT_UNAVAILABLE" in message:
                code, retry, required = "SNAPSHOT_UNAVAILABLE", True, "capture-new-frame"
            elif "provider is unavailable" in message or "CAPABILITY_UNAVAILABLE" in message:
                code, retry, required = "CAPABILITY_UNAVAILABLE", False, "install-or-configure-provider"
            elif "unsupported gui_run operation" in message:
                code, retry, required = "UNSUPPORTED_OPERATION", False, "call-describe"
            else:
                code, retry, required = "CONTROLLER_TASK_CONTRACT_INVALID", False, "correct-request"
            return response_envelope(
                operation,
                "failed",
                error={"code": code, "message": message, "retry": retry, "required_action": required},
            )

    def _handle(
        self,
        operation: str,
        *,
        task_id: str = "",
        task_contract: dict[str, Any] | None = None,
        proposal: dict[str, Any] | None = None,
        proposal_id: str = "",
        confirmed: bool = False,
        strategy: str = "compact",
        object_ref: str = "",
        diagnostic: bool = False,
    ) -> dict[str, Any]:
        operation = operation.strip().lower()
        operation = {"assess": "decide", "verify": "evaluate"}.get(operation, operation)
        if operation == "describe":
            description = {
                "protocol_version": 2,
                "schema_version": "1",
                "adapter": to_primitive(self.runtime.compositor.descriptor),
                "capabilities": {
                    "pointer": self.runtime.executor is not None,
                    "keyboard": self.runtime.executor is not None,
                    "window_geometry": self.runtime.compositor.descriptor.capabilities.desktop_geometry,
                    "frame": self.runtime.frame_provider is not None,
                    "child_control_semantics": self.runtime.compositor.descriptor.capabilities.child_controls,
                },
                "providers": {
                    "proposal": _component_id(self.runtime.proposal_provider, "provider_id"),
                    "frame": _component_id(self.runtime.frame_provider, "provider_id"),
                    "policy": [
                        _component_id(item, "provider_id") for item in self.runtime.policy_providers
                    ],
                    "evidence": [
                        {"provider_id": item.provider_id, "fact_paths": sorted(item.fact_paths)}
                        for item in self.runtime.evidence_providers
                    ],
                    "executor": _component_id(self.runtime.executor, "executor_id"),
                    "application_launcher": _component_id(
                        self.runtime.application_launcher, "launcher_id"
                    ),
                },
                "actions": [item.value for item in ActionType],
                "operations": ["describe", "observe", "propose", "decide", "execute", "evaluate", "status", "reset", "trace"],
                "policy_profiles": sorted(self.runtime.gate.policy_profiles),
            }
            ref = self.runtime.store.put(description, prefix="description")
            return self._response("describe", "ok", ref, diagnostic)

        if operation == "trace" and object_ref:
            value = self.runtime.store.require(object_ref)
            if isinstance(value, bytes):
                expanded = {"type": "binary-artifact", "size": len(value)}
            else:
                expanded = to_primitive(value)
            return {
                "protocol_version": 2,
                "operation": "trace",
                "status": "ok",
                "object_ref": object_ref,
                "object": expanded,
            }

        resolved_task = task_id.strip() or str((task_contract or {}).get("task_id") or "").strip()
        if not resolved_task:
            raise ValueError("task_id is required")
        if task_contract is not None:
            parsed_contract = parse_task_contract(task_contract)
            if parsed_contract.task_id != resolved_task:
                raise ValueError("task_id does not match task_contract.task_id")
            if self.runtime.has_task(resolved_task):
                if self.runtime.task_contract(resolved_task) != parsed_contract:
                    raise ValueError("task_contract cannot change after task creation")
            else:
                self.runtime.register_task(parsed_contract)

        if operation == "observe":
            value = self.runtime.observe(resolved_task)
            return self._response(operation, "ok", value.snapshot_id, diagnostic)
        if operation == "propose":
            if proposal is None:
                value = self.runtime.propose(resolved_task, strategy=strategy)
            else:
                current = self.runtime.latest_snapshot(resolved_task) or self.runtime.observe(resolved_task)
                value = self.runtime.submit_proposal(
                    resolved_task, parse_action_proposal(proposal, current.snapshot_id)
                )
            return self._response(operation, "needs-execution", value.proposal_id, diagnostic)
        if operation == "decide":
            value = self.runtime.decide(proposal_id.strip())
            ref = self._last_object_ref(resolved_task, "decision.created")
            status = {
                PolicyStatus.ALLOW: "needs-execution",
                PolicyStatus.CONFIRM: "needs-confirmation",
                PolicyStatus.DENY: "refused",
                PolicyStatus.INVALID: "refused",
                PolicyStatus.STALE: "refused",
            }[value.status]
            return self._response(operation, status, ref, diagnostic)
        if operation == "execute":
            value = self.runtime.execute(proposal_id.strip(), confirmed=confirmed)
            if value.error_code == "CONFIRMATION_REQUIRED":
                status = "needs-confirmation"
            else:
                status = "needs-evidence" if value.status.value == "delivered" else "refused" if value.status.value == "rejected" else "failed"
            response = self._response(operation, status, value.execution_id, diagnostic)
            if value.error_code and value.error_code != "CONFIRMATION_REQUIRED":
                response["error"] = {
                    "code": value.error_code,
                    "message": "The proposed action was not delivered",
                    "retry": value.error_code not in {"MECHANICAL_PERMISSION_DENIED", "SEMANTIC_POLICY_DENIED"},
                    "required_action": "capture-new-frame" if value.error_code.endswith("CHANGED") else "review-policy",
                }
            return response
        if operation == "evaluate":
            _, _, state = self.runtime.evaluate(resolved_task)
            ref = self._last_object_ref(resolved_task, "task.transitioned")
            status = {
                TaskStatus.COMPLETED: "completed",
                TaskStatus.FAILED: "failed",
                TaskStatus.NEEDS_EVIDENCE: "needs-evidence",
                TaskStatus.RETRY: "partial",
                TaskStatus.CONTINUE: "ok",
            }[state.status]
            return self._response(operation, status, ref, diagnostic)
        if operation == "status":
            state = self.runtime.status(resolved_task)
            ref = self.runtime.store.put(state, prefix="task-state")
            return self._response(operation, state.status.value, ref, diagnostic)
        if operation == "trace":
            return {
                "protocol_version": 2,
                "operation": "trace",
                "status": "ok",
                "events": [to_primitive(item) for item in self.runtime.ledger.events(resolved_task)],
            }
        if operation == "reset":
            self.runtime.reset(resolved_task)
            return response_envelope("reset", "ok")
        raise ValueError(f"unsupported gui_run operation: {operation}")

    def _response(self, operation: str, status: str, ref: str, diagnostic: bool) -> dict[str, Any]:
        response = response_envelope(operation, status, object_ref=ref)
        if diagnostic:
            response["object"] = to_primitive(self.runtime.store.require(ref))
        return response

    def _last_object_ref(self, task_id: str, event_type: str) -> str:
        return next(
            event.object_ref
            for event in reversed(self.runtime.ledger.events(task_id))
            if event.event_type == event_type
        )


def parse_task_contract(value: dict[str, Any]) -> TaskContract:
    if not isinstance(value, dict):
        raise ValueError("task_contract must be an object")
    permissions = value.get("permissions") or {}
    actions = frozenset(
        ActionType(_ACTION_ALIASES.get(str(item), str(item)))
        for item in permissions.get("actions", [])
    )
    assertions = tuple(
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
        for item in value.get("assertions", [])
    )
    limits = value.get("limits") or {}
    return TaskContract(
        task_id=str(value.get("task_id") or "").strip(),
        goal=str(value.get("goal") or "").strip(),
        permissions=TaskPermissions(
            actions,
            frozenset(str(item) for item in permissions.get("semantic_intents", [])),
        ),
        assertions=assertions,
        limits=TaskLimits(int(limits.get("max_steps", 10)), int(limits.get("max_retries", 1))),
        policy_profile=str(value.get("policy_profile") or "desktop-safe-default"),
        verification_profile=str(value.get("verification_profile") or "default"),
        policy_overrides=dict(value.get("policy_overrides") or {}),
    )


def _component_id(component: Any, attribute: str) -> str | None:
    if component is None:
        return None
    return str(getattr(component, attribute, type(component).__name__))


def parse_action_proposal(value: dict[str, Any], default_snapshot: str) -> ActionProposal:
    if not isinstance(value, dict):
        raise ValueError("proposal must be an object")
    action_value = value.get("action") or {}
    action_type = ActionType(_ACTION_ALIASES.get(str(action_value.get("type")), str(action_value.get("type"))))
    coordinate_value = action_value.get("coordinate")
    point = None
    space = None
    if coordinate_value is not None:
        if not isinstance(coordinate_value, dict):
            raise ValueError("action.coordinate must be an object")
        point = Point(float(coordinate_value["x"]), float(coordinate_value["y"]))
        space = str(coordinate_value.get("space") or action_value.get("coordinate_space") or "")
    parameters = dict(action_value.get("parameters") or {})
    for key, item in action_value.items():
        if key not in {"type", "coordinate", "coordinate_space", "parameters"}:
            parameters.setdefault(key, item)
    return ActionProposal(
        proposal_id=str(value.get("proposal_id") or new_id("proposal")),
        source=str(value.get("source") or "controller"),
        based_on_snapshot=str(value.get("based_on_snapshot") or default_snapshot),
        action=Action(action_type, point, space, parameters),
        semantic_intent=value.get("semantic_intent"),
        expected_effect=dict(value.get("expected_effect") or {}),
        debug_ref=value.get("debug_ref"),
    )
