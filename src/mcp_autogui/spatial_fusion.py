"""Coordinate spaces used across the perception/control chain.

1. Qwen normalized: 0..999 integer grid on each axis. The model returns
   these; ``screenshot_to_qwen_normalized`` maps screenshot pixels into it
   and ``qwen_normalized_to_screenshot`` maps back. Round-trips are lossy by
   at most one screenshot pixel because of the integer normalization.
2. Screenshot pixels: integer pixels of the captured PNG (e.g. 1920x1080).
   A screenshot pixel maps to a logical desktop point via
   ``_screenshot_to_desktop_point`` using ``desktop_bounds``.
3. Treeland logical desktop: float coordinates spanning ``desktop_bounds``
   (origin x/y + width/height), including all visible layers (background,
   workspace, dock). ``desktop_to_screenshot_point`` maps logical points
   back to screenshot pixels for input injection.

The Qwen normalized conversion is the single source used by the controller
coordinate constraint; do not duplicate the formula elsewhere.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any


QWEN_NORMALIZED_MAX = 999


def desktop_bounds_from_treeland(tree: dict[str, Any]) -> dict[str, float]:
    source_rects = [
        window.get("geometry") or {}
        for window in flatten_treeland_windows(tree)
        if _has_area(window.get("geometry") or {})
    ]
    for layer in tree.get("layers", []):
        if "background" not in str(layer.get("name") or "").lower():
            continue
        for window in layer.get("windows", []):
            for key in ("boundingRect", "geometry"):
                rect = window.get(key) or {}
                if _has_area(rect):
                    source_rects.append(rect)
                    break
    if not source_rects:
        raise ValueError("Unable to determine desktop bounds from Treeland tree")
    min_x = min(_number(rect.get("x")) for rect in source_rects)
    min_y = min(_number(rect.get("y")) for rect in source_rects)
    max_x = max(_number(rect.get("x")) + _number(rect.get("width")) for rect in source_rects)
    max_y = max(_number(rect.get("y")) + _number(rect.get("height")) for rect in source_rects)
    return {
        "x": min_x,
        "y": min_y,
        "width": max_x - min_x,
        "height": max_y - min_y,
    }


def fuse_qwen_actions_with_treeland(
    actions: list[dict[str, Any]],
    treeland_tree: dict[str, Any],
    screenshot_size: tuple[int, int],
) -> dict[str, Any]:
    """Attach deterministic Treeland window context to parsed Qwen actions."""
    screenshot_width, screenshot_height = screenshot_size
    if screenshot_width <= 0 or screenshot_height <= 0:
        raise ValueError("Screenshot size must be positive")

    tree = deepcopy(treeland_tree)
    desktop_bounds = desktop_bounds_from_treeland(tree)
    windows = flatten_treeland_windows(tree)
    active_window = next((window for window in windows if window.get("active") is True), None)
    fused_actions = []

    for action_index, action in enumerate(actions):
        fused_action = deepcopy(action)
        fused_action["action_index"] = action_index
        screenshot_coordinate = action.get("coordinate")
        if _valid_point(screenshot_coordinate):
            desktop_coordinate = _screenshot_to_desktop_point(
                screenshot_coordinate,
                screenshot_width,
                screenshot_height,
                desktop_bounds,
            )
            matching_windows = [
                (window_id, window)
                for window_id, window in enumerate(windows)
                if _point_in_geometry(desktop_coordinate, window.get("geometry") or {})
            ]
            # Treeland supplies top-level container geometry and stacking only.
            # BackgroundContainer can contain clickable desktop icons, and a
            # workspace window can contain arbitrary controls. Do not infer
            # child-control semantics from this match: it identifies only the
            # frontmost top-level target for validation and reprojection.
            target = (
                _qwen_window_target(matching_windows[0][0], matching_windows[0][1], desktop_coordinate)
                if matching_windows
                else None
            )
            fused_action["desktop_coordinate"] = desktop_coordinate
            fused_action["target_window"] = target
            fused_action["validation"] = {
                "inside_desktop": _point_in_desktop(desktop_coordinate, desktop_bounds),
                "target_window_found": target is not None,
                "topmost_at_coordinate": target is not None,
                "matching_window_count": len(matching_windows),
            }
            if len(matching_windows) > 1:
                fused_action["window_candidates_front_to_back"] = [
                    _qwen_window_summary(window_id, window)
                    for window_id, window in matching_windows
                ]
        else:
            fused_action["desktop_coordinate"] = None
            fused_action["target_window"] = (
                _qwen_window_target_from_active(windows, active_window)
                if active_window is not None
                else None
            )
            fused_action["validation"] = {
                "inside_desktop": None,
                "target_window_found": active_window is not None,
                "topmost_at_coordinate": None,
                "matching_window_count": None,
                "target_source": "active_window" if active_window is not None else None,
            }
        fused_actions.append(fused_action)

    return {
        "currentMode": tree.get("currentMode"),
        "screenshot_size": {"width": screenshot_width, "height": screenshot_height},
        "desktop_bounds": desktop_bounds,
        "window_count": len(windows),
        "actions": fused_actions,
    }


def _qwen_window_target_from_active(
    windows: list[dict[str, Any]],
    active_window: dict[str, Any],
) -> dict[str, Any]:
    return _qwen_window_summary(windows.index(active_window), active_window)

def _qwen_window_summary(window_id: int, window: dict[str, Any]) -> dict[str, Any]:
    return {
        "window_id": window_id,
        "appId": window.get("appId"),
        "title": window.get("title"),
        "output": window.get("output"),
        "container": window.get("container"),
        "workspace": window.get("workspace"),
        "geometry": deepcopy(window.get("geometry") or {}),
        "active": window.get("active"),
        "visible": window.get("visible"),
        "layer": window.get("layer"),
        "z": window.get("z"),
    }


def _qwen_window_target(
    window_id: int,
    window: dict[str, Any],
    desktop_coordinate: dict[str, float],
) -> dict[str, Any]:
    target = _qwen_window_summary(window_id, window)
    geometry = window.get("geometry") or {}
    target["window_relative_coordinate"] = {
        "x": desktop_coordinate["x"] - _number(geometry.get("x")),
        "y": desktop_coordinate["y"] - _number(geometry.get("y")),
    }
    return target


def screenshot_to_qwen_normalized(
    coordinate: dict[str, Any],
    screenshot_width: int,
    screenshot_height: int,
) -> dict[str, int]:
    """Map screenshot pixels to Qwen's 0..999 normalized space."""
    if screenshot_width <= 1 or screenshot_height <= 1:
        raise ValueError("Screenshot size must be at least 2 pixels per axis")
    return {
        "x": round(
            _number(coordinate.get("x")) * QWEN_NORMALIZED_MAX / (screenshot_width - 1)
        ),
        "y": round(
            _number(coordinate.get("y")) * QWEN_NORMALIZED_MAX / (screenshot_height - 1)
        ),
    }


