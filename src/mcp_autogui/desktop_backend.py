"""Desktop-backend assembly outside the compositor-neutral Core."""

from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Callable
from typing import Any

from .adapters.compositor import TreelandAdapter
from .adapters.platform import DeepinKeybindingProvider
from .core.store import ObjectStore
from .ports.application_launcher import ApplicationLauncher
from .ports.compositor import CompositorAdapter
from .ports.policy import PolicyProvider


DEFAULT_DESKTOP_BACKEND = "treeland-deepin"


@dataclass(frozen=True)
class DesktopBackend:
    """Ports contributed by one desktop-session backend."""

    backend_id: str
    compositor: CompositorAdapter
    application_launcher: ApplicationLauncher | None
    policy_providers: tuple[PolicyProvider, ...]
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
    capability_loader: Callable[[], list[dict[str, Any]]],
    capability_resolver: Callable[[str], dict[str, Any] | None],
) -> DesktopBackend:
    compositor = TreelandAdapter(
        tree_reader=tree_reader,
        cursor_reader=cursor_reader,
        artifact_store=artifact_store,
    )
    platform_provider = DeepinKeybindingProvider(
        loader=capability_loader,
        resolver=capability_resolver,
    )
    return DesktopBackend(
        backend_id=DEFAULT_DESKTOP_BACKEND,
        compositor=compositor,
        application_launcher=compositor.application_launcher,
        policy_providers=(platform_provider,),
        platform_resolver=platform_provider.resolve,
    )


def create_desktop_backend(
    backend_id: str,
    *,
    tree_reader: Callable[[], dict[str, Any]],
    cursor_reader: Callable[[], Any],
    artifact_store: ObjectStore,
    capability_loader: Callable[[], list[dict[str, Any]]],
    capability_resolver: Callable[[str], dict[str, Any] | None],
) -> DesktopBackend:
    """Create one explicitly selected desktop backend from the registry."""
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
    )


register_desktop_backend(DEFAULT_DESKTOP_BACKEND, _create_treeland_deepin_backend)
