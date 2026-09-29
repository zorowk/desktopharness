"""Qwen-CUA adapter that emits one canonical, possibly multi-action Proposal."""

from __future__ import annotations

import json
from dataclasses import replace
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
from ...qwen_action_registry import V2_PARSED_QWEN_ACTIONS


_ACTION_TYPES = {
    "moveTo": ActionType.POINTER_MOVE,
    "click": ActionType.POINTER_CLICK,
    "rightClick": ActionType.POINTER_CLICK,
    "middleClick": ActionType.POINTER_CLICK,
    "doubleClick": ActionType.POINTER_DOUBLE_CLICK,
    "tripleClick": ActionType.POINTER_CLICK,
    "dragTo": ActionType.POINTER_DRAG,
    "moveRel": ActionType.POINTER_MOVE,
    "dragRel": ActionType.POINTER_DRAG,
    "scroll": ActionType.POINTER_SCROLL,
    "hscroll": ActionType.POINTER_SCROLL,
    "press": ActionType.KEYBOARD_KEY,
    "keyDown": ActionType.KEYBOARD_KEY,
    "keyUp": ActionType.KEYBOARD_KEY,
    "hotkey": ActionType.KEYBOARD_SHORTCUT,
    "typewrite": ActionType.KEYBOARD_TEXT,
    "write": ActionType.KEYBOARD_TEXT,
    "mouseDown": ActionType.POINTER_CLICK,
    "mouseUp": ActionType.POINTER_CLICK,
    "done": ActionType.DONE,
}
if frozenset(_ACTION_TYPES) != V2_PARSED_QWEN_ACTIONS:  # pragma: no cover - import-time contract
    raise RuntimeError("Qwen v2 action registry and canonical mapping diverged")


