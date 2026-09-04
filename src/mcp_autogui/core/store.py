"""Reference stores for v2 runtime objects and optional lightweight audit."""

from __future__ import annotations

import hashlib
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
        self.artifact_directory = self.directory.parent / "artifacts"
        self.artifact_directory.mkdir(mode=0o700, exist_ok=True)
        os.chmod(self.artifact_directory, 0o700)
        self.retention_days = retention_days
        self.max_total_bytes = max_total_bytes
        self._prune()

    def put(self, value: Any, *, prefix: str = "object", object_ref: str | None = None) -> str:
        reference = super().put(value, prefix=prefix, object_ref=object_ref)
        payload = self._audit_payload(reference, value)
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
        return _from_audit_primitive(envelope.get("value"), self.artifact_directory)

    def clear(self) -> None:
        super().clear()
        for path in self.directory.glob("*.json"):
            path.unlink()
        for path in self.artifact_directory.glob("*.bin"):
            path.unlink()

    def _audit_payload(self, reference: str, value: Any) -> bytes | None:
        try:
            encoded = json.dumps(
                {
                    "schema_version": 1,
                    "stored_at": utc_now(),
                    "value": self._to_audit_primitive(reference, value, [0]),
                },
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
        except (TypeError, ValueError, RecursionError) as exc:
            self._remove_artifacts_for_reference(reference)
            encoded = json.dumps(
                {
                    "schema_version": 1,
                    "stored_at": utc_now(),
                    "value": {
                        "__audit_unavailable__": {
                            "value_type": f"{type(value).__module__}.{type(value).__qualname__}",
                            "reason": f"{type(exc).__name__}: value is not JSON serializable",
                        }
                    },
                },
                separators=(",", ":"),
            ).encode("utf-8")
        return encoded

    def _to_audit_primitive(self, reference: str, value: Any, artifact_index: list[int]) -> Any:
        value = to_primitive(value)
        if isinstance(value, bytes):
            suffix = "" if artifact_index[0] == 0 else f"-{artifact_index[0]}"
            artifact_index[0] += 1
            artifact_ref = f"{reference}{suffix}"
            self._write_artifact(artifact_ref, value)
            return {
                "__audit_artifact__": {
                    "path": f"artifacts/{artifact_ref}.bin",
                    "bytes": len(value),
                    "sha256": hashlib.sha256(value).hexdigest(),
                }
            }
        if isinstance(value, dict):
            return {
                str(key): self._to_audit_primitive(reference, item, artifact_index)
                for key, item in value.items()
            }
        if isinstance(value, list):
            return [self._to_audit_primitive(reference, item, artifact_index) for item in value]
        return value

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

    def _write_artifact(self, reference: str, payload: bytes) -> None:
        if not _SAFE_REFERENCE.fullmatch(reference):
            raise ValueError("invalid artifact reference for audit store")
        destination = self.artifact_directory / f"{reference}.bin"
        if destination.exists():
            return
        descriptor, temporary = tempfile.mkstemp(prefix=".artifact-", dir=self.artifact_directory)
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

    def _remove_artifacts_for_reference(self, reference: str) -> None:
        artifacts = [
            self.artifact_directory / f"{reference}.bin",
            *self.artifact_directory.glob(f"{reference}-*.bin"),
        ]
        for artifact in artifacts:
            if artifact.exists():
                artifact.unlink()

    def _prune(self) -> None:
        cutoff = time.time() - self.retention_days * 86400
        for created_at, paths in self._audit_bundles():
            if created_at < cutoff:
                self._remove_bundle(paths)
        bundles = self._audit_bundles()
        total = sum(path.stat().st_size for _, paths in bundles for path in paths)
        for _, paths in bundles:
            if total <= self.max_total_bytes:
                break
            total -= sum(path.stat().st_size for path in paths)
            self._remove_bundle(paths)

    def _audit_bundles(self) -> list[tuple[float, tuple[Path, ...]]]:
        bundles: list[tuple[float, tuple[Path, ...]]] = []
        referenced_artifacts: set[Path] = set()
        for object_path in self.directory.glob("*.json"):
            artifacts: list[Path] = []
            try:
                with object_path.open("r", encoding="utf-8") as handle:
                    envelope = json.load(handle)
                artifacts = self._artifact_paths(envelope.get("value"))
            except (OSError, ValueError, TypeError, json.JSONDecodeError):
                # A malformed object is retained/pruned as one independent
                # unit so cleanup never deletes unrelated evidence.
                artifacts = []
            referenced_artifacts.update(artifacts)
            bundles.append((object_path.stat().st_mtime, tuple([object_path, *artifacts])))
        for artifact_path in self.artifact_directory.glob("*.bin"):
            if artifact_path not in referenced_artifacts:
                bundles.append((artifact_path.stat().st_mtime, (artifact_path,)))
        return sorted(bundles, key=lambda item: item[0])

    def _artifact_paths(self, value: Any) -> list[Path]:
        if isinstance(value, dict):
            if set(value) == {"__audit_artifact__"}:
                metadata = value["__audit_artifact__"]
                path = metadata.get("path") if isinstance(metadata, dict) else None
                name = Path(path).name if isinstance(path, str) else ""
                candidate = self.artifact_directory / name
                return [candidate] if name and candidate.is_file() else []
            return [path for item in value.values() for path in self._artifact_paths(item)]
        if isinstance(value, list):
            return [path for item in value for path in self._artifact_paths(item)]
        return []

    @staticmethod
    def _remove_bundle(paths: tuple[Path, ...]) -> None:
        for path in paths:
            if path.exists():
                path.unlink()


def _private_directory(directory: str | Path) -> Path:
    path = Path(directory).expanduser().resolve()
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    mode = path.stat().st_mode & 0o777
    if mode & 0o077:
        raise PermissionError("audit directory must not be accessible by group or other users")
    os.chmod(path, 0o700)
    return path


def _from_audit_primitive(value: Any, artifact_directory: Path) -> Any:
    if isinstance(value, dict) and set(value) == {"__audit_artifact__"}:
        metadata = value["__audit_artifact__"]
        relative_path = metadata.get("path") if isinstance(metadata, dict) else None
        if not isinstance(relative_path, str) or not relative_path.startswith("artifacts/"):
            raise ValueError("invalid archived artifact reference")
        path = artifact_directory / Path(relative_path).name
        payload = path.read_bytes()
        if hashlib.sha256(payload).hexdigest() != metadata.get("sha256"):
            raise ValueError("archived artifact checksum mismatch")
        return payload
    if isinstance(value, dict):
        return {key: _from_audit_primitive(item, artifact_directory) for key, item in value.items()}
    if isinstance(value, list):
        return [_from_audit_primitive(item, artifact_directory) for item in value]
    return value
