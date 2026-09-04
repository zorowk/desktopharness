"""Qwen-CUA adapter that emits exactly one canonical ActionProposal."""

from __future__ import annotations

import json
from typing import Any

from ...core.models import (
    Action,
    ActionProposal,
    ActionType,
    CanonicalSnapshot,
    ModelContext,
    Point,
    new_id,
    to_primitive,
)
from ...core.store import ObjectStore
from ...qwen_actions import parse_qwen_actions


_ACTION_TYPES = {
    "moveTo": ActionType.POINTER_MOVE,
    "click": ActionType.POINTER_CLICK,
    "rightClick": ActionType.POINTER_CLICK,
    "middleClick": ActionType.POINTER_CLICK,
    "doubleClick": ActionType.POINTER_DOUBLE_CLICK,
    "dragTo": ActionType.POINTER_DRAG,
    "scroll": ActionType.POINTER_SCROLL,
    "press": ActionType.KEYBOARD_KEY,
    "hotkey": ActionType.KEYBOARD_SHORTCUT,
    "typewrite": ActionType.KEYBOARD_TEXT,
    "write": ActionType.KEYBOARD_TEXT,
    "done": ActionType.DONE,
}


class QwenCUAProposalProvider:
    provider_id = "qwen-cua"

    def __init__(self, backend: Any, object_store: ObjectStore) -> None:
        self._backend = backend
        self._store = object_store

    def propose(self, context: ModelContext) -> ActionProposal:
        if context.frame is None:
            raise RuntimeError("Qwen-CUA requires a frame")
        screenshot = self._store.require(context.frame.image_ref)
        instruction = self._instruction(context)
        result = self._backend.predict(
            instruction,
            screenshot,
            context.task_id,
            image_mime="image/png",
            client_step=context.current_step + 1,
            session_instruction=context.goal,
        )
        parsed = parse_qwen_actions(result.get("actions", []))
        if len(parsed) != 1:
            raise ValueError("Qwen-CUA v2 must return exactly one action")
        debug_ref = self._store.put(result, prefix="model-output")
        action = self._canonical_action(parsed[0], context)
        return ActionProposal(
            proposal_id=new_id("proposal"),
            source="qwen-cua",
            based_on_snapshot=context.based_on_snapshot,
            action=action,
            semantic_intent=None,
            expected_effect={},
            debug_ref=debug_ref,
        )

    def record_execution(self, task_id: str, receipt: Any) -> object:
        recorder = getattr(self._backend, "record_execution", None)
        if not callable(recorder):
            return {"ok": False, "message": "execution feedback unsupported"}
        if receipt.status.value == "delivered" and getattr(receipt.executed_action, "type", None) == ActionType.DONE:
            status = "partial"
            reason = "DONE triggers evidence collection; task completion is not established"
        else:
            status = {
                "delivered": "success",
                "rejected": "rejected",
                "failed": "error",
                "unknown": "partial",
            }[receipt.status.value]
            reason = receipt.error_code
        return recorder(
            task_id,
            status=status,
            execution=to_primitive(receipt),
            reason=reason,
        )

    def reset(self, task_id: str) -> None:
        resetter = getattr(self._backend, "reset", None)
        if callable(resetter):
            resetter(task_id)

    @staticmethod
    def _instruction(context: ModelContext) -> str:
        projection = {
            "goal": context.goal,
            "current_step": context.current_step,
            "pending_assertions": context.pending_assertions,
            "verified_facts": context.verified_facts,
            "spatial_projection": context.spatial_projection,
            "recent_execution_receipt": context.recent_execution_receipt,
            "assertion_feedback": context.assertion_feedback,
            "constraints": context.constraints,
        }
        return "Use the screenshot and this controller context. Return one action only.\n" + json.dumps(
            to_primitive(projection), ensure_ascii=False
        )

    def _canonical_action(self, parsed: dict[str, Any], context: ModelContext) -> Action:
        source_type = str(parsed.get("type"))
        action_type = _ACTION_TYPES.get(source_type)
        if action_type is None:
            raise ValueError(f"unsupported Qwen action in v2: {source_type}")
        coordinate = parsed.get("coordinate")
        desktop_point = None
        if coordinate is not None:
            snapshot: CanonicalSnapshot = self._store.require(context.based_on_snapshot)
            width, height = context.frame.pixel_size
            bounds = snapshot.coordinate_space.bounds
            desktop_point = Point(
                bounds.x + float(coordinate["x"]) * bounds.width / width,
                bounds.y + float(coordinate["y"]) * bounds.height / height,
            )
        args = parsed.get("args", [])
        kwargs = dict(parsed.get("kwargs", {}))
        parameters: dict[str, Any] = {}
        if action_type in {ActionType.POINTER_CLICK, ActionType.POINTER_DOUBLE_CLICK, ActionType.POINTER_DRAG}:
            parameters = {key: kwargs[key] for key in ("button", "duration") if key in kwargs}
            if source_type == "rightClick":
                parameters.setdefault("button", "right")
            elif source_type == "middleClick":
                parameters.setdefault("button", "middle")
        elif action_type == ActionType.POINTER_SCROLL:
            parameters = {"clicks": kwargs.get("clicks", args[0] if args else 0)}
        elif action_type == ActionType.KEYBOARD_KEY:
            parameters = {"key": kwargs.get("key", args[0] if args else None)}
        elif action_type == ActionType.KEYBOARD_SHORTCUT:
            parameters = {"keys": list(args)}
        elif action_type == ActionType.KEYBOARD_TEXT:
            parameters = {
                "text": kwargs.get("message", kwargs.get("text", args[0] if args else "")),
                "interval": kwargs.get("interval", 0),
            }
        return Action(
            type=action_type,
            coordinate=desktop_point,
            coordinate_space="desktop-logical" if desktop_point is not None else None,
            parameters=parameters,
        )
