"""Optional read-only Wayland clipboard evidence via wl-clipboard."""

from __future__ import annotations

import subprocess
from collections.abc import Callable, Sequence

from ...core.facts import STANDARD_FACT_PATHS
from ...core.models import (
    AssertionSpec,
    CanonicalSnapshot,
    EvidenceConfidence,
    EvidenceRecord,
    new_id,
    utc_now,
)


class WlClipboardEvidenceProvider:
    """Collect plain-text clipboard evidence without changing clipboard state."""

    provider_id = "wl-clipboard"
    fact_paths = frozenset({"clipboard.text"})

    def __init__(self, reader: Callable[[], str] | None = None) -> None:
        if not self.fact_paths <= STANDARD_FACT_PATHS:
            raise ValueError("provider declared an unregistered fact path")
        self._reader = reader or self._read_text

    @staticmethod
    def _read_text() -> str:
        result = subprocess.run(
            ["wl-paste", "--no-newline"],
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        )
        return result.stdout

    def collect(
        self, assertions: Sequence[AssertionSpec], snapshot: CanonicalSnapshot
    ) -> Sequence[EvidenceRecord]:
        if not any(item.path == "clipboard.text" for item in assertions):
            return ()
        try:
            text = self._reader()
        except (OSError, subprocess.SubprocessError):
            return ()
        return (
            EvidenceRecord(
                evidence_id=new_id("evidence"),
                source=self.provider_id,
                captured_at=utc_now(),
                subject={
                    "snapshot_id": snapshot.snapshot_id,
                    "environment_version": snapshot.environment_version,
                },
                facts={"clipboard.text": text},
                quality=EvidenceConfidence.DETERMINISTIC,
            ),
        )
