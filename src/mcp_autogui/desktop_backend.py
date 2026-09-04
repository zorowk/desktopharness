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


def available_desktop_backends() -> tuple[str, ...]:
    return (DEFAULT_DESKTOP_BACKEND,)


def create_desktop_backend(
    backend_id: str,
    *,
    tree_reader: Callable[[], dict[str, Any]],
    cursor_reader: Callable[[], Any],
    artifact_store: ObjectStore,
    capability_loader: Callable[[], list[dict[str, Any]]],
    capability_resolver: Callable[[str], dict[str, Any] | None],
) -> DesktopBackend:
    """Create one explicitly selected desktop backend.

    New platforms add a branch (or registry entry) here and hand the resulting
    ports to the composition root; they must not add platform branches to Core.
    """
    if backend_id != DEFAULT_DESKTOP_BACKEND:
        choices = ", ".join(available_desktop_backends())
        raise ValueError(f"unsupported desktop backend {backend_id!r}; available: {choices}")

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
        backend_id=backend_id,
        compositor=compositor,
        application_launcher=compositor.application_launcher,
        policy_providers=(platform_provider,),
        platform_resolver=platform_provider.resolve,
    )
