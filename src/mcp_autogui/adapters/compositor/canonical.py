"""Strict adapter for compositors that already emit the canonical JSON shape."""

from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy
from typing import Any

from ...core.models import (
    AdapterDescriptor,
    CanonicalSnapshot,
    CanonicalWindowFact,
    CoordinateSpace,
    OutputFact,
    Point,
    Rect,
    WindowRole,
)
from ...core.store import ObjectStore


class CanonicalJsonAdapter:
    """Validate and copy only finite canonical fields from an external bridge.

    A Wayland/X11-specific bridge can use this adapter when it already maps its
    native protocol to the v2 JSON schema. Unknown input keys are discarded.
    """

    def __init__(
        self,
        descriptor: AdapterDescriptor,
        reader: Callable[[], dict[str, Any]],
        artifact_store: ObjectStore | None = None,
    ) -> None:
        self._descriptor = descriptor
        self._reader = reader
        self._artifacts = artifact_store or ObjectStore()
        self._latest: CanonicalSnapshot | None = None

    @property
    def descriptor(self) -> AdapterDescriptor:
        return self._descriptor

    def get_window_tree(self) -> object:
        return self._reader()

    def get_cursor_position(self) -> Point | None:
        snapshot = self._latest or self.observe()
        return snapshot.cursor

    def get_desktop_geometry(self) -> Rect:
        snapshot = self._latest or self.observe()
        return snapshot.coordinate_space.bounds

    def observe(self) -> CanonicalSnapshot:
        raw = deepcopy(self._reader())
        raw_ref = self._artifacts.put(deepcopy(raw), prefix=f"{self.descriptor.adapter_id}-raw")
        space = raw["coordinate_space"]
        bounds = _rect(space["bounds"])
        snapshot = CanonicalSnapshot(
            snapshot_id=str(raw["snapshot_id"]),
            captured_at=str(raw["captured_at"]),
            environment_version=str(raw["environment_version"]),
            coordinate_space=CoordinateSpace(
                str(space["id"]), bounds,
                str(space["version"]) if space.get("version") is not None else None,
            ),
            outputs=tuple(
                OutputFact(
                    str(item["output_id"]), _rect(item["geometry"]),
                    float(item["scale"]) if item.get("scale") is not None else None,
                )
                for item in raw.get("outputs", [])
            ),
            cursor=(
                Point(float(raw["cursor"]["x"]), float(raw["cursor"]["y"]))
                if raw.get("cursor") is not None else None
            ),
            windows=tuple(_window(item) for item in raw.get("windows", [])),
            raw_artifact_ref=raw_ref,
        )
        self._latest = snapshot
        return snapshot

    def hit_test(self, point: Point, snapshot: CanonicalSnapshot | None = None) -> str | None:
        current = snapshot or self._latest or self.observe()
        candidates = [
            window for window in current.windows
            if window.visible is not False and window.geometry.contains(point)
        ]
        if not candidates:
            return None
        if self.descriptor.capabilities.stacking.z_index:
            known = [window for window in candidates if window.z_index is not None]
            if known:
                return max(known, key=lambda window: window.z_index).window_id
        if self.descriptor.capabilities.stacking.model.value in {"hit-test", "topmost-only"}:
            return candidates[0].window_id
        return None

    def topmost_window_at(self, point: Point, snapshot: CanonicalSnapshot | None = None) -> str | None:
        return self.hit_test(point, snapshot)

    def is_above(
        self, window_a: str, window_b: str, snapshot: CanonicalSnapshot | None = None
    ) -> bool | None:
        current = snapshot or self._latest or self.observe()
        if not self.descriptor.capabilities.stacking.z_index:
            return None
        left, right = current.window(window_a), current.window(window_b)
        if left is None or right is None or left.z_index is None or right.z_index is None:
            return None
        return left.z_index > right.z_index

    def occluded(
        self, window_id: str, region: Rect, snapshot: CanonicalSnapshot | None = None
    ) -> bool | None:
        current = snapshot or self._latest or self.observe()
        if not self.descriptor.capabilities.stacking.occlusion:
            return None
        target = current.window(window_id)
        if target is None:
            return None
        return any(
            item.window_id != window_id
            and item.visible is not False
            and self.is_above(item.window_id, window_id, current) is True
            and _intersects(region, item.geometry)
            for item in current.windows
        )


def _rect(value: dict[str, Any]) -> Rect:
    return Rect(float(value["x"]), float(value["y"]), float(value["width"]), float(value["height"]))


def _window(value: dict[str, Any]) -> CanonicalWindowFact:
    # Explicit construction is intentional: extensions never cross the adapter.
    return CanonicalWindowFact(
        window_id=str(value["window_id"]),
        app_id=str(value["app_id"]) if value.get("app_id") is not None else None,
        title=str(value["title"]) if value.get("title") is not None else None,
        geometry=_rect(value["geometry"]),
        visible=value.get("visible") if isinstance(value.get("visible"), bool) else None,
        active=value.get("active") if isinstance(value.get("active"), bool) else None,
        z_index=float(value["z_index"]) if value.get("z_index") is not None else None,
        workspace_id=str(value["workspace_id"]) if value.get("workspace_id") is not None else None,
        output_id=str(value["output_id"]) if value.get("output_id") is not None else None,
        role=WindowRole(value.get("role", "unknown")),
    )


def _intersects(left: Rect, right: Rect) -> bool:
    return not (
        left.x + left.width <= right.x
        or right.x + right.width <= left.x
        or left.y + left.height <= right.y
        or right.y + right.height <= left.y
    )
