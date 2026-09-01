#coding: utf-8

from __future__ import annotations

from copy import deepcopy
from typing import Any


Rect = dict[str, float]


def normalize_box(bbox: list[float], screen_width: float, screen_height: float) -> Rect:
    """Convert OmniParser [x1, y1, x2, y2] ratios to pixel coordinates."""
    xmin, ymin, xmax, ymax = bbox
    return {
        "x1": float(xmin) * screen_width,
        "y1": float(ymin) * screen_height,
        "x2": float(xmax) * screen_width,
        "y2": float(ymax) * screen_height,
    }


def is_element_in_window(elem_box: Rect, win_geometry: dict[str, Any]) -> bool:
    cx = (elem_box["x1"] + elem_box["x2"]) / 2
    cy = (elem_box["y1"] + elem_box["y2"]) / 2
    wx1 = _number(win_geometry.get("x"))
    wy1 = _number(win_geometry.get("y"))
    wx2 = wx1 + _number(win_geometry.get("width"))
    wy2 = wy1 + _number(win_geometry.get("height"))
    return wx1 <= cx <= wx2 and wy1 <= cy <= wy2


def fuse_omniparser_with_treeland(
    omniparser_elements: list[dict[str, Any]],
    treeland_tree: dict[str, Any],
) -> dict[str, Any]:
    fused_tree = deepcopy(treeland_tree)
    screen_width, screen_height = screen_size_from_treeland(fused_tree)
    ensure_lockscreen_window(fused_tree, screen_width, screen_height)
    windows = flatten_treeland_windows(fused_tree, initialize_elements=True)
    desktop_unparented_elements = []
    assigned_count = 0

    for index, element in enumerate(omniparser_elements):
        bbox = element.get("bbox")
        if not _valid_bbox(bbox):
            desktop_unparented_elements.append(_build_element_record(index, element, None, None))
            continue

        absolute_box = normalize_box(bbox, screen_width, screen_height)
        captured = False
        for window in windows:
            geometry = window.get("geometry") or {}
            if not is_element_in_window(absolute_box, geometry):
                continue

            window["elements"].append(
                _build_element_record(index, element, absolute_box, _relative_box(absolute_box, geometry))
            )
            assigned_count += 1
            captured = True
            break

        if not captured:
            desktop_unparented_elements.append(_build_element_record(index, element, absolute_box, None))

    fused_tree["desktop_unparented_elements"] = desktop_unparented_elements
    fused_tree["fusion_stats"] = {
        "screen_width": screen_width,
        "screen_height": screen_height,
        "total_elements": len(omniparser_elements),
        "assigned_elements": assigned_count,
        "unassigned_elements": len(desktop_unparented_elements),
        "window_count": len(windows),
    }
    return fused_tree


def ensure_lockscreen_window(tree: dict[str, Any], screen_width: float, screen_height: float) -> None:
    if tree.get("currentMode") != "LockScreen":
        return

    layers = tree.setdefault("layers", [])
    lockscreen_layer = None
    for layer in layers:
        if str(layer.get("name") or "").lower() == "lockscreen":
            lockscreen_layer = layer
            break

    if lockscreen_layer is None:
        max_layer = max((_number(layer.get("layer")) for layer in layers), default=0.0)
        lockscreen_layer = {
            "name": "lockscreen",
            "layer": max_layer + 1.0,
            "windows": [],
            "workspaces": [],
        }
        layers.append(lockscreen_layer)
    else:
        lockscreen_layer.setdefault("windows", [])
        lockscreen_layer.setdefault("workspaces", [])

    if any(window.get("synthetic") is True and window.get("container") == "lockscreen" for window in lockscreen_layer["windows"]):
        return

    layer_value = _number(lockscreen_layer.get("layer"))
    lockscreen_layer["windows"].append(
        {
            "appId": "lockscreen",
            "title": "Lock Screen",
            "output": "",
            "container": "lockscreen",
            "workspace": -1,
            "layer": layer_value,
            "z": 0,
            "type": 3,
            "state": 0,
            "visible": True,
            "active": True,
            "synthetic": True,
            "geometry": {
                "x": 0.0,
                "y": 0.0,
                "width": screen_width,
                "height": screen_height,
            },
            "titlebarGeometry": {
                "x": 0.0,
                "y": 0.0,
                "width": 0.0,
                "height": 0.0,
            },
            "boundingRect": {
                "x": 0.0,
                "y": 0.0,
                "width": screen_width,
                "height": screen_height,
            },
            "iconGeometry": {
                "x": 0.0,
                "y": 0.0,
                "width": 0.0,
                "height": 0.0,
            },
            "position": {
                "x": 0.0,
                "y": 0.0,
            },
        }
    )


def build_action_targets(fused_tree: dict[str, Any]) -> dict[str, Any]:
    windows = []
    for window_id, window in enumerate(actionable_treeland_windows(fused_tree)):
        elements = [
            {
                "element_id": element.get("id"),
                "type": element.get("type"),
                "content": element.get("content"),
                "interactive": element.get("interactivity"),
            }
            for element in window.get("elements", [])
        ]
        target = {
            "window_id": window_id,
            "title": window.get("title"),
            "appId": window.get("appId"),
            "container": window.get("container"),
            "workspace": window.get("workspace"),
            "active": window.get("active"),
            "layer": window.get("layer"),
            "z": window.get("z"),
            "regions": _window_regions(window),
            "elements": elements,
        }
        windows.append(target)

    return {
        "currentMode": fused_tree.get("currentMode"),
        "stats": fused_tree.get("fusion_stats", {}),
        "windows": windows,
    }


