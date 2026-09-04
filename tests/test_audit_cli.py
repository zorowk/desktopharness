import contextlib
import io
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from mcp_autogui.audit_cli import _load_object, _print_json, extract, main, summary, timeline
from mcp_autogui.core.ledger import CsvAuditEventLedger
from mcp_autogui.core.store import JsonAuditObjectStore


class AuditCliTests(unittest.TestCase):
    def test_directory_alone_selects_interactive_mode(self):
        with tempfile.TemporaryDirectory() as directory, patch("mcp_autogui.audit_cli.interactive") as interactive:
            self.assertEqual(main([directory]), 0)
            interactive.assert_called_once()

    def test_summary_timeline_show_and_extract(self):
        with tempfile.TemporaryDirectory() as directory:
            store = JsonAuditObjectStore(directory)
            store.put({"goal": "open editor"}, object_ref="contract-1")
            store.put(b"png-data", object_ref="image-1")
            CsvAuditEventLedger(directory).append(
                "task-1", "task.created", "controller_contract", "contract-1"
            )
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                summary(Path(directory))
                timeline(Path(directory), "task-1")
                _print_json(_load_object(Path(directory), "contract-1"))
            rendered = output.getvalue()
            self.assertIn("tasks: 1", rendered)
            self.assertIn("task.created", rendered)
            self.assertIn('"goal": "open editor"', rendered)

            image = f"{directory}/extracted.png"
            extract(Path(directory), "image-1", image)
            with open(image, "rb") as handle:
                self.assertEqual(handle.read(), b"png-data")
