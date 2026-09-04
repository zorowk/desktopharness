"""Standard Action to PyAutoGUI input adapter."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from ...core.models import (
    ActionProposal,
    ActionType,
    ExecutionReceipt,
    ExecutionStatus,
    Point,
    new_id,
    utc_now,
)


class PyAutoGUIExecutor:
    executor_id = "pyautogui"

    def __init__(
        self,
        module: Any,
        *,
        coordinate_mapper: Callable[[Point, str, ActionProposal], Point] | None = None,
        platform_resolver: Callable[[str], dict | None] | None = None,
    ) -> None:
        self._module = module
        self._coordinate_mapper = coordinate_mapper or (lambda point, _space, _proposal: point)
        self._platform_resolver = platform_resolver

    def execute(self, proposal: ActionProposal) -> ExecutionReceipt:
        started = utc_now()
        status = ExecutionStatus.DELIVERED
        error = None
        try:
            self._inject(proposal)
        except Exception as exc:
            status = ExecutionStatus.FAILED
            error = f"EXECUTOR_{type(exc).__name__.upper()}"
        return ExecutionReceipt(
            execution_id=new_id("execution"),
            proposal_id=proposal.proposal_id,
            status=status,
            executed_action=proposal.action if status == ExecutionStatus.DELIVERED else None,
            started_at=started,
            finished_at=utc_now(),
            error_code=error,
        )

    def _inject(self, proposal: ActionProposal) -> None:
        action = proposal.action
        point = (
            self._coordinate_mapper(action.coordinate, action.coordinate_space or "", proposal)
            if action.coordinate is not None
            else None
        )
        params = dict(action.parameters)
        if action.type == ActionType.POINTER_MOVE:
            self._module.moveTo(point.x, point.y, duration=params.get("duration", 0))
        elif action.type == ActionType.POINTER_CLICK:
            self._module.click(point.x, point.y, button=params.get("button", "left"))
        elif action.type == ActionType.POINTER_DOUBLE_CLICK:
            self._module.doubleClick(point.x, point.y, button=params.get("button", "left"))
        elif action.type == ActionType.POINTER_DRAG:
            self._module.dragTo(point.x, point.y, duration=params.get("duration", 0.5), button=params.get("button", "left"))
        elif action.type == ActionType.POINTER_SCROLL:
            self._module.scroll(params.get("clicks", 0))
        elif action.type == ActionType.KEYBOARD_KEY:
            self._module.press(params["key"])
        elif action.type == ActionType.KEYBOARD_SHORTCUT:
            self._module.hotkey(*params["keys"])
        elif action.type == ActionType.KEYBOARD_TEXT:
            self._module.write(params["text"], interval=params.get("interval", 0))
        elif action.type == ActionType.PLATFORM_INVOKE:
            if self._platform_resolver is None:
                raise RuntimeError("platform capability resolver unavailable")
            capability = self._platform_resolver(str(params.get("capability_id") or ""))
            if not capability or not capability.get("auto_invokable"):
                raise PermissionError("platform capability is not executable")
            keys = capability.get("normalized_hotkeys", [[]])[0]
            self._module.press(keys[0]) if len(keys) == 1 else self._module.hotkey(*keys)
        elif action.type == ActionType.DONE:
            return
        else:
            raise ValueError(f"unsupported input action: {action.type.value}")
