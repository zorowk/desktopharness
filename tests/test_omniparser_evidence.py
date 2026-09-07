import unittest

from mcp_autogui.adapters.evidence.omniparser import OmniParserEvidenceProvider
from mcp_autogui.core.models import AssertionSpec
from mcp_autogui.core.store import ObjectStore

from test_v2_core import snapshot


class Response:
    def raise_for_status(self):
        return None

    def json(self):
        return {
            "parsed_content_list": [
                {"type": "text", "content": "Hello"},
                {"type": "button", "text": "Save"},
            ],
            "private_server_field": "kept-out-of-evidence",
        }


class OmniParserEvidenceTests(unittest.TestCase):
    def provider(self):
        calls = []

        def post(*args, **kwargs):
            calls.append((args, kwargs))
            return Response()

        return OmniParserEvidenceProvider(
            "parser.example:8000", lambda: b"png", ObjectStore(), request_post=post
        ), calls

    def test_does_not_capture_when_no_supported_fact_is_requested(self):
        provider, calls = self.provider()
        records = provider.collect(
            [AssertionSpec("window", "active_window.title", "equals", "Editor")], snapshot()
        )
        self.assertEqual(records, ())
        self.assertEqual(calls, [])

    def test_emits_scoped_control_and_document_evidence_with_raw_artifact_reference(self):
        provider, calls = self.provider()
        records = provider.collect(
            [
                AssertionSpec("document", "document.text", "contains", "Save"),
                AssertionSpec(
                    "button", "control.name", "equals", "Save",
                    subject={"control_locator": {"name": " save ", "role": "BUTTON"}},
                ),
            ],
            snapshot(),
        )
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][0][0], "http://parser.example:8000/parse/")
        self.assertEqual(records[0].facts, {"document.text": "Hello\nSave"})
        self.assertEqual(
            records[1].subject["control_locator"],
            {"name": "save", "role": "button"},
        )
        self.assertEqual(records[1].facts, {"control.name": "Save"})
        self.assertEqual(records[0].confidence, "probabilistic")
        self.assertIsNotNone(records[0].raw_artifact_ref)

    def test_ambiguous_control_locator_emits_no_control_evidence(self):
        class DuplicateResponse(Response):
            def json(self):
                return {"parsed_content_list": [{"type": "button", "text": "Save"}] * 2}

        provider, _ = self.provider()
        provider.request_post = lambda *_args, **_kwargs: DuplicateResponse()
        records = provider.collect(
            [
                AssertionSpec(
                    "button",
                    "control.name",
                    "equals",
                    "Save",
                    subject={"control_locator": {"name": "Save", "role": "button"}},
                )
            ],
            snapshot(),
        )

        self.assertEqual(records, ())
