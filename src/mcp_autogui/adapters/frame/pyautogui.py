from __future__ import annotations

import io
from typing import Any

from ...core.models import FrameReference, new_id, utc_now
from ...core.store import ObjectStore


class PyAutoGUIFrameProvider:
    provider_id = "pyautogui-frame"

    def __init__(self, module: Any, artifact_store: ObjectStore) -> None:
        self._module = module
        self._artifacts = artifact_store

    def capture_frame(self) -> FrameReference:
        image = self._module.screenshot().convert("RGB")
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        image_ref = self._artifacts.put(buffer.getvalue(), prefix="image")
        transform_ref = self._artifacts.put(
            {
                "from": "frame-pixel",
                "to": "desktop-logical",
                "kind": "bounds-linear",
            },
            prefix="transform",
        )
        return FrameReference(
            frame_id=new_id("frame"),
            captured_at=utc_now(),
            image_ref=image_ref,
            pixel_size=(int(image.size[0]), int(image.size[1])),
            transform_ref=transform_ref,
        )
