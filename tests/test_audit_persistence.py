import os
import tempfile
import time
import unittest

from mcp_autogui.core.audit import audit_components_from_environment
from mcp_autogui.core.ledger import CsvAuditEventLedger
from mcp_autogui.core.store import JsonAuditObjectStore


class AuditPersistenceTests(unittest.TestCase):
    def test_json_objects_and_csv_events_survive_a_new_store_instance(self):
        with tempfile.TemporaryDirectory() as directory:
            store = JsonAuditObjectStore(directory)
            store.put({"answer": 42}, object_ref="object-1")
            ledger = CsvAuditEventLedger(directory)
            event = ledger.append("task-1", "task.created", "controller_contract", "object-1")

            reopened_store = JsonAuditObjectStore(directory)
            reopened_ledger = CsvAuditEventLedger(directory)
            self.assertEqual(reopened_store.require("object-1"), {"answer": 42})
            self.assertEqual(reopened_ledger.events("task-1"), (event,))
            self.assertEqual(os.stat(reopened_ledger.path).st_mode & 0o077, 0)

    def test_large_and_binary_artifacts_are_available_after_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            store = JsonAuditObjectStore(directory)
            store.put(b"image", object_ref="image-1")
            store.put({"large": "x" * 100}, object_ref="large-1")

            reopened = JsonAuditObjectStore(directory)
            self.assertEqual(reopened.require("image-1"), b"image")
            self.assertEqual(reopened.require("large-1"), {"large": "x" * 100})
            self.assertTrue((reopened.artifact_directory / "image-1.bin").is_file())

    def test_environment_factory_defaults_to_memory_and_can_enable_audit(self):
        original = os.environ.pop("GUI_AUDIT_DIR", None)
        try:
            store, ledger = audit_components_from_environment()
            self.assertNotIsInstance(store, JsonAuditObjectStore)
            self.assertNotIsInstance(ledger, CsvAuditEventLedger)
        finally:
            if original is not None:
                os.environ["GUI_AUDIT_DIR"] = original

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
