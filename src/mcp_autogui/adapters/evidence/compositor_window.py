"""Window-level evidence derived only from CanonicalSnapshot fields."""

from __future__ import annotations

from collections.abc import Sequence

from ...core.facts import STANDARD_FACT_PATHS
from ...core.models import (
    AssertionSpec,
    CanonicalSnapshot,
    EvidenceConfidence,
    EvidenceRecord,
    new_id,
    to_primitive,
    utc_now,
)


class CompositorWindowEvidenceProvider:
    provider_id = "compositor-window"
    fact_paths = frozenset(
        {
            "active_window.app_id",
            "active_window.window_id",
            "active_window.title",
            "window.exists",
            "window.geometry",
            "window.visible",
            "cursor.position",
        }
    )

    def __init__(self) -> None:
        if not self.fact_paths <= STANDARD_FACT_PATHS:
            raise ValueError("provider declared an unregistered fact path")

    def collect(
        self, assertions: Sequence[AssertionSpec], snapshot: CanonicalSnapshot
    ) -> Sequence[EvidenceRecord]:
        records: list[EvidenceRecord] = []
        active = snapshot.active_window()
        active_facts = {}
        if active is not None:
            active_facts = {"active_window.window_id": active.window_id}
            if active.app_id is not None:
                active_facts["active_window.app_id"] = active.app_id
            if active.title is not None:
                active_facts["active_window.title"] = active.title
        if snapshot.cursor is not None:
            active_facts["cursor.position"] = to_primitive(snapshot.cursor)
        if active_facts:
            records.append(self._record(snapshot, self._subject(snapshot), active_facts))

        requested_subjects = {
            str(assertion.subject["window_id"])
            for assertion in assertions
            if "window_id" in assertion.subject and assertion.path.startswith("window.")
        }
        for window_id in requested_subjects:
            window = snapshot.window(window_id)
            facts = {"window.exists": window is not None}
            if window is not None:
                facts["window.geometry"] = to_primitive(window.geometry)
                if window.visible is not None:
                    facts["window.visible"] = window.visible
            records.append(
                self._record(
                    snapshot,
                    {**self._subject(snapshot), "window_id": window_id},
                    facts,
                )
            )
        return records

    @staticmethod
    def _subject(snapshot: CanonicalSnapshot) -> dict:
        return {
            "snapshot_id": snapshot.snapshot_id,
            "environment_version": snapshot.environment_version,
        }

    def _record(self, snapshot: CanonicalSnapshot, subject: dict, facts: dict) -> EvidenceRecord:
        return EvidenceRecord(
            evidence_id=new_id("evidence"),
            provider=self.provider_id,
            collected_at=utc_now(),
            subject=subject,
            facts=facts,
            confidence=EvidenceConfidence.DETERMINISTIC,
            method="canonical-compositor-api",
            valid_at_collection=True,
            expires_on_environment_change=True,
            operation_id=new_id("collect"),
            raw_artifact_ref=snapshot.raw_artifact_ref,
        )
