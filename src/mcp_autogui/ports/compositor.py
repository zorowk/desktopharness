from __future__ import annotations

from typing import Protocol

from ..core.models import AdapterDescriptor, CanonicalSnapshot, Point, Rect


class CompositorAdapter(Protocol):
    @property
    def descriptor(self) -> AdapterDescriptor: ...

    def observe(self) -> CanonicalSnapshot: ...

    def get_window_tree(self) -> object: ...

    def get_cursor_position(self) -> Point | None: ...

    def get_desktop_geometry(self) -> Rect: ...

    def hit_test(self, point: Point, snapshot: CanonicalSnapshot | None = None) -> str | None: ...

    def topmost_window_at(self, point: Point, snapshot: CanonicalSnapshot | None = None) -> str | None: ...

    def is_above(
        self, window_a: str, window_b: str, snapshot: CanonicalSnapshot | None = None
    ) -> bool | None: ...

    def occluded(
        self, window_id: str, region: Rect, snapshot: CanonicalSnapshot | None = None
    ) -> bool | None: ...
