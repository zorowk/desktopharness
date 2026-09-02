from __future__ import annotations

import ast
import time
from typing import Any, Callable


ALLOWED_PYAUTOGUI_CALLS = {
    "click",
    "doubleClick",
    "tripleClick",
    "rightClick",
    "middleClick",
    "moveTo",
    "moveRel",
    "dragTo",
    "dragRel",
    "scroll",
    "hscroll",
    "press",
    "hotkey",
    "keyDown",
    "keyUp",
    "typewrite",
    "write",
    "mouseDown",
    "mouseUp",
}
TERMINAL_ACTIONS = {"DONE", "FAIL"}


def parse_qwen_actions(raw_actions: Any) -> list[dict[str, Any]]:
    if not isinstance(raw_actions, list):
        raise ValueError("Qwen-CUA actions must be a list")

    parsed: list[dict[str, Any]] = []
    for source_index, raw_action in enumerate(raw_actions):
        if not isinstance(raw_action, str):
            raise ValueError(f"Qwen-CUA action {source_index} must be a string")
        text = raw_action.strip()
        if text in TERMINAL_ACTIONS or text == "WAIT":
            parsed.append(
                {
                    "source_index": source_index,
                    "statement_index": 0,
                    "raw": raw_action,
                    "type": text.lower(),
                    "function": None,
                    "args": [],
                    "kwargs": {},
                    "coordinate": None,
                }
            )
            continue

        try:
            module = ast.parse(text, mode="exec")
        except SyntaxError as exc:
            raise ValueError(f"Qwen-CUA action {source_index} is invalid Python syntax") from exc
        if not module.body:
            raise ValueError(f"Qwen-CUA action {source_index} is empty")

        for statement_index, statement in enumerate(module.body):
            parsed.append(
                _parse_statement(statement, raw_action, source_index, statement_index)
            )
    return parsed


def _parse_statement(
    statement: ast.stmt,
    raw: str,
    source_index: int,
    statement_index: int,
) -> dict[str, Any]:
    if not isinstance(statement, ast.Expr) or not isinstance(statement.value, ast.Call):
        raise ValueError("Only direct pyautogui calls and time.sleep are allowed")
    call = statement.value
    namespace, function = _call_name(call.func)
    if namespace == "pyautogui":
        if function not in ALLOWED_PYAUTOGUI_CALLS:
            raise ValueError(f"pyautogui.{function} is not allowed")
        action_type = function
    elif namespace == "time" and function == "sleep":
        action_type = "wait"
    else:
        raise ValueError(f"{namespace}.{function} is not allowed")

    try:
        args = [ast.literal_eval(arg) for arg in call.args]
        kwargs = {
            keyword.arg: ast.literal_eval(keyword.value)
            for keyword in call.keywords
            if keyword.arg is not None
        }
    except (ValueError, TypeError) as exc:
        raise ValueError("Qwen-CUA action arguments must be literal values") from exc
    if any(keyword.arg is None for keyword in call.keywords):
        raise ValueError("Expanded keyword arguments are not allowed")

    return {
        "source_index": source_index,
        "statement_index": statement_index,
        "raw": raw,
        "type": action_type,
        "function": f"{namespace}.{function}",
        "args": args,
        "kwargs": kwargs,
        "coordinate": _absolute_coordinate(function, args, kwargs),
    }


def _call_name(node: ast.expr) -> tuple[str, str]:
    if (
        isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and isinstance(node.attr, str)
    ):
        return node.value.id, node.attr
    raise ValueError("Only namespace.function(...) calls are allowed")


def _absolute_coordinate(
    function: str,
    args: list[Any],
    kwargs: dict[str, Any],
) -> dict[str, float] | None:
    if function not in {
        "click",
        "doubleClick",
        "tripleClick",
        "rightClick",
        "middleClick",
        "moveTo",
        "dragTo",
    }:
        return None
    x = kwargs.get("x", args[0] if len(args) >= 1 else None)
    y = kwargs.get("y", args[1] if len(args) >= 2 else None)
    if not isinstance(x, (int, float)) or isinstance(x, bool):
        return None
    if not isinstance(y, (int, float)) or isinstance(y, bool):
        return None
    return {"x": float(x), "y": float(y)}


