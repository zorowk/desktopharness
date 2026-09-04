"""Reference stores for v2 runtime objects and optional lightweight audit."""

from __future__ import annotations

import base64
import json
import os
import re
import tempfile
import time
from pathlib import Path
from threading import RLock
from typing import Any

from .models import new_id, to_primitive, utc_now


_SAFE_REFERENCE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


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


class JsonAuditObjectStore(ObjectStore):
    """Persist small, structured audit objects as private JSON files.

    Runtime values remain in memory, so the normal v2 transaction path keeps
    its typed protocol objects.  A later process can read the JSON copy for
    audit/trace purposes, but cannot resume an in-flight task from it.
    """

    def __init__(
        self,
        directory: str | Path,
        *,
        retention_days: int = 7,
        max_total_bytes: int = 100 * 1024 * 1024,
    ) -> None:
        super().__init__()
        if retention_days < 1 or max_total_bytes < 1:
            raise ValueError("audit retention and size limits must be positive")
        self.directory = _private_directory(directory) / "objects"
        self.directory.mkdir(mode=0o700, exist_ok=True)
        os.chmod(self.directory, 0o700)
        self.retention_days = retention_days
        self.max_total_bytes = max_total_bytes
        self._prune()

    def put(self, value: Any, *, prefix: str = "object", object_ref: str | None = None) -> str:
        reference = super().put(value, prefix=prefix, object_ref=object_ref)
        payload = self._audit_payload(value)
        if payload is not None:
            self._write(reference, payload)
            self._prune()
        return reference

    def get(self, reference: str) -> Any | None:
        value = super().get(reference)
        if value is not None:
            return value
        path = self._path(reference)
        if not path.is_file():
            return None
        with path.open("r", encoding="utf-8") as handle:
            envelope = json.load(handle)
        return _from_audit_primitive(envelope.get("value"))

    def clear(self) -> None:
        super().clear()
        for path in self.directory.glob("*.json"):
            path.unlink()

    def _audit_payload(self, value: Any) -> bytes | None:
        try:
            encoded = json.dumps(
                {"schema_version": 1, "stored_at": utc_now(), "value": _to_audit_primitive(value)},
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
        except (TypeError, ValueError):
            return None
        return encoded

    def _write(self, reference: str, payload: bytes) -> None:
        destination = self._path(reference)
        if destination.exists():
            return
        descriptor, temporary = tempfile.mkstemp(prefix=".audit-", dir=self.directory)
        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, destination)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def _path(self, reference: str) -> Path:
        if not _SAFE_REFERENCE.fullmatch(reference):
            raise ValueError("invalid object reference for audit store")
        return self.directory / f"{reference}.json"

    def _prune(self) -> None:
        files = sorted(self.directory.glob("*.json"), key=lambda item: item.stat().st_mtime)
        cutoff = time.time() - self.retention_days * 86400
        for path in files:
            if path.stat().st_mtime < cutoff:
                path.unlink()
        files = sorted(self.directory.glob("*.json"), key=lambda item: item.stat().st_mtime)
        total = sum(path.stat().st_size for path in files)
        for path in files:
            if total <= self.max_total_bytes:
                break
            total -= path.stat().st_size
            path.unlink()


def _private_directory(directory: str | Path) -> Path:
    path = Path(directory).expanduser().resolve()
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    mode = path.stat().st_mode & 0o777
    if mode & 0o077:
        raise PermissionError("audit directory must not be accessible by group or other users")
    os.chmod(path, 0o700)
    return path


def _to_audit_primitive(value: Any) -> Any:
    value = to_primitive(value)
    if isinstance(value, bytes):
        return {"__audit_bytes__": base64.b64encode(value).decode("ascii")}
    if isinstance(value, dict):
        return {str(key): _to_audit_primitive(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_to_audit_primitive(item) for item in value]
    return value


def _from_audit_primitive(value: Any) -> Any:
    if isinstance(value, dict) and set(value) == {"__audit_bytes__"}:
        return base64.b64decode(value["__audit_bytes__"])
    if isinstance(value, dict):
        return {key: _from_audit_primitive(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_from_audit_primitive(item) for item in value]
    return value