def qwen_normalized_to_screenshot(
    coordinate: dict[str, Any],
    screenshot_width: int,
    screenshot_height: int,
) -> dict[str, float]:
    """Map Qwen's 0..999 normalized coordinates back to screenshot pixels."""
    if screenshot_width <= 1 or screenshot_height <= 1:
        raise ValueError("Screenshot size must be at least 2 pixels per axis")
    return {
        "x": _number(coordinate.get("x"))
        * (screenshot_width - 1)
        / QWEN_NORMALIZED_MAX,
        "y": _number(coordinate.get("y"))
        * (screenshot_height - 1)
        / QWEN_NORMALIZED_MAX,
    }


def screenshot_to_desktop_point(
    coordinate: dict[str, Any],
    screenshot_width: int,
    screenshot_height: int,
    desktop_bounds: dict[str, float],
) -> dict[str, float]:
    return {
        "x": desktop_bounds["x"]
        + _number(coordinate.get("x")) * desktop_bounds["width"] / screenshot_width,
        "y": desktop_bounds["y"]
        + _number(coordinate.get("y")) * desktop_bounds["height"] / screenshot_height,
    }


# Kept as a private alias for callers from before this conversion became part
# of the controller-facing window-move path.
_screenshot_to_desktop_point = screenshot_to_desktop_point


def desktop_to_screenshot_point(
    coordinate: dict[str, Any],
    screenshot_width: int,
    screenshot_height: int,
    desktop_bounds: dict[str, float],
) -> dict[str, float]:
    """Map Treeland logical desktop coordinates to input/screenshot pixels."""
    if screenshot_width <= 0 or screenshot_height <= 0:
        raise ValueError("Screenshot size must be positive")
    if desktop_bounds["width"] <= 0 or desktop_bounds["height"] <= 0:
        raise ValueError("Treeland desktop bounds must be positive")
    return {
        "x": (_number(coordinate.get("x")) - desktop_bounds["x"])
        * screenshot_width
        / desktop_bounds["width"],
        "y": (_number(coordinate.get("y")) - desktop_bounds["y"])
        * screenshot_height
        / desktop_bounds["height"],
    }


def _point_in_geometry(point: dict[str, float], geometry: dict[str, Any]) -> bool:
    if not _has_area(geometry):
        return False
    x = point["x"]
    y = point["y"]
    left = _number(geometry.get("x"))
    top = _number(geometry.get("y"))
    return (
        left <= x < left + _number(geometry.get("width"))
        and top <= y < top + _number(geometry.get("height"))
    )


def _point_in_desktop(point: dict[str, float], bounds: dict[str, float]) -> bool:
    return (
        bounds["x"] <= point["x"] < bounds["x"] + bounds["width"]
        and bounds["y"] <= point["y"] < bounds["y"] + bounds["height"]
    )


def _valid_point(point: Any) -> bool:
    if not isinstance(point, dict):
        return False
    for key in ("x", "y"):
        value = point.get(key)
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            return False
    return True


def flatten_treeland_windows(tree: dict[str, Any]) -> list[dict[str, Any]]:
    window_entries: list[tuple[float, float, dict[str, Any]]] = []
    for layer in tree.get("layers", []):
        layer_name = layer.get("name", "")
        for window in layer.get("windows", []):
            _append_window(window_entries, window, layer_name, layer.get("layer"))

        for workspace in layer.get("workspaces", []):
            if workspace.get("isActive") is not True:
                continue
            for window in workspace.get("windows", []):
                _append_window(window_entries, window, layer_name, layer.get("layer"))

    window_entries.sort(key=lambda entry: (entry[0], entry[1]), reverse=True)
    return [window for _, _, window in window_entries]


def _append_window(
    window_entries: list[tuple[float, float, dict[str, Any]]],
    window: dict[str, Any],
    layer_name: str,
    layer_value: Any,
) -> None:
    geometry = window.get("geometry") or {}
    if not _has_area(geometry):
        return
    if "workspace" in str(layer_name).lower() and window.get("visible") is not True:
        return

    window.setdefault("layer", layer_value)
    window_entries.append((_number(window.get("layer", layer_value)), _number(window.get("z")), window))


def _has_area(rect: dict[str, Any]) -> bool:
    return _number(rect.get("width")) > 0 and _number(rect.get("height")) > 0


def _number(value: Any) -> float:
    if value is None:
        return 0.0
    return float(value)
