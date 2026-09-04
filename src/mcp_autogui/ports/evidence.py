from __future__ import annotations

from typing import Protocol, Sequence

from ..core.models import AssertionSpec, CanonicalSnapshot, EvidenceRecord


class EvidenceProvider(Protocol):
    @property
    def provider_id(self) -> str: ...

    @property
    def fact_paths(self) -> frozenset[str]: ...

    def collect(
        self, assertions: Sequence[AssertionSpec], snapshot: CanonicalSnapshot
    ) -> Sequence[EvidenceRecord]: ...

