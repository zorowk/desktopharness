"""Desktop-backend assembly outside the compositor-neutral Core."""

from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Callable
from typing import Any

from .core.store import ObjectStore
from .ports.application_launcher import ApplicationLauncher
from .ports.compositor import CompositorAdapter
from .ports.executor import InputExecutor
from .ports.frame import FrameProvider
from .ports.policy import PolicyProvider


DEFAULT_DESKTOP_BACKEND = "treeland-deepin"


@dataclass(frozen=True)
class DesktopBackend:
    """Ports contributed by one desktop-session backend."""

    backend_id: str
    compositor: CompositorAdapter
    executor: InputExecutor
    frame_provider: FrameProvider
    read_observation_state: Callable[[], object]
    capture_observation: Callable[[], tuple[bytes, tuple[int, int], object]]
    active_window_summary: Callable[[object], dict[str, object] | None]
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


def create_desktop_backend(
    backend_id: str,
    *,
    artifact_store: ObjectStore,
    **backend_options: Any,
) -> DesktopBackend:
    """Create one explicitly selected desktop backend from the registry."""
    factory = _BACKEND_FACTORIES.get(backend_id)
    if factory is None:
        choices = ", ".join(available_desktop_backends())
        raise ValueError(f"unsupported desktop backend {backend_id!r}; available: {choices}")
    return factory(artifact_store=artifact_store, **backend_options)


from .adapters.backends import register_builtin_backends

register_builtin_backends()