def actionable_treeland_windows(tree: dict[str, Any]) -> list[dict[str, Any]]:
    actionable_windows = []
    covering_rects: list[Rect] = []
    for window in flatten_treeland_windows(tree):
        rect = _geometry_to_box(window.get("geometry") or {})
        if rect is None:
            continue
        if _box_fully_covered(rect, covering_rects):
            continue

        actionable_windows.append(window)
        covering_rects.append(rect)
    return actionable_windows


def screen_size_from_treeland(tree: dict[str, Any]) -> tuple[float, float]:
    bounds = desktop_bounds_from_treeland(tree)
    return bounds["width"], bounds["height"]


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


def _screenshot_to_desktop_point(
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


def flatten_treeland_windows(
    tree: dict[str, Any],
    initialize_elements: bool = False,
) -> list[dict[str, Any]]:
    window_entries: list[tuple[float, float, dict[str, Any]]] = []
    for layer in tree.get("layers", []):
        layer_name = layer.get("name", "")
        for window in layer.get("windows", []):
            _append_window(window_entries, window, layer_name, layer.get("layer"), initialize_elements)

        for workspace in layer.get("workspaces", []):
            if workspace.get("isActive") is not True:
                continue
            for window in workspace.get("windows", []):
                _append_window(window_entries, window, layer_name, layer.get("layer"), initialize_elements)

    window_entries.sort(key=lambda entry: (entry[0], entry[1]), reverse=True)
    return [window for _, _, window in window_entries]


def _append_window(
    window_entries: list[tuple[float, float, dict[str, Any]]],
    window: dict[str, Any],
    layer_name: str,
    layer_value: Any,
    initialize_elements: bool,
) -> None:
    geometry = window.get("geometry") or {}
    if not _has_area(geometry):
        return
    if "workspace" in str(layer_name).lower() and window.get("visible") is not True:
        return

    window.setdefault("layer", layer_value)
    if initialize_elements:
        window["elements"] = []
    else:
        window.setdefault("elements", [])
    window_entries.append((_number(window.get("layer", layer_value)), _number(window.get("z")), window))


def _build_element_record(
    index: int,
    element: dict[str, Any],
    absolute_box: Rect | None,
    relative_box: Rect | None,
) -> dict[str, Any]:
    record = {
        "id": index,
        "type": element.get("type"),
        "content": element.get("content"),
        "bbox": element.get("bbox"),
        "interactivity": element.get("interactivity"),
        "source": element.get("source"),
    }
    if absolute_box is not None:
        record["absolute_box"] = absolute_box
    if relative_box is not None:
        record["relative_box"] = relative_box
    return record


def _window_regions(window: dict[str, Any]) -> list[dict[str, Any]]:
    regions = [
        {
            "name": "content",
            "actions": ["click"],
        }
    ]
    if _is_draggable_window(window):
        regions.insert(
            0,
            {
                "name": "titlebar",
                "actions": ["click", "drag_window"],
            },
        )
    return regions


def _is_draggable_window(window: dict[str, Any]) -> bool:
    container = str(window.get("container") or "").lower()
    return container == "workspace"


def _geometry_to_box(geometry: dict[str, Any]) -> Rect | None:
    if not _has_area(geometry):
        return None
    x1 = _number(geometry.get("x"))
    y1 = _number(geometry.get("y"))
    return {
        "x1": x1,
        "y1": y1,
        "x2": x1 + _number(geometry.get("width")),
        "y2": y1 + _number(geometry.get("height")),
    }


def _box_fully_covered(box: Rect, covering_boxes: list[Rect]) -> bool:
    remaining = [box]
    for covering_box in covering_boxes:
        next_remaining = []
        for remaining_box in remaining:
            next_remaining.extend(_subtract_box(remaining_box, covering_box))
        remaining = next_remaining
        if not remaining:
            return True
    return False


def _subtract_box(box: Rect, covering_box: Rect) -> list[Rect]:
    ix1 = max(box["x1"], covering_box["x1"])
    iy1 = max(box["y1"], covering_box["y1"])
    ix2 = min(box["x2"], covering_box["x2"])
    iy2 = min(box["y2"], covering_box["y2"])
    if ix1 >= ix2 or iy1 >= iy2:
        return [box]

    pieces = []
    if box["y1"] < iy1:
        pieces.append({"x1": box["x1"], "y1": box["y1"], "x2": box["x2"], "y2": iy1})
    if iy2 < box["y2"]:
        pieces.append({"x1": box["x1"], "y1": iy2, "x2": box["x2"], "y2": box["y2"]})
    if box["x1"] < ix1:
        pieces.append({"x1": box["x1"], "y1": iy1, "x2": ix1, "y2": iy2})
    if ix2 < box["x2"]:
        pieces.append({"x1": ix2, "y1": iy1, "x2": box["x2"], "y2": iy2})
    return pieces


def _relative_box(absolute_box: Rect, geometry: dict[str, Any]) -> Rect:
    win_x = _number(geometry.get("x"))
    win_y = _number(geometry.get("y"))
    return {
        "x1": absolute_box["x1"] - win_x,
        "y1": absolute_box["y1"] - win_y,
        "x2": absolute_box["x2"] - win_x,
        "y2": absolute_box["y2"] - win_y,
    }


def _valid_bbox(bbox: Any) -> bool:
    if not isinstance(bbox, list) or len(bbox) != 4:
        return False
    try:
        [_number(value) for value in bbox]
    except (TypeError, ValueError):
        return False
    return True


def _has_area(rect: dict[str, Any]) -> bool:
    return _number(rect.get("width")) > 0 and _number(rect.get("height")) > 0


def _number(value: Any) -> float:
    if value is None:
        return 0.0
    return float(value)