def execute_parsed_actions(
    actions: list[dict[str, Any]],
    pyautogui_module: Any,
    *,
    action_indexes: list[int] | None = None,
    before_action: Callable[[dict[str, Any]], None] | None = None,
    drag_handler: Callable[[dict[str, Any]], Any] | None = None,
) -> list[dict[str, Any]]:
    selected = set(action_indexes) if action_indexes is not None else None
    results = []
    for index, action in enumerate(actions):
        if selected is not None and index not in selected:
            continue
        if before_action is not None:
            before_action(action)
        action_type = action.get("type")
        try:
            if action_type in {"done", "fail"}:
                result = f"Terminal action: {str(action_type).upper()}"
            elif action_type == "wait":
                seconds = _wait_seconds(action)
                time.sleep(seconds)
                result = f"Waited {seconds} seconds"
            else:
                function_name = str(action.get("function") or "").removeprefix("pyautogui.")
                if function_name not in ALLOWED_PYAUTOGUI_CALLS:
                    raise ValueError(f"Action function is not allowed: {function_name}")
                failsafe_cursor = _failsafe_corner(pyautogui_module)
                if failsafe_cursor is not None:
                    results.append(
                        {
                            "action_index": index,
                            "status": "error",
                            "error_code": "cursor_in_failsafe_corner",
                            "error": (
                                "Refused PyAutoGUI action because the cursor is at a "
                                f"fail-safe corner: ({failsafe_cursor['x']}, "
                                f"{failsafe_cursor['y']})"
                            ),
                            "cursor": failsafe_cursor,
                        }
                    )
                    break
                if function_name == "dragTo" and drag_handler is not None:
                    result = drag_handler(action)
                else:
                    function = getattr(pyautogui_module, function_name)
                    result = function(*action.get("args", []), **action.get("kwargs", {}))
            results.append({"action_index": index, "status": "success", "result": result})
        except Exception as exc:
            results.append(
                {
                    "action_index": index,
                    "status": "error",
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
            break
    return results


def _failsafe_corner(pyautogui_module: Any) -> dict[str, float] | None:
    """Return a cursor corner that would trigger PyAutoGUI's fail-safe.

    Do not disable ``pyautogui.FAILSAFE`` globally.  Rejecting the pending
    action here gives the controller a stable, machine-readable failure rather
    than allowing PyAutoGUI to raise after validation has already passed.
    """
    if not bool(getattr(pyautogui_module, "FAILSAFE", False)):
        return None
    try:
        cursor = pyautogui_module.position()
        screen = pyautogui_module.size()
        x, y = float(cursor[0]), float(cursor[1])
        width, height = float(screen[0]), float(screen[1])
    except Exception:
        return None
    if width <= 0 or height <= 0:
        return None
    if x in {0.0, width - 1.0} and y in {0.0, height - 1.0}:
        return {"x": x, "y": y}
    return None


def set_absolute_coordinate(
    action: dict[str, Any],
    coordinate: dict[str, float],
) -> None:
    """Replace a parsed absolute coordinate without changing other arguments."""
    if action.get("coordinate") is None:
        raise ValueError("Action does not contain an absolute coordinate")
    x = float(coordinate["x"])
    y = float(coordinate["y"])
    kwargs = action.setdefault("kwargs", {})
    args = action.setdefault("args", [])
    if "x" in kwargs or "y" in kwargs:
        kwargs["x"] = x
        kwargs["y"] = y
    else:
        if len(args) < 2:
            raise ValueError("Coordinate action is missing positional x/y arguments")
        args[0] = x
        args[1] = y
    action["coordinate"] = {"x": x, "y": y}


def _wait_seconds(action: dict[str, Any]) -> float:
    if action.get("function") == "time.sleep":
        args = action.get("args", [])
        value = args[0] if args else action.get("kwargs", {}).get("secs", 1.0)
    else:
        value = 1.0
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ValueError("Wait duration must be numeric")
    if value < 0 or value > 30:
        raise ValueError("Wait duration must be between 0 and 30 seconds")
    return float(value)
