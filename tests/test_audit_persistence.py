import os
import tempfile
import time
import unittest
from pathlib import Path
from mcp_autogui.core.audit import recording_components_from_config
from mcp_autogui.core.ledger import CsvAuditEventLedger
from mcp_autogui.core.store import JsonAuditObjectStore


class AuditPersistenceTests(unittest.TestCase):
    def test_schema_1_ledger_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            ledger = CsvAuditEventLedger(directory)
            ledger.append("task-1", "task.created", "object-1")
            contents = ledger.path.read_text(encoding="utf-8")
            ledger.path.write_text(contents.replace(",2\n", ",1\n"), encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "schema_version is not supported"):
                CsvAuditEventLedger(directory)

    def test_schema_1_object_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            store = JsonAuditObjectStore(directory)
            store.put({"answer": 42}, object_ref="object-1")
            path = store.directory / "object-1.json"
            contents = path.read_text(encoding="utf-8").replace(
                '"schema_version":2', '"schema_version":1'
            )
            path.write_text(contents, encoding="utf-8")

            reopened = JsonAuditObjectStore(directory)
            with self.assertRaisesRegex(ValueError, "schema_version is not supported"):
                reopened.require("object-1")

    def test_json_objects_and_csv_events_survive_a_new_store_instance(self):
        with tempfile.TemporaryDirectory() as directory:
            store = JsonAuditObjectStore(directory)
            store.put({"answer": 42}, object_ref="object-1")
            ledger = CsvAuditEventLedger(directory)
            event = ledger.append("task-1", "task.created", "object-1")

            reopened_store = JsonAuditObjectStore(directory)
            reopened_ledger = CsvAuditEventLedger(directory)
            self.assertEqual(reopened_store.require("object-1"), {"answer": 42})
            self.assertEqual(reopened_ledger.events("task-1"), (event,))
            self.assertEqual(os.stat(reopened_ledger.path).st_mode & 0o077, 0)
            self.assertNotIn("epistemic_type", reopened_ledger.path.read_text(encoding="utf-8").splitlines()[0])

    def test_large_and_binary_artifacts_are_available_after_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            store = JsonAuditObjectStore(directory)
            store.put(b"image", object_ref="image-1")
            store.put({"large": "x" * 100}, object_ref="large-1")

            reopened = JsonAuditObjectStore(directory)
            self.assertEqual(reopened.require("image-1"), b"image")
            self.assertEqual(reopened.require("large-1"), {"large": "x" * 100})
            self.assertTrue((reopened.artifact_directory / "image-1.bin").is_file())

    def test_json_config_creates_persistent_components(self):
        with tempfile.TemporaryDirectory() as directory:
            with unittest.mock.patch.dict(os.environ, {"GUI_AUDIT_DIR": "/legacy"}):
                components = recording_components_from_config(
                    {
                        "audit": True,
                        "diagnostic": False,
                        "directory": directory,
                        "retention_days": 3,
                        "max_gib": 2,
                    }
                )
                store, ledger = components.audit_store, components.audit_ledger

            self.assertIsInstance(store, JsonAuditObjectStore)
            self.assertIsInstance(ledger, CsvAuditEventLedger)
            self.assertEqual(store.directory, Path(directory) / "objects")
            self.assertEqual(store.retention_days, 3)
            self.assertEqual(store.max_total_bytes, 2 * 1024 * 1024 * 1024)

    def test_retention_prunes_an_object_and_all_of_its_artifacts_together(self):
        with tempfile.TemporaryDirectory() as directory:
            store = JsonAuditObjectStore(directory, retention_days=7)
            store.put({"one": b"first", "two": b"second"}, object_ref="object-1")
            paths = [store.directory / "object-1.json", *store.artifact_directory.glob("object-1*.bin")]
            expired = time.time() - 2 * 86400
            for path in paths:
                os.utime(path, (expired, expired))

            JsonAuditObjectStore(directory, retention_days=1)

            self.assertFalse((store.directory / "object-1.json").exists())
            self.assertEqual(list(store.artifact_directory.glob("object-1*.bin")), [])

    def test_unserializable_value_is_saved_as_an_explanatory_stub(self):
        with tempfile.TemporaryDirectory() as directory:
            store = JsonAuditObjectStore(directory)
            store.put(object(), object_ref="opaque-1")

            reopened = JsonAuditObjectStore(directory)
            stub = reopened.require("opaque-1")["__audit_unavailable__"]
            self.assertEqual(stub["value_type"], "builtins.object")
            self.assertIn("not JSON serializable", stub["reason"])
