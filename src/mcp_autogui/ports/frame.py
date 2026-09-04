from __future__ import annotations

from typing import Protocol

from ..core.models import FrameReference


class FrameProvider(Protocol):
    def capture_frame(self) -> FrameReference: ...

