"""Treeland raw-tree to finite CanonicalSnapshot adapter."""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import subprocess
from collections.abc import Callable
from threading import RLock
from typing import Any

from ...core.models import (
    ActionProposal,
    ActionType,
    AdapterCapabilities,
    AdapterDescriptor,
    CanonicalSnapshot,
    CanonicalWindowFact,
    CoordinateSpace,
    ExecutionReceipt,
    ExecutionStatus,
    OutputFact,
    Point,
    Rect,
    StackingCapabilities,
    StackingModel,
    WindowRole,
    new_id,
    utc_now,
)
from ...core.store import ObjectStore
from ...desktop_capabilities import validate_application_id
from ...spatial_fusion import desktop_bounds_from_treeland, flatten_treeland_windows


def read_treeland_tree(timeout: float = 35) -> dict[str, Any]:
    result = subprocess.run(
        ["treeland-debug", "--tree"], check=True, capture_output=True, text=True, timeout=timeout
    )
    output = result.stdout.strip()
    if not output:
        raise RuntimeError("treeland-debug --tree returned no window-tree data")
    try:
        value = json.loads(output)
    except json.JSONDecodeError as exc:
        raise RuntimeError("treeland-debug --tree returned invalid JSON") from exc
    if not isinstance(value, dict):
        raise RuntimeError("treeland-debug --tree returned a non-object tree")
    return value


class DdeApplicationLauncher:
    """Deepin session launcher supplied by the Treeland desktop backend."""

    launcher_id = "dde-am"

    def __init__(self, runner: Callable[..., object] = subprocess.run) -> None:
        self._runner = runner
        self._results: dict[str, object] = {}
        self._lock = RLock()

    def launch(self, proposal: ActionProposal) -> ExecutionReceipt:
        started = utc_now()
        status = ExecutionStatus.FAILED
        error = None
        try:
            if proposal.action.type != ActionType.APPLICATION_LAUNCH:
                raise ValueError("launcher received a non-launch proposal")
            app_id = validate_application_id(str(proposal.action.parameters.get("app_id") or ""))
            result = self._runner(
                ["dde-am", app_id], capture_output=True, text=True, timeout=10, check=False
            )
            with self._lock:
                self._results[proposal.proposal_id] = result
                while len(self._results) > 100:
                    self._results.pop(next(iter(self._results)))
            if getattr(result, "returncode", 1) != 0:
                error = "APPLICATION_LAUNCH_FAILED"
            else:
                status = ExecutionStatus.DELIVERED
        except FileNotFoundError:
            error = "CAPABILITY_UNAVAILABLE"
        except Exception as exc:
            error = f"APPLICATION_LAUNCH_{type(exc).__name__.upper()}"
        return ExecutionReceipt(
            execution_id=new_id("execution"),
            proposal_id=proposal.proposal_id,
            status=status,
            executed_action=proposal.action if status == ExecutionStatus.DELIVERED else None,
            started_at=started,
            finished_at=utc_now(),
            error_code=error,
        )

    def result_for(self, proposal_id: str) -> object | None:
        with self._lock:
            return self._results.get(proposal_id)