class QwenProposalError(ValueError):
    """A rejected model response whose raw diagnostic is available by reference."""

    def __init__(self, message: str, debug_ref: str) -> None:
        super().__init__(message)
        self.debug_ref = debug_ref


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
        try:
            result = self._backend.predict(
                instruction, screenshot, context.task_id, image_mime="image/png",
                client_step=context.current_step + 1, session_instruction=context.goal,
            )
        except ValueError as exc:
            raw = getattr(exc, "response", None)
            if isinstance(raw, str):
                debug_ref = self._store.put({"assistant_output": raw}, prefix="model-output")
                raise QwenProposalError(str(exc), debug_ref) from exc
            raise
        debug_ref = self._store.put(result, prefix="model-output")
        try:
            parsed = parse_qwen_actions(result.get("actions", []))
            if not parsed:
                raise ValueError("Qwen-CUA v2 must return at least one action")
            snapshot: CanonicalSnapshot = self._store.require(context.based_on_snapshot)
            canonical_actions: list[Action] = []
            working_snapshot = snapshot
            for item in parsed:
                action = canonical_action_from_parsed(
                    item, working_snapshot, context.frame.pixel_size
                )
                canonical_actions.append(action)
                if action.coordinate is not None and action.type in {
                    ActionType.POINTER_MOVE,
                    ActionType.POINTER_CLICK,
                    ActionType.POINTER_DOUBLE_CLICK,
                    ActionType.POINTER_DRAG,
                }:
                    working_snapshot = replace(working_snapshot, cursor=action.coordinate)
            actions = tuple(canonical_actions)
        except (KeyError, TypeError, ValueError) as exc:
            raise QwenProposalError(str(exc), debug_ref) from exc
        return ActionProposal(
            proposal_id=new_id("proposal"),
            source="qwen-cua",
            based_on_snapshot=context.based_on_snapshot,
            actions=actions,
            claimed_intent=None,
            debug_ref=debug_ref,
        )

    def record_execution(self, task_id: str, receipt: Any) -> object:
        recorder = getattr(self._backend, "record_execution", None)
        if not callable(recorder):
            return {"ok": False, "message": "execution feedback unsupported"}
        executed = tuple(getattr(receipt, "executed_actions", ())) or (
            (receipt.executed_action,) if receipt.executed_action is not None else ()
        )
        if receipt.status.value == "delivered" and executed and executed[-1].type == ActionType.DONE:
            status = "partial"
            reason = "DONE triggers evidence collection; task completion is not established"
        else:
            status = {
                "delivered": "success",
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

    def record_outcome(
        self, task_id: str, *, status: str, execution: Any, reason: Any
    ) -> object:
        """Resolve an unexecuted proposal with its actual terminal outcome."""
        recorder = getattr(self._backend, "record_execution", None)
        if not callable(recorder):
            return {"ok": False, "message": "outcome feedback unsupported"}
        return recorder(
            task_id,
            status=status,
            execution=execution,
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
            "spatial_projection": context.spatial_projection,
            "recent_execution_receipt": context.recent_execution_receipt,
            "assertion_feedback": context.assertion_feedback,
            "constraints": context.constraints,
            "recent_frame_refs": context.recent_frame_refs,
            "primary_attribution": context.primary_attribution,
            "projection_limits": context.projection_limits,
        }
        return (
            "Use the screenshot and this controller context. Return one proposal.\n"
            "The completion_requirements are controller-authoritative: do not "
            "terminate successfully, or treat the GUI sequence as finished, until "
            "the latest screenshot and spatial_projection show every required "
            "condition. For an active_window.title requirement, make the window "
            "whose visible title matches expected active after completing the "
            "requested interaction; if it is not active, use a visible GUI action "
            "to focus it before terminating. Re-check this after every action and "
            "prioritize this finalization when remaining_steps is low.\n"
            + json.dumps(to_primitive(projection), ensure_ascii=False)
        )



def canonical_action_from_parsed(
    parsed: dict[str, Any],
    snapshot: CanonicalSnapshot,
    pixel_size: tuple[int, int],
) -> Action:
    """Translate one allowlisted legacy Qwen call into the finite v2 action schema."""
    source_type = str(parsed.get("type"))
    action_type = _ACTION_TYPES.get(source_type)
    if action_type is None:
        raise ValueError(f"unsupported Qwen action in v2: {source_type}")
    args = list(parsed.get("args", []))
    kwargs = dict(parsed.get("kwargs", {}))
    coordinate = parsed.get("coordinate")
    relative = source_type in {"moveRel", "dragRel"}
    if relative:
        if snapshot.cursor is None:
            raise ValueError("relative Qwen action requires a current cursor position")
        dx = kwargs.get("xOffset", kwargs.get("x", args[0] if args else None))
        dy = kwargs.get("yOffset", kwargs.get("y", args[1] if len(args) > 1 else None))
        if not isinstance(dx, (int, float)) or not isinstance(dy, (int, float)):
            raise ValueError("relative Qwen action requires numeric x/y offsets")
        desktop_point = Point(snapshot.cursor.x + float(dx), snapshot.cursor.y + float(dy))
    elif coordinate is not None:
        width, height = pixel_size
        if width <= 0 or height <= 0:
            raise ValueError("frame pixel size must be positive")
        bounds = snapshot.coordinate_space.bounds
        desktop_point = Point(
            bounds.x + float(coordinate["x"]) * bounds.width / width,
            bounds.y + float(coordinate["y"]) * bounds.height / height,
        )
    elif action_type == ActionType.POINTER_CLICK:
        if snapshot.cursor is None:
            raise ValueError("pointer action requires a current cursor position")
        desktop_point = snapshot.cursor
    else:
        desktop_point = None

    parameters: dict[str, Any] = {}
    if action_type in {ActionType.POINTER_CLICK, ActionType.POINTER_DOUBLE_CLICK, ActionType.POINTER_DRAG}:
        parameters = {key: kwargs[key] for key in ("button", "duration") if key in kwargs}
        if source_type == "rightClick":
            parameters.setdefault("button", "right")
        elif source_type == "middleClick":
            parameters.setdefault("button", "middle")
        elif source_type == "tripleClick":
            parameters["clicks"] = 3
        elif source_type in {"mouseDown", "mouseUp"}:
            parameters["event"] = "down" if source_type == "mouseDown" else "up"
        if relative:
            parameters["relative"] = True
    elif action_type == ActionType.POINTER_MOVE:
        parameters = {key: kwargs[key] for key in ("duration",) if key in kwargs}
        if relative:
            parameters["relative"] = True
    elif action_type == ActionType.POINTER_SCROLL:
        parameters = {
            "clicks": kwargs.get("clicks", args[0] if args else 0),
            "axis": "horizontal" if source_type == "hscroll" else "vertical",
        }
    elif action_type == ActionType.KEYBOARD_KEY:
        parameters = {
            "key": kwargs.get("key", args[0] if args else None),
            "presses": kwargs.get("presses", 1),
            "interval": kwargs.get("interval", 0),
        }
        if source_type in {"keyDown", "keyUp"}:
            parameters["event"] = "down" if source_type == "keyDown" else "up"
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
        coordinate_space=snapshot.coordinate_space.id if desktop_point is not None else None,
        parameters=parameters,
    )
