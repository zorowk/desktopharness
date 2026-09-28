import tempfile
import unittest
from dataclasses import replace

from mcp_autogui.core.audit import recording_components_from_config
from mcp_autogui.core.audit_recorder import AuditRecorder
from mcp_autogui.core.models import (
    AttributionEventKind,
    AttributionOwner,
    AttributionStage,
    ReasonCode,
    TaskContract,
)
from mcp_autogui.core.ledger import EventLedger
from mcp_autogui.core.orchestrator import CoreOrchestrator
from mcp_autogui.core.store import ObjectStore
from mcp_autogui.facade import AutoUIFacade
from mcp_autogui.runtime_description import RuntimeDescription
from tests.test_v2_core import FakeCompositor, FakeExecutor, click, snapshot


class RecordingModeTests(unittest.TestCase):
    def components(self, directory, *, diagnostic):
        return recording_components_from_config({
            "audit": True,
            "diagnostic": diagnostic,
            "directory": directory,
        })

    def recorder(self, components):
        return AuditRecorder(
            components.runtime_store,
            components.runtime_ledger,
            audit_store=components.audit_store,
            audit_ledger=components.audit_ledger,
            diagnostic_enabled=components.diagnostic_enabled,
        )

    def record_detailed_objects(self, recorder, components):
        components.runtime_store.put(b"desktop-tree", object_ref="raw-tree")
        observed = replace(snapshot(), raw_artifact_ref="raw-tree")
        recorder.put("task-1", observed, object_ref=observed.snapshot_id)
        recorder.append(
            "task-1", "snapshot.created", observed.snapshot_id,
            artifact_refs=("raw-tree",),
        )
        components.runtime_store.put(
            {"assistant_output": "raw model output"}, object_ref="model-output"
        )
        proposal = replace(click(), debug_ref="model-output")
        recorder.put("task-1", proposal, object_ref=proposal.proposal_id)
        recorder.append(
            "task-1", "proposal.created", proposal.proposal_id,
            debug_ref="model-output",
        )
        return observed, proposal

    def test_recording_disabled_uses_memory_only(self):
        components = recording_components_from_config(None)
        self.assertFalse(components.audit_enabled)
        self.assertFalse(components.diagnostic_enabled)
        self.assertIsNone(components.audit_store)
        self.assertIsNone(components.audit_ledger)

    def test_audit_only_persists_minimum_without_large_diagnostics(self):
        with tempfile.TemporaryDirectory() as directory:
            components = self.components(directory, diagnostic=False)
            recorder = self.recorder(components)
            observed, proposal = self.record_detailed_objects(
                recorder, components
            )
            attribution = recorder.record_attribution(
                "task-1",
                AttributionEventKind.ERROR,
                AttributionStage.EXECUTION,
                AttributionOwner.EXECUTOR,
                ReasonCode.EXECUTOR_ACTION_FAILED,
                "action failed",
                evidence_refs=("raw-tree",),
            )

            self.assertIsNone(components.audit_store.get(observed.snapshot_id))
            self.assertIsNone(components.audit_store.get("raw-tree"))
            self.assertIsNone(components.audit_store.get("model-output"))
            persisted = components.audit_store.require(proposal.proposal_id)
            self.assertIsNone(persisted.debug_ref)
            persisted_attribution = components.audit_store.require(
                attribution.attribution_id
            )
            self.assertEqual(persisted_attribution.evidence_refs, ())
            self.assertEqual(
                [event.event_type for event in components.audit_ledger.events("task-1")],
                ["proposal.created", "attribution.recorded"],
            )

    def test_diagnostic_mode_persists_trace_objects_and_artifacts(self):
        with tempfile.TemporaryDirectory() as directory:
            components = self.components(directory, diagnostic=True)
            observed, proposal = self.record_detailed_objects(
                self.recorder(components), components
            )

            self.assertIsNotNone(components.audit_store.get(observed.snapshot_id))
            self.assertEqual(components.audit_store.require("raw-tree"), b"desktop-tree")
            self.assertEqual(
                components.audit_store.require("model-output")["assistant_output"],
                "raw model output",
            )
            self.assertIsNotNone(components.audit_store.get(proposal.proposal_id))
            reference = self.recorder(components).put_diagnostic(
                {"kind": "trace"}, prefix="trace"
            )
            self.assertEqual(
                components.audit_store.require(reference), {"kind": "trace"}
            )

    def test_reset_drops_runtime_refs_but_retains_audit_history(self):
        with tempfile.TemporaryDirectory() as directory:
            components = self.components(directory, diagnostic=False)
            runtime = CoreOrchestrator(
                FakeCompositor([snapshot(), snapshot("snapshot-2")]),
                FakeExecutor(),
                store=components.runtime_store,
                ledger=components.runtime_ledger,
                audit_store=components.audit_store,
                audit_ledger=components.audit_ledger,
                diagnostic_enabled=False,
            )
            runtime.register_task(TaskContract("task-1", "test"))
            runtime.observe("task-1")
            proposal = click()
            runtime.submit_proposal("task-1", proposal)
            receipt = runtime.execute(proposal.proposal_id)
            execution_ref = receipt.execution_id

            runtime.reset("task-1")

            self.assertIsNone(runtime.store.get(proposal.proposal_id))
            self.assertIsNone(runtime.store.get(execution_ref))
            self.assertEqual(runtime.ledger.events("task-1"), ())
            self.assertIsNotNone(components.audit_store.get(proposal.proposal_id))
            self.assertIsNotNone(components.audit_store.get(execution_ref))
            events = components.audit_ledger.events("task-1")
            self.assertEqual(events[-1].event_type, "task.reset")
            event_ids = {event.event_id for event in events}
            self.assertTrue(all(
                set(event.caused_by) <= event_ids for event in events
            ))

    def test_diagnostic_requires_audit(self):
        with self.assertRaisesRegex(ValueError, "diagnostic=true requires"):
            recording_components_from_config({"audit": False, "diagnostic": True})

    def test_recording_failure_does_not_reexecute_a_delivered_action(self):
        class FailingStore(ObjectStore):
            def put(self, *args, **kwargs):
                raise OSError("archive unavailable")

        executor = FakeExecutor()
        runtime = CoreOrchestrator(
            FakeCompositor([snapshot(), snapshot("snapshot-2")]),
            executor,
            audit_store=FailingStore(),
            audit_ledger=EventLedger(),
            diagnostic_enabled=False,
        )
        runtime.register_task(TaskContract("task-1", "test"))
        runtime.observe("task-1")
        proposal = click()
        runtime.submit_proposal("task-1", proposal)

        first = runtime.execute(proposal.proposal_id)
        second = runtime.execute(proposal.proposal_id)

        self.assertIs(first, second)
        self.assertEqual(len(executor.actions), 1)
        self.assertTrue(runtime.recording_errors)
        description = RuntimeDescription.from_components(
            compositor=runtime.compositor,
            executor=runtime.executor,
            proposal_provider=None,
            frame_provider=None,
            evidence_providers=(),
            denied_actions=(),
            context_strategies={"compact", "recovery"},
            recording={"audit": True, "diagnostic": False},
        )
        response = AutoUIFacade(runtime, description).handle(
            "status", task_id="task-1"
        )
        self.assertEqual(response["recording"]["status"], "failed")


if __name__ == "__main__":
    unittest.main()
