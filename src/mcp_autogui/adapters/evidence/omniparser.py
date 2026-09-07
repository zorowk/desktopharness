"""Read-only OmniParser grounding adapter for the v2 evidence protocol.

The adapter deliberately has no action methods.  It captures a fresh frame,
asks the configured OmniParser service to parse it, and keeps the unnormalised
response behind an object-store reference for diagnostics.
"""

from __future__ import annotations

import base64
from collections.abc import Callable, Mapping, Sequence
from typing import Any

import requests

from ...core.facts import STANDARD_FACT_PATHS
from ...core.models import (
    AssertionSpec,
    CanonicalSnapshot,
    EvidenceConfidence,
    EvidenceRecord,
    new_id,
    utc_now,
)
from ...core.store import ObjectStore


class OmniParserEvidenceProvider:
    """Expose OCR/vision observations as probabilistic, expiring evidence."""

    provider_id = "omniparser-grounding"
    fact_paths = frozenset({"control.name", "control.role", "control.value", "document.text"})

    def __init__(
        self,
        endpoint: str,
        capture_png: Callable[[], bytes],
        artifact_store: ObjectStore,
        *,
        request_post: Callable[..., Any] = requests.post,
        timeout_s: float = 15.0,
    ) -> None:
        if not endpoint:
            raise ValueError("OmniParser endpoint must not be empty")
        if timeout_s <= 0:
            raise ValueError("OmniParser timeout must be positive")
        if not self.fact_paths <= STANDARD_FACT_PATHS:
            raise ValueError("provider declared an unregistered fact path")
        base_url = endpoint if endpoint.startswith(("http://", "https://")) else f"http://{endpoint}"
        self.endpoint = base_url.rstrip("/") + "/parse/"
        self.capture_png = capture_png
        self.artifact_store = artifact_store
        self.request_post = request_post
        self.timeout_s = timeout_s

    def collect(
        self, assertions: Sequence[AssertionSpec], snapshot: CanonicalSnapshot
    ) -> Sequence[EvidenceRecord]:
        requested = {assertion.path for assertion in assertions} & self.fact_paths
        if not requested:
            return ()
        image = self.capture_png()
        if not isinstance(image, bytes) or not image:
            raise ValueError("OmniParser frame capture must return non-empty PNG bytes")
        response = self.request_post(
            self.endpoint,
            json={"base64_image": base64.b64encode(image).decode("ascii")},
            timeout=self.timeout_s,
        )
        response.raise_for_status()
        payload = response.json()
        elements = payload.get("parsed_content_list")
        if not isinstance(elements, list):
            raise ValueError("OmniParser response lacks parsed_content_list")
        artifact_ref = self.artifact_store.put(payload, prefix="omniparser-raw")
        subject = {
            "snapshot_id": snapshot.snapshot_id,
            "environment_version": snapshot.environment_version,
        }
        records: list[EvidenceRecord] = []
        text = self._document_text(elements)
        if "document.text" in requested and text:
            records.append(self._record(snapshot, subject, {"document.text": text}, artifact_ref))

        for locator in self._requested_control_locators(assertions):
            matches = [
                element
                for element in elements
                if isinstance(element, Mapping) and self._matches_locator(element, locator)
            ]
            # A semantic locator must resolve to exactly one control in the
            # current frame.  Ambiguous controls deliberately yield no fact.
            if len(matches) != 1:
                continue
            facts = self._control_facts(matches[0], requested)
            if facts:
                records.append(
                    self._record(
                        snapshot,
                        {**subject, "control_locator": locator},
                        facts,
                        artifact_ref,
                    )
                )
        return tuple(records)

    @classmethod
    def _requested_control_locators(
        cls, assertions: Sequence[AssertionSpec]
    ) -> tuple[dict[str, str], ...]:
        locators: list[dict[str, str]] = []
        for assertion in assertions:
            if assertion.path not in cls.fact_paths - {"document.text"}:
                continue
            candidate = assertion.subject.get("control_locator")
            if not isinstance(candidate, Mapping):
                continue
            name = candidate.get("name")
            role = candidate.get("role")
            if not isinstance(name, str) or not name.strip():
                continue
            locator = {"name": cls._normalise(name)}
            if isinstance(role, str) and role.strip():
                locator["role"] = cls._normalise(role)
            if locator not in locators:
                locators.append(locator)
        return tuple(locators)

    @classmethod
    def _matches_locator(cls, element: Mapping[str, Any], locator: Mapping[str, str]) -> bool:
        text = cls._text(element)
        if text is None or cls._normalise(text) != locator["name"]:
            return False
        requested_role = locator.get("role")
        if requested_role is None:
            return True
        role = element.get("type") or element.get("role")
        return isinstance(role, str) and cls._normalise(role) == requested_role

    @staticmethod
    def _normalise(value: str) -> str:
        return " ".join(value.casefold().split())

    @staticmethod
    def _document_text(elements: Sequence[Any]) -> str:
        values = []
        for element in elements:
            if not isinstance(element, Mapping):
                continue
            value = OmniParserEvidenceProvider._text(element)
            if value:
                values.append(value)
        return "\n".join(values)

    @staticmethod
    def _control_facts(element: Mapping[str, Any], requested: set[str]) -> dict[str, Any]:
        text = OmniParserEvidenceProvider._text(element)
        role = element.get("type") or element.get("role")
        facts: dict[str, Any] = {}
        if "control.name" in requested and text:
            facts["control.name"] = text
        if "control.value" in requested and text:
            facts["control.value"] = text
        if "control.role" in requested and isinstance(role, str) and role.strip():
            facts["control.role"] = role.strip()
        return facts

    @staticmethod
    def _text(element: Mapping[str, Any]) -> str | None:
        for key in ("content", "text", "label"):
            value = element.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return None

    def _record(
        self,
        snapshot: CanonicalSnapshot,
        subject: Mapping[str, Any],
        facts: Mapping[str, Any],
        artifact_ref: str,
    ) -> EvidenceRecord:
        return EvidenceRecord(
            evidence_id=new_id("evidence"),
            provider=self.provider_id,
            collected_at=utc_now(),
            subject=subject,
            facts=facts,
            confidence=EvidenceConfidence.PROBABILISTIC,
            method="omniparser-http",
            valid_at_collection=True,
            expires_on_environment_change=True,
            operation_id=new_id("collect"),
            raw_artifact_ref=artifact_ref,
        )
