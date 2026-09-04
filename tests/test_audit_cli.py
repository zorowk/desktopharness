import contextlib
import io
import json
import tarfile
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from mcp_autogui.audit_cli import _load_object, _open_archive, _print_json, create_archive, extract, main, summary, timeline
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
            self.assertIn("archive_bytes:", rendered)

            image = f"{directory}/extracted.png"
            extract(Path(directory), "image-1", image)
            with open(image, "rb") as handle:
                self.assertEqual(handle.read(), b"png-data")

    def test_portable_compressed_archive_can_be_opened_without_manual_extraction(self):
        with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory() as destination:
            store = JsonAuditObjectStore(directory)
            store.put({"event": "preserved"}, object_ref="object-1")
            store.put(b"raw-artifact", object_ref="artifact-1")
            CsvAuditEventLedger(directory).append("task-1", "event", "evidence", "object-1")
            archive = create_archive(Path(directory), f"{destination}/audit-copy")

            self.assertTrue(archive.name.endswith(".tar.gz"))
            with _open_archive(archive) as extracted:
                self.assertEqual(_load_object(extracted, "object-1"), {"event": "preserved"})
                extract(extracted, "artifact-1", f"{destination}/restored.bin")
                self.assertEqual(Path(f"{destination}/restored.bin").read_bytes(), b"raw-artifact")

    def test_portable_archive_rejects_a_manifest_with_missing_members(self):
        with tempfile.TemporaryDirectory() as directory:
            archive_path = Path(directory) / "invalid.tar.gz"
            payload = b"task_id,sequence\n"
            with tarfile.open(archive_path, "w:gz") as archive:
                ledger = tarfile.TarInfo("ledger.csv")
                ledger.size = len(payload)
                archive.addfile(ledger, io.BytesIO(payload))
                manifest = json.dumps({"schema_version": 1, "files": {}}).encode()
                manifest_info = tarfile.TarInfo("manifest.json")
                manifest_info.size = len(manifest)
                archive.addfile(manifest_info, io.BytesIO(manifest))

            with self.assertRaisesRegex(ValueError, "manifest does not match"):
                with _open_archive(archive_path):
                    pass
