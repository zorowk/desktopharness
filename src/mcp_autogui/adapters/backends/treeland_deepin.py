"""Treeland/Deepin desktop bundle for the generic desktop harness."""

from __future__ import annotations

from collections.abc import Callable
import subprocess
from threading import RLock
from typing import Any

from ..compositor import TreelandAdapter
from ..compositor.treeland import read_treeland_tree
from ..executor import PyAutoGUIExecutor
from ..frame import PyAutoGUIFrameProvider
from ..platform import DeepinKeybindingProvider
from ...core.models import (
    ActionProposal,
    ActionType,
    ExecutionReceipt,
    ExecutionStatus,
    Point,
    new_id,
    utc_now,
)
from ...core.store import ObjectStore
from ...desktop_backend import DesktopBackend
from ...desktop_capabilities import (
    find_capability,
    load_desktop_application_catalogue,
    load_keybinding_catalogue,
    validate_application_id,
)
from ...coordinate_mapping import screenshot_to_desktop_point
from ..compositor.treeland import desktop_bounds_from_treeland, flatten_treeland_windows


BACKEND_ID = "treeland-deepin"
WINDOW_RESIZE_HANDLE_PX = 12.0


class DdeApplicationLauncher:
    """Deepin application launcher owned by the Treeland/Deepin backend."""

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


def create_backend(
    *,
    artifact_store: ObjectStore,
    tree_reader: Callable[[], dict[str, Any]] | None = None,
    cursor_reader: Callable[[], Any] | None = None,
    capability_loader: Callable[[], list[dict[str, Any]]] | None = None,
    capability_resolver: Callable[[str], dict[str, Any] | None] | None = None,
    input_module: Any | None = None,
) -> DesktopBackend:
    """Construct the complete Treeland/Deepin port bundle."""
    if input_module is None:
        import pyautogui as input_module
    if tree_reader is None:
        tree_reader = read_treeland_tree
    if cursor_reader is None:
        cursor_reader = input_module.position

    compositor = TreelandAdapter(
        tree_reader=tree_reader,
        cursor_reader=cursor_reader,
        artifact_store=artifact_store,
    )
    application_launcher = DdeApplicationLauncher()
    capability_loader = capability_loader or load_keybinding_catalogue
    capability_resolver = capability_resolver or find_capability
    platform_provider = DeepinKeybindingProvider(loader=capability_loader, resolver=capability_resolver)

    def coordinate_mapper(point: Point, coordinate_space: str, proposal: ActionProposal) -> Point:
        if coordinate_space != "desktop-logical":
            raise ValueError("unsupported executor coordinate space")
        current = artifact_store.require(proposal.based_on_snapshot)
        width, height = input_module.size()
        bounds = current.coordinate_space.bounds
        return Point(
            (point.x - bounds.x) * float(width) / bounds.width,
            (point.y - bounds.y) * float(height) / bounds.height,
        )

    def drag_handler(proposal: ActionProposal, point: Point) -> bool:
        width, height = input_module.size()
        source_x, source_y = input_module.position()
        tree = tree_reader()
        bounds = desktop_bounds_from_treeland(tree)
        source = screenshot_to_desktop_point(
            {"x": float(source_x), "y": float(source_y)}, int(width), int(height), bounds
        )
        active_windows = [
            window for window in flatten_treeland_windows(tree) if window.get("active") is True
        ]
        if len(active_windows) != 1:
            raise ValueError("drag_active_window_ambiguous")
        window = active_windows[0]
        identity = {
            key: window.get(key)
            for key in ("appId", "title", "container", "workspace")
        }
        matches = [
            item
            for item in flatten_treeland_windows(tree)
            if all(item.get(key) == value for key, value in identity.items())
        ]
        if len(matches) != 1:
            raise ValueError("drag_source_window_ambiguous")
        geometry = window.get("geometry") or {}
        titlebar = window.get("titlebarGeometry") or {}
        titlebar_width = float(titlebar.get("width") or 0)
        titlebar_height = float(titlebar.get("height") or 0)
        left = float(geometry.get("x") or 0) + float(titlebar.get("x") or 0)
        top = float(geometry.get("y") or 0) + float(titlebar.get("y") or 0)
        on_titlebar = (
            titlebar_width > 0
            and titlebar_height > 0
            and left <= source["x"] < left + titlebar_width
            and top <= source["y"] < top + titlebar_height
        )
        if on_titlebar:
            input_module.hotkey("alt", "f7")
            input_module.moveTo(point.x, point.y)
            input_module.click(point.x, point.y)
            return True
        window_left = float(geometry.get("x") or 0)
        window_top = float(geometry.get("y") or 0)
        window_right = window_left + float(geometry.get("width") or 0)
        window_bottom = window_top + float(geometry.get("height") or 0)
        on_resize_border = (
            window_right > window_left
            and window_bottom > window_top
            and window_left - WINDOW_RESIZE_HANDLE_PX <= source["x"] <= window_right + WINDOW_RESIZE_HANDLE_PX
            and window_top - WINDOW_RESIZE_HANDLE_PX <= source["y"] <= window_bottom + WINDOW_RESIZE_HANDLE_PX
            and (
                abs(source["x"] - window_left) <= WINDOW_RESIZE_HANDLE_PX
                or abs(source["x"] - window_right) <= WINDOW_RESIZE_HANDLE_PX
                or abs(source["y"] - window_top) <= WINDOW_RESIZE_HANDLE_PX
                or abs(source["y"] - window_bottom) <= WINDOW_RESIZE_HANDLE_PX
            )
        )
        if on_resize_border:
            input_module.dragTo(
                point.x,
                point.y,
                duration=proposal.action.parameters.get("duration", 0.5),
                button=proposal.action.parameters.get("button", "left"),
            )
            return True
        raise ValueError("drag_source_not_on_titlebar_or_resize_border")

    def active_window_summary(tree: object) -> dict[str, object] | None:
        if not isinstance(tree, dict):
            return None
        for window in flatten_treeland_windows(tree):
            if window.get("active") is True:
                return {
                    "appId": window.get("appId"),
                    "title": window.get("title"),
                    "container": window.get("container"),
                    "workspace": window.get("workspace"),
                }
        return None

    return DesktopBackend(
        backend_id=BACKEND_ID,
        compositor=compositor,
        executor=PyAutoGUIExecutor(
            input_module,
            coordinate_mapper=coordinate_mapper,
            platform_resolver=platform_provider.resolve,
            drag_handler=drag_handler,
        ),
        frame_provider=PyAutoGUIFrameProvider(input_module, artifact_store),
        read_observation_state=tree_reader,
        capture_observation=lambda: _capture_observation(input_module, tree_reader),
        active_window_summary=active_window_summary,
        application_launcher=application_launcher,
        policy_providers=(platform_provider,),
        list_capabilities=capability_loader,
        find_capability=capability_resolver,
        list_applications=load_desktop_application_catalogue,
        validate_application_id=validate_application_id,
        platform_resolver=platform_provider.resolve,
    )


def _capture_observation(
    input_module: Any, tree_reader: Callable[[], dict[str, Any]]
) -> tuple[bytes, tuple[int, int], object]:
    import io

    screenshot = input_module.screenshot().convert("RGB")
    buffer = io.BytesIO()
    screenshot.save(buffer, format="PNG")
    return buffer.getvalue(), screenshot.size, tree_reader()
