from __future__ import annotations

from typing import Any, Mapping, Protocol, Sequence


class PlatformCapabilityProvider(Protocol):
    def list_capabilities(self) -> Sequence[Mapping[str, Any]]: ...

    def resolve(self, capability_id: str) -> Mapping[str, Any] | None: ...

