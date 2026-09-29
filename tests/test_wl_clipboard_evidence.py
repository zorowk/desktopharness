import unittest

from mcp_autogui.adapters.evidence.wl_clipboard import WlClipboardEvidenceProvider
from mcp_autogui.core.models import AssertionSpec

from tests.test_v2_core import snapshot


class WlClipboardEvidenceTests(unittest.TestCase):
    def test_reads_clipboard_only_when_the_contract_requests_it(self):
        reads = []
        provider = WlClipboardEvidenceProvider(reader=lambda: reads.append(True) or "first paragraph")

        self.assertEqual(
            provider.collect([AssertionSpec("title", "active_window.title", "equals", "Editor")], snapshot()),
            (),
        )
        self.assertEqual(reads, [])

        records = provider.collect(
            [AssertionSpec("clipboard", "clipboard.text", "equals", "first paragraph")], snapshot()
        )
        self.assertEqual(reads, [True])
        self.assertEqual(records[0].facts, {"clipboard.text": "first paragraph"})
        self.assertEqual(records[0].quality, "deterministic")

    def test_unavailable_wayland_clipboard_leaves_the_fact_unknown(self):
        provider = WlClipboardEvidenceProvider(reader=lambda: (_ for _ in ()).throw(OSError("no display")))
        self.assertEqual(
            provider.collect([AssertionSpec("clipboard", "clipboard.text", "contains", "text")], snapshot()),
            (),
        )