class TreelandAdapter:
    def __init__(
        self,
        tree_reader: Callable[[], dict[str, Any]] = read_treeland_tree,
        cursor_reader: Callable[[], Any] | None = None,
        artifact_store: ObjectStore | None = None,
        application_launcher: DdeApplicationLauncher | None = None,
    ) -> None:
        self._tree_reader = tree_reader
        self._cursor_reader = cursor_reader
        self._artifacts = artifact_store or ObjectStore()
        self._latest: CanonicalSnapshot | None = None
        self._application_launcher = application_launcher or DdeApplicationLauncher()

    @property
    def descriptor(self) -> AdapterDescriptor:
        return AdapterDescriptor(
            adapter_id="treeland",
            capabilities=AdapterCapabilities(
                window_tree=True,
                cursor_position=self._cursor_reader is not None,
                desktop_geometry=True,
                stacking=StackingCapabilities(
                    model=StackingModel.HIT_TEST,
                    z_index=True,
                    occlusion=True,
                    hit_test=True,
                ),
                active_window=True,
                workspace=True,
                window_identity="best-effort",
            ),
        )

    @property
    def latest_snapshot(self) -> CanonicalSnapshot | None:
        return self._latest

    @property
    def application_launcher(self) -> DdeApplicationLauncher:
        """Optional desktop-session capability exposed by this backend."""
        return self._application_launcher

    def get_window_tree(self) -> object:
        return self._tree_reader()

    def get_cursor_position(self) -> Point | None:
        if self._cursor_reader is None:
            return None
        value = self._cursor_reader()
        return Point(float(value[0]), float(value[1]))

    def get_desktop_geometry(self) -> Rect:
        tree = self._tree_reader()
        return self._bounds(tree)

    def observe(self) -> CanonicalSnapshot:
        return self.observe_raw(self._tree_reader(), self.get_cursor_position())

    def observe_raw(
        self, raw_tree: dict[str, Any], cursor: Point | None = None
    ) -> CanonicalSnapshot:
        """Normalize an already captured tree without performing another transport read."""
        raw = deepcopy(raw_tree)
        canonical_source = deepcopy(raw)
        bounds = self._bounds(canonical_source)
        raw_ref = self._artifacts.put(raw, prefix="treeland-tree")
        windows: list[CanonicalWindowFact] = []
        occurrences: dict[str, int] = {}
        for raw_window in flatten_treeland_windows(canonical_source):
            geometry = raw_window.get("geometry") or {}
            identity_seed = "|".join(
                str(raw_window.get(key) or "")
                for key in ("appId", "title", "container", "workspace", "output")
            )
            occurrences[identity_seed] = occurrences.get(identity_seed, 0) + 1
            window_id = raw_window.get("windowId") or raw_window.get("window_id") or raw_window.get("id")
            if window_id is None:
                digest = hashlib.sha256(identity_seed.encode("utf-8")).hexdigest()[:16]
                window_id = f"treeland-{digest}-{occurrences[identity_seed]}"
            windows.append(
                CanonicalWindowFact(
                    window_id=str(window_id),
                    app_id=_optional_text(raw_window.get("appId")),
                    title=_optional_text(raw_window.get("title")),
                    geometry=Rect(
                        float(geometry.get("x") or 0),
                        float(geometry.get("y") or 0),
                        float(geometry.get("width") or 0),
                        float(geometry.get("height") or 0),
                    ),
                    visible=_optional_bool(raw_window.get("visible")),
                    active=_optional_bool(raw_window.get("active")),
                    z_index=_optional_number(raw_window.get("z")),
                    workspace_id=_optional_text(raw_window.get("workspace")),
                    output_id=_optional_text(raw_window.get("output")),
                    role=_role(raw_window, raw),
                )
            )
        environment_payload = {
            "bounds": [bounds.x, bounds.y, bounds.width, bounds.height],
            "windows": [
                [w.window_id, w.app_id, w.title, w.geometry.x, w.geometry.y, w.geometry.width, w.geometry.height,
                 w.visible, w.active, w.z_index, w.workspace_id, w.output_id, w.role.value]
                for w in windows
            ],
        }
        environment_version = "sha256:" + hashlib.sha256(
            json.dumps(environment_payload, sort_keys=True).encode("utf-8")
        ).hexdigest()
        geometry_version = "sha256:" + hashlib.sha256(
            json.dumps(environment_payload["bounds"]).encode("utf-8")
        ).hexdigest()
        snapshot = CanonicalSnapshot(
            snapshot_id=new_id("snapshot"),
            captured_at=utc_now(),
            environment_version=environment_version,
            coordinate_space=CoordinateSpace("desktop-logical", bounds, geometry_version),
            outputs=(OutputFact("desktop", bounds, None),),
            cursor=cursor,
            windows=tuple(windows),
            raw_artifact_ref=raw_ref,
        )
        self._latest = snapshot
        return snapshot

    def hit_test(self, point: Point, snapshot: CanonicalSnapshot | None = None) -> str | None:
        current = snapshot or self._latest
        if current is None:
            current = self.observe()
        # TreelandAdapter emits windows in compositor front-to-back order.
        return next(
            (
                window.window_id
                for window in current.windows
                if window.visible is not False and window.geometry.contains(point)
            ),
            None,
        )

    def topmost_window_at(self, point: Point, snapshot: CanonicalSnapshot | None = None) -> str | None:
        return self.hit_test(point, snapshot)

    def is_above(
        self, window_a: str, window_b: str, snapshot: CanonicalSnapshot | None = None
    ) -> bool | None:
        del window_a, window_b, snapshot
        # Treeland's exposed z values are best-effort and do not establish a
        # portable total or partial order between arbitrary windows.
        return None

    def occluded(
        self, window_id: str, region: Rect, snapshot: CanonicalSnapshot | None = None
    ) -> bool | None:
        current = snapshot or self._latest
        if current is None:
            current = self.observe()
        target_index = next(
            (index for index, item in enumerate(current.windows) if item.window_id == window_id),
            None,
        )
        if target_index is None:
            return None
        return any(
            item.visible is not False and _rects_intersect(region, item.geometry)
            for item in current.windows[:target_index]
        )

    @staticmethod
    def _bounds(tree: dict[str, Any]) -> Rect:
        value = desktop_bounds_from_treeland(tree)
        return Rect(value["x"], value["y"], value["width"], value["height"])


def _optional_text(value: Any) -> str | None:
    if value is None or value == "":
        return None
    return str(value)


def _optional_bool(value: Any) -> bool | None:
    return value if isinstance(value, bool) else None


def _optional_number(value: Any) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return None


def _role(window: dict[str, Any], tree: dict[str, Any]) -> WindowRole:
    text = " ".join(
        str(window.get(key) or "").lower() for key in ("container", "role", "type", "appId")
    )
    if tree.get("currentMode") == "LockScreen" or "lockscreen" in text or "lock-screen" in text:
        return WindowRole.LOCKSCREEN
    if "background" in text or "desktop" in text:
        return WindowRole.DESKTOP
    if "panel" in text or "dock" in text:
        return WindowRole.PANEL
    if "overlay" in text:
        return WindowRole.OVERLAY
    if "dialog" in text:
        return WindowRole.DIALOG
    if "workspace" in text or "normal" in text:
        return WindowRole.NORMAL
    return WindowRole.UNKNOWN


def _rects_intersect(left: Rect, right: Rect) -> bool:
    return not (
        left.x + left.width <= right.x
        or right.x + right.width <= left.x
        or left.y + left.height <= right.y
        or right.y + right.height <= left.y
    )
