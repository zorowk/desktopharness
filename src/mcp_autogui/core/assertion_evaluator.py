"""Pure assertion evaluation over already collected evidence."""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Any

from .facts import require_standard_fact_path
from .models import (
    AssertionResult,
    AssertionSpec,
    AssertionStatus,
    CanonicalSnapshot,
    EvidenceConfidence,
    EvidenceRecord,
    ExcludedEvidence,
    utc_now,
)


_CONFIDENCE_RANK = {
    EvidenceConfidence.DETERMINISTIC: 5,
    EvidenceConfidence.HUMAN_ANNOTATION: 5,
    EvidenceConfidence.DERIVED: 4,
    EvidenceConfidence.PROBABILISTIC: 2,
    EvidenceConfidence.MODEL_CLAIM: 1,
}
SUPPORTED_OPERATORS = frozenset(
    {"equals", "not_equals", "exists", "contains", "starts_with", "matches", "greater_than", "less_than"}
)


class AssertionEvaluator:
    def evaluate(
        self,
        assertion: AssertionSpec,
        evidence: Sequence[EvidenceRecord],
        current_snapshot: CanonicalSnapshot | None = None,
    ) -> AssertionResult:
        require_standard_fact_path(assertion.path)
        applicable: list[tuple[EvidenceRecord, Any]] = []
        excluded: list[ExcludedEvidence] = []
        for record in evidence:
            reason = self._exclusion_reason(assertion, record, current_snapshot)
            if reason is not None:
                excluded.append(ExcludedEvidence(record.evidence_id, reason))
                continue
            applicable.append((record, record.facts[assertion.path]))

        if not applicable:
            return self._result(assertion, AssertionStatus.UNKNOWN, (), "No applicable evidence", excluded)

        if assertion.providers:
            provider_rank = {provider: index for index, provider in enumerate(assertion.providers)}
            known = [item for item in applicable if item[0].provider in provider_rank]
            for record, _ in applicable:
                if record.provider not in provider_rank:
                    excluded.append(ExcludedEvidence(record.evidence_id, "provider is not allowed for assertion"))
            if not known:
                return self._result(
                    assertion,
                    AssertionStatus.UNKNOWN,
                    (),
                    "No evidence from an allowed provider",
                    excluded,
                )
            best = min(provider_rank[item[0].provider] for item in known)
            selected = [item for item in known if provider_rank[item[0].provider] == best]
            for record, _ in known:
                if (record, record.facts[assertion.path]) not in selected:
                    excluded.append(ExcludedEvidence(record.evidence_id, "lower provider priority"))
            applicable = selected
        else:
            best = max(_CONFIDENCE_RANK[item[0].confidence] for item in applicable)
            selected = [item for item in applicable if _CONFIDENCE_RANK[item[0].confidence] == best]
            for record, _ in applicable:
                if _CONFIDENCE_RANK[record.confidence] < best:
                    excluded.append(ExcludedEvidence(record.evidence_id, "lower confidence source"))
            applicable = selected

        # A model re-observation is useful diagnostic evidence, but never an
        # independent task-success source by itself.
        if all(item[0].confidence == EvidenceConfidence.MODEL_CLAIM for item in applicable):
            return self._result(
                assertion,
                AssertionStatus.UNKNOWN,
                (),
                "Only model-claim evidence is available",
                excluded + [ExcludedEvidence(item[0].evidence_id, "model claim is diagnostic only") for item in applicable],
            )

        values = [item[1] for item in applicable]
        if any(value != values[0] for value in values[1:]):
            return self._result(
                assertion,
                AssertionStatus.CONFLICT,
                tuple(item[0].evidence_id for item in applicable),
                "Equally applicable evidence conflicts",
                excluded,
            )
        try:
            passed = _apply_operator(assertion.operator, values[0], assertion.expected)
        except (TypeError, ValueError, re.error) as exc:
            return self._result(
                assertion,
                AssertionStatus.UNKNOWN,
                tuple(item[0].evidence_id for item in applicable),
                f"Assertion could not be evaluated: {exc}",
                excluded,
            )
        return self._result(
            assertion,
            AssertionStatus.PASSED if passed else AssertionStatus.FAILED,
            tuple(item[0].evidence_id for item in applicable),
            "Evidence satisfies assertion" if passed else "Evidence does not satisfy assertion",
            excluded,
        )

    @staticmethod
    def _exclusion_reason(
        assertion: AssertionSpec,
        record: EvidenceRecord,
        current_snapshot: CanonicalSnapshot | None,
    ) -> str | None:
        if assertion.path not in record.facts:
            return "fact path not provided"
        if record.valid_at_collection is not True:
            return "evidence was not valid at collection"
        for key, expected in assertion.subject.items():
            if record.subject.get(key) != expected:
                return f"subject mismatch: {key}"
        if (
            current_snapshot is not None
            and record.expires_on_environment_change is True
        ):
            evidence_environment = record.subject.get("environment_version")
            expired = (
                evidence_environment != current_snapshot.environment_version
                if evidence_environment is not None
                else record.subject.get("snapshot_id") != current_snapshot.snapshot_id
            )
            if expired:
                return "evidence expired after environment change"
        return None

    @staticmethod
    def _result(
        assertion: AssertionSpec,
        status: AssertionStatus,
        refs: tuple[str, ...],
        reason: str,
        excluded: Sequence[ExcludedEvidence],
    ) -> AssertionResult:
        return AssertionResult(
            assertion_id=assertion.assertion_id,
            expression=assertion,
            status=status,
            evidence_refs=refs,
            evaluated_at=utc_now(),
            reason=reason,
            excluded_evidence=tuple(excluded),
        )


def _apply_operator(operator: str, actual: Any, expected: Any) -> bool:
    if operator == "equals":
        return actual == expected
    if operator == "not_equals":
        return actual != expected
    if operator == "exists":
        return actual is not None if expected is None else bool(actual) is bool(expected)
    if operator == "contains":
        return expected in actual
    if operator == "starts_with":
        return actual.startswith(expected)
    if operator == "matches":
        return re.search(str(expected), str(actual)) is not None
    if operator == "greater_than":
        return actual > expected
    if operator == "less_than":
        return actual < expected
    raise ValueError(f"unsupported assertion operator: {operator}")
