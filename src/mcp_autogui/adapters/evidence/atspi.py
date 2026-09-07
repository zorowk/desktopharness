"""Optional AT-SPI evidence adapter for accessible Linux desktop controls."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from typing import Any

from ...core.facts import STANDARD_FACT_PATHS
from ...core.models import (
    AssertionSpec,
    CanonicalSnapshot,
    EvidenceConfidence,
    EvidenceRecord,
    new_id,
    utc_now,
)


class AtSpiEvidenceProvider:
    """Read accessible controls without making AT-SPI a runtime prerequisite."""

    provider_id = "atspi-accessibility"
    fact_paths = frozenset({"control.name", "control.role", "control.value", "document.text"})

    def __init__(self, desktop_reader: Callable[[], Any] | None = None) -> None:
        if not self.fact_paths <= STANDARD_FACT_PATHS:
            raise ValueError("provider declared an unregistered fact path")
        self.desktop_reader = desktop_reader or self._desktop_from_atspi

    @classmethod
    def available(cls) -> bool:
        try:
            cls._desktop_from_atspi()
        except Exception:
            return False
        return True

    def collect(
        self, assertions: Sequence[AssertionSpec], snapshot: CanonicalSnapshot
    ) -> Sequence[EvidenceRecord]:
        requested = {assertion.path for assertion in assertions} & self.fact_paths
        if not requested:
            return ()
        try:
            controls = tuple(self._controls(self.desktop_reader()))
        except Exception:
            # AT-SPI is optional: an unavailable or restarting bus must leave
            # the assertion unknown rather than break the transaction.
            return ()

        subject = {
            "snapshot_id": snapshot.snapshot_id,
            "environment_version": snapshot.environment_version,
        }
        records: list[EvidenceRecord] = []
        if "document.text" in requested:
            text = "\n".join(item["text"] for item in controls if item["text"])
            if text:
                records.append(self._record(snapshot, subject, {"document.text": text}))

        for locator in self._requested_control_locators(assertions):
            matches = [item for item in controls if self._matches_locator(item, locator)]
            if len(matches) != 1:
                continue
            facts = self._control_facts(matches[0], requested)
            if facts:
                records.append(
                    self._record(snapshot, {**subject, "control_locator": locator}, facts)
                )
        return tuple(records)

    @staticmethod
    def _desktop_from_atspi() -> Any:
        import pyatspi

        return pyatspi.Registry.getDesktop(0)

    @classmethod
    def _controls(cls, desktop: Any) -> Iterable[dict[str, str | None]]:
        for accessible in cls._walk(desktop):
            name = cls._string(getattr(accessible, "name", None))
            role = cls._role(accessible)
            text = cls._text(accessible)
            value = cls._value(accessible)
            if name or role or text or value:
                yield {"name": name, "role": role, "text": text, "value": value}

    @classmethod
    def _walk(cls, root: Any) -> Iterable[Any]:
        yield root
        try:
            children = tuple(root)
        except Exception:
            return
        for child in children:
            yield from cls._walk(child)

    @staticmethod
    def _role(accessible: Any) -> str | None:
        getter = getattr(accessible, "getRoleName", None)
        try:
            return AtSpiEvidenceProvider._string(getter() if callable(getter) else None)
        except Exception:
            return None

    @staticmethod
    def _text(accessible: Any) -> str | None:
        query = getattr(accessible, "queryText", None)
        try:
            text = query()
            return AtSpiEvidenceProvider._string(text.getText(0, text.characterCount))
        except Exception:
            return None

    @staticmethod
    def _value(accessible: Any) -> str | None:
        query = getattr(accessible, "queryValue", None)
        try:
            return AtSpiEvidenceProvider._string(str(query().currentValue))
        except Exception:
            return None

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
            name, role = candidate.get("name"), candidate.get("role")
            if not isinstance(name, str) or not name.strip():
                continue
            locator = {"name": cls._normalise(name)}
            if isinstance(role, str) and role.strip():
                locator["role"] = cls._normalise(role)
            if locator not in locators:
                locators.append(locator)
        return tuple(locators)

    @classmethod
    def _matches_locator(cls, control: Mapping[str, str | None], locator: Mapping[str, str]) -> bool:
        if cls._normalise(control.get("name") or "") != locator["name"]:
            return False
        role = locator.get("role")
        return role is None or cls._normalise(control.get("role") or "") == role

    @staticmethod
    def _control_facts(control: Mapping[str, str | None], requested: set[str]) -> dict[str, str]:
        fields = {
            "control.name": control.get("name"),
            "control.role": control.get("role"),
            "control.value": control.get("value") or control.get("text"),
        }
        return {path: value for path, value in fields.items() if path in requested and value}

    @staticmethod
    def _string(value: Any) -> str | None:
        return value.strip() if isinstance(value, str) and value.strip() else None

    @staticmethod
    def _normalise(value: str) -> str:
        return " ".join(value.casefold().split())

    def _record(
        self, snapshot: CanonicalSnapshot, subject: Mapping[str, Any], facts: Mapping[str, str]
    ) -> EvidenceRecord:
        return EvidenceRecord(
            evidence_id=new_id("evidence"), provider=self.provider_id, collected_at=utc_now(),
            subject=subject, facts=facts, confidence=EvidenceConfidence.DETERMINISTIC,
            method="atspi-accessibility", valid_at_collection=True,
            expires_on_environment_change=True, operation_id=new_id("collect"),
        )
