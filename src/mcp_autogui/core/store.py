"""Small in-memory reference store used by the v2 runtime and tests."""

from __future__ import annotations

from threading import RLock
from typing import Any

from .models import new_id


class ObjectStore:
    def __init__(self) -> None:
        self._objects: dict[str, Any] = {}
        self._lock = RLock()

    def put(self, value: Any, *, prefix: str = "object", object_ref: str | None = None) -> str:
        reference = object_ref or new_id(prefix)
        with self._lock:
            if reference in self._objects:
                if self._objects[reference] == value:
                    return reference
                raise ValueError(f"object reference already exists: {reference}")
            self._objects[reference] = value
        return reference

    def get(self, reference: str) -> Any | None:
        with self._lock:
            return self._objects.get(reference)

    def require(self, reference: str) -> Any:
        value = self.get(reference)
        if value is None:
            raise KeyError(reference)
        return value

    def clear(self) -> None:
        with self._lock:
            self._objects.clear()
