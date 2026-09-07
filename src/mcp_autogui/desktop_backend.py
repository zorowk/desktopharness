"""Desktop-backend assembly outside the compositor-neutral Core."""

from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Callable
from typing import Any

from .adapters.compositor import TreelandAdapter
from .adapters.executor import PyAutoGUIExecutor
from .adapters.frame import PyAutoGUIFrameProvider
from .adapters.platform import DeepinKeybindingProvider
from .core.models import ActionProposal, Point
from .core.store import ObjectStore
from .desktop_capabilities import (
    find_capability,
    load_desktop_application_catalogue,
    load_keybinding_catalogue,
    validate_application_id,
)
from .ports.application_launcher import ApplicationLauncher
from .ports.compositor import CompositorAdapter
from .ports.executor import InputExecutor
from .ports.frame import FrameProvider
from .ports.policy import PolicyProvider
from .spatial_fusion import desktop_bounds_from_treeland, flatten_treeland_windows, screenshot_to_desktop_point


DEFAULT_DESKTOP_BACKEND = "treeland-deepin"
WINDOW_RESIZE_HANDLE_PX = 12.0


@dataclass(frozen=True)
class DesktopBackend:
    """Ports contributed by one desktop-session backend."""

    backend_id: str
    compositor: CompositorAdapter
    executor: InputExecutor
    frame_provider: FrameProvider
    read_raw_tree: Callable[[], dict[str, Any]]
    capture_observation: Callable[[], tuple[bytes, tuple[int, int], dict[str, Any]]]
    application_launcher: ApplicationLauncher | None
    policy_providers: tuple[PolicyProvider, ...]
    list_capabilities: Callable[[], list[dict[str, Any]]]
    find_capability: Callable[[str], dict[str, Any] | None]
    list_applications: Callable[[], list[dict[str, Any]]]
    validate_application_id: Callable[[str], str]
    platform_resolver: Callable[[str], dict[str, Any] | None] | None = None


DesktopBackendFactory = Callable[..., DesktopBackend]
_BACKEND_FACTORIES: dict[str, DesktopBackendFactory] = {}


def register_desktop_backend(backend_id: str, factory: DesktopBackendFactory) -> None:
    """Register one composition-root factory under a stable backend ID.

    Registration is deliberately outside Core: a new desktop adds an adapter
    bundle and calls this function during application composition.  Replacing
    an existing backend is rejected to keep configuration selection stable.
    """
    normalized = backend_id.strip()
    if not normalized:
        raise ValueError("desktop backend ID must be non-empty")
    if normalized in _BACKEND_FACTORIES:
        raise ValueError(f"desktop backend is already registered: {normalized}")
    _BACKEND_FACTORIES[normalized] = factory


def available_desktop_backends() -> tuple[str, ...]:
    return tuple(sorted(_BACKEND_FACTORIES))


def _create_treeland_deepin_backend(
    *,
    tree_reader: Callable[[], dict[str, Any]],
    cursor_reader: Callable[[], Any],
    artifact_store: ObjectStore,
    capability_loader: Callable[[], list[dict[str, Any]]] | None,
    capability_resolver: Callable[[str], dict[str, Any] | None] | None,
    input_module: Any = None,
) -> DesktopBackend:
    compositor = TreelandAdapter(
        tree_reader=tree_reader,
        cursor_reader=cursor_reader,
        artifact_store=artifact_store,
    )
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

    return DesktopBackend(
        backend_id=DEFAULT_DESKTOP_BACKEND,
        compositor=compositor,
        executor=PyAutoGUIExecutor(
            input_module,
            coordinate_mapper=coordinate_mapper,
            platform_resolver=platform_provider.resolve,
            drag_handler=drag_handler,
        ),
        frame_provider=PyAutoGUIFrameProvider(input_module, artifact_store),
        read_raw_tree=tree_reader,
        capture_observation=lambda: _capture_treeland_observation(input_module, tree_reader),
        application_launcher=compositor.application_launcher,
        policy_providers=(platform_provider,),
        list_capabilities=capability_loader,
        find_capability=capability_resolver,
        list_applications=load_desktop_application_catalogue,
        validate_application_id=validate_application_id,
        platform_resolver=platform_provider.resolve,
    )


def create_desktop_backend(
    backend_id: str,
    *,
    tree_reader: Callable[[], dict[str, Any]],
    artifact_store: ObjectStore,
    cursor_reader: Callable[[], Any] | None = None,
    capability_loader: Callable[[], list[dict[str, Any]]] | None = None,
    capability_resolver: Callable[[str], dict[str, Any] | None] | None = None,
    input_module: Any | None = None,
) -> DesktopBackend:
    """Create one explicitly selected desktop backend from the registry."""
    if input_module is None:
        import pyautogui as input_module
    if cursor_reader is None:
        cursor_reader = input_module.position
    factory = _BACKEND_FACTORIES.get(backend_id)
    if factory is None:
        choices = ", ".join(available_desktop_backends())
        raise ValueError(f"unsupported desktop backend {backend_id!r}; available: {choices}")
    return factory(
        tree_reader=tree_reader,
        cursor_reader=cursor_reader,
        artifact_store=artifact_store,
        capability_loader=capability_loader,
        capability_resolver=capability_resolver,
        input_module=input_module,
    )


register_desktop_backend(DEFAULT_DESKTOP_BACKEND, _create_treeland_deepin_backend)


def _capture_treeland_observation(
    input_module: Any, tree_reader: Callable[[], dict[str, Any]]
) -> tuple[bytes, tuple[int, int], dict[str, Any]]:
    import io

    screenshot = input_module.screenshot().convert("RGB")
    buffer = io.BytesIO()
    screenshot.save(buffer, format="PNG")
    return buffer.getvalue(), screenshot.size, tree_reader()
