"""Compositor-neutral conversions between screenshot and desktop coordinates."""

from __future__ import annotations

from typing import Any


def screenshot_to_desktop_point(
    coordinate: dict[str, Any],
    screenshot_width: int,
    screenshot_height: int,
    desktop_bounds: dict[str, float],
) -> dict[str, float]:
    if screenshot_width <= 0 or screenshot_height <= 0:
        raise ValueError("Screenshot size must be positive")
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
    if (
        screenshot_width <= 0
        or screenshot_height <= 0
        or desktop_bounds["width"] <= 0
        or desktop_bounds["height"] <= 0
    ):
        raise ValueError("Screenshot and desktop bounds must be positive")
    return {
        "x": (_number(coordinate.get("x")) - desktop_bounds["x"])
        * screenshot_width
        / desktop_bounds["width"],
        "y": (_number(coordinate.get("y")) - desktop_bounds["y"])
        * screenshot_height
        / desktop_bounds["height"],
    }


def _number(value: Any) -> float:
    return 0.0 if value is None else float(value)
