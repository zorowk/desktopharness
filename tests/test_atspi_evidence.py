import unittest

from mcp_autogui.adapters.evidence.atspi import AtSpiEvidenceProvider
from mcp_autogui.core.models import AssertionSpec

from test_v2_core import snapshot


class FakeText:
    characterCount = 4

    def getText(self, _start, _end):
        return "Save"


class Accessible:
    def __init__(self, name="", role="", children=()):
        self.name = name
        self._role = role
        self._children = children

    def __iter__(self):
        return iter(self._children)

    def getRoleName(self):
        return self._role

    def queryText(self):
        if self.name == "Save":
            return FakeText()
        raise RuntimeError("not text")

    def queryValue(self):
        raise RuntimeError("not value")


class AtSpiEvidenceTests(unittest.TestCase):
    def test_collects_semantically_located_accessible_control(self):
        provider = AtSpiEvidenceProvider(
            lambda: Accessible(children=(Accessible("Save", "push button"),))
        )
        records = provider.collect(
            [
                AssertionSpec(
                    "save",
                    "control.role",
                    "equals",
                    "push button",
                    subject={"control_locator": {"name": " save ", "role": "PUSH BUTTON"}},
                )
            ],
            snapshot(),
        )

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].provider, "atspi-accessibility")
        self.assertEqual(records[0].facts, {"control.role": "push button"})
        self.assertEqual(
            records[0].subject["control_locator"],
            {"name": "save", "role": "push button"},
        )

    def test_unavailable_or_ambiguous_accessibility_tree_yields_unknown_evidence(self):
        assertion = AssertionSpec(
            "save",
            "control.name",
            "equals",
            "Save",
            subject={"control_locator": {"name": "Save"}},
        )
        unavailable = AtSpiEvidenceProvider(lambda: (_ for _ in ()).throw(RuntimeError("no bus")))
        self.assertEqual(unavailable.collect([assertion], snapshot()), ())

        duplicate = AtSpiEvidenceProvider(
            lambda: Accessible(children=(Accessible("Save", "button"), Accessible("Save", "button")))
        )
        self.assertEqual(duplicate.collect([assertion], snapshot()), ())


if __name__ == "__main__":
    unittest.main()
