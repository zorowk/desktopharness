import unittest
from dataclasses import replace

from mcp_autogui.adapters.compositor.treeland import TreelandAdapter
from mcp_autogui.adapters.compositor.canonical import CanonicalJsonAdapter
from mcp_autogui.adapters.evidence.compositor_window import CompositorWindowEvidenceProvider
from mcp_autogui.core.action_gate import ActionGate
from mcp_autogui.core.assertion_evaluator import AssertionEvaluator
from mcp_autogui.core.ledger import EventLedger
from mcp_autogui.core.context_builder import ContextBuilder
from mcp_autogui.core.models import (
    Action,
    ActionProposal,
    ActionType,
    AdapterCapabilities,
    AdapterDescriptor,
    AssertionSpec,
    AssertionStatus,
    CanonicalSnapshot,
    CanonicalWindowFact,
    CoordinateSpace,
    EvidenceConfidence,
    EvidenceRecord,
    ExecutionReceipt,
    ExecutionStatus,
    OutputFact,
    Point,
    PolicyStatus,
    Rect,
    SemanticTag,
    StackingCapabilities,
    StackingModel,
    TaskContract,
    TaskLimits,
    TaskPermissions,
    TaskState,
    TaskStatus,
    WindowRole,
    new_id,
    utc_now,
)
from mcp_autogui.core.orchestrator import CoreOrchestrator
from mcp_autogui.core.store import ObjectStore
from mcp_autogui.core.task_state import TaskStateReducer


def descriptor():
    return AdapterDescriptor(
        "fixture",
        AdapterCapabilities(
            True,
            True,
            True,
            StackingCapabilities(StackingModel.HIT_TEST, hit_test=True),
            active_window=True,
            window_identity="stable",
        ),
    )


def snapshot(*, snapshot_id="snapshot-1", target="desktop", environment="env-1", title="Desktop"):
    bounds = Rect(0, 0, 1000, 800)
    return CanonicalSnapshot(
        snapshot_id=snapshot_id,
        captured_at=utc_now(),
        environment_version=environment,
        coordinate_space=CoordinateSpace("desktop-logical", bounds, "geometry-1"),
        outputs=(OutputFact("display-1", bounds),),
        cursor=Point(20, 20),
        windows=(
            CanonicalWindowFact(
                target,
                bounds,
                app_id="desktop",
                title=title,
                visible=True,
                active=True,
                role=WindowRole.DESKTOP,
            ),
        ),
    )


def contract(*assertions, actions=None, intents=None, retries=1):
    return TaskContract(
        task_id="task-1",
        goal="test",
        permissions=TaskPermissions(
            frozenset(actions or {ActionType.POINTER_CLICK}),
            frozenset(intents or {"navigation"}),
        ),
        assertions=tuple(assertions),
        limits=TaskLimits(max_steps=5, max_retries=retries),
    )


def click_proposal(snapshot_id="snapshot-1", semantic="navigation", source="controller"):
    return ActionProposal(
        proposal_id=new_id("proposal"),
        source=source,
        based_on_snapshot=snapshot_id,
        action=Action(ActionType.POINTER_CLICK, Point(100, 100), "desktop-logical"),
        semantic_intent=semantic,
    )


class FakeCompositor:
    descriptor = descriptor()

    def __init__(self, snapshots):
        self.snapshots = list(snapshots)
        self.latest = self.snapshots[0]

    def observe(self):
        if self.snapshots:
            self.latest = self.snapshots.pop(0)
        return self.latest

    def hit_test(self, point, current=None):
        current = current or self.latest
        return next((window.window_id for window in current.windows if window.geometry.contains(point)), None)


class FakeExecutor:
    def execute(self, proposal):
        now = utc_now()
        return ExecutionReceipt(
            new_id("execution"), proposal.proposal_id, ExecutionStatus.DELIVERED,
            proposal.action, now, now,
        )


class CanonicalAdapterTests(unittest.TestCase):
    def test_treeland_adapter_filters_raw_fields_and_keeps_artifact_reference(self):
        raw = {
            "currentMode": "Normal",
            "privateCompositorField": "secret-detail",
            "layers": [{
                "name": "BackgroundContainer", "layer": -2, "workspaces": [],
                "windows": [{
                    "windowId": 42, "appId": "", "title": "", "visible": True,
                    "active": True, "z": 0, "container": "BackgroundContainer",
                    "geometry": {"x": 0, "y": 0, "width": 1000, "height": 800},
                    "titlebarGeometry": {"implementation": "must-not-leak"},
                }],
            }],
        }
        store = ObjectStore()
        adapter = TreelandAdapter(lambda: raw, lambda: (10, 20), store)

        observed = adapter.observe()

        self.assertEqual(observed.windows[0].window_id, "42")
        self.assertEqual(observed.windows[0].role, WindowRole.DESKTOP)
        self.assertIsNone(observed.windows[0].app_id)
        self.assertEqual(observed.cursor, Point(10, 20))
        self.assertEqual(store.require(observed.raw_artifact_ref)["privateCompositorField"], "secret-detail")
        self.assertFalse(hasattr(observed.windows[0], "container"))
        self.assertFalse(hasattr(observed.windows[0], "titlebarGeometry"))

    def test_second_compositor_fixture_has_the_same_canonical_semantics(self):
        bounds = {"x": 0, "y": 0, "width": 1000, "height": 800}
        raw = {
            "snapshot_id": "other-1",
            "captured_at": utc_now(),
            "environment_version": "other-env",
            "coordinate_space": {"id": "desktop-logical", "bounds": bounds},
            "outputs": [{"output_id": "display", "geometry": bounds, "vendor_extra": 1}],
            "cursor": {"x": 10, "y": 20},
            "windows": [{
                "window_id": "42", "app_id": None, "title": None, "visible": True,
                "active": True, "role": "desktop", "geometry": bounds,
                "foreign_private_data": {"must": "not leak"},
            }],
            "foreign_root_data": True,
        }
        adapter = CanonicalJsonAdapter(descriptor(), lambda: raw)
        observed = adapter.observe()
        self.assertEqual(observed.coordinate_space.bounds, Rect(0, 0, 1000, 800))
        self.assertEqual(observed.windows[0].role, WindowRole.DESKTOP)
        self.assertEqual(adapter.hit_test(Point(20, 20), observed), "42")
        self.assertFalse(hasattr(observed.windows[0], "foreign_private_data"))


class ActionGateTests(unittest.TestCase):
    def test_unrelated_snapshot_change_does_not_invalidate_guard(self):
        first = snapshot()
        gate = ActionGate(descriptor(), lambda point, snap: snap.windows[0].window_id)
        tag = SemanticTag("navigation", "test", "e-1", EvidenceConfidence.DETERMINISTIC)
        decision, guard, _ = gate.decide(click_proposal(), contract(), first, [tag])
        changed = replace(first, snapshot_id="snapshot-2", environment_version="env-animation")

        self.assertEqual(decision.status, PolicyStatus.ALLOW)
        self.assertIsNone(gate.recheck(guard, changed))

    def test_hit_target_change_invalidates_guard_with_specific_code(self):
        first = snapshot()
        current = {"target": "desktop"}
        gate = ActionGate(descriptor(), lambda point, snap: current["target"])
        tag = SemanticTag("navigation", "test", "e-1", EvidenceConfidence.DETERMINISTIC)
        _, guard, _ = gate.decide(click_proposal(), contract(), first, [tag])
        changed = replace(
            first,
            snapshot_id="snapshot-2",
            windows=first.windows + (
                CanonicalWindowFact("overlay", Rect(0, 0, 200, 200), visible=True, role=WindowRole.OVERLAY),
            ),
        )
        current["target"] = "overlay"

        self.assertEqual(gate.recheck(guard, changed), "HIT_TEST_CHANGED")

    def test_qwen_semantic_claim_alone_is_unknown_and_requires_confirmation(self):
        snap = snapshot()
        gate = ActionGate(descriptor(), lambda point, current: "desktop")
        proposal = click_proposal(source="qwen-cua")

        decision, _, resolution = gate.decide(proposal, contract(), snap)

        self.assertEqual(resolution.status, "unknown")
        self.assertEqual(decision.status, PolicyStatus.CONFIRM)
        self.assertEqual(decision.reason_code, "CONFIRMATION_REQUIRED")

    def test_desktop_role_is_not_an_automatic_click_rejection(self):
        snap = snapshot()
        gate = ActionGate(descriptor(), lambda point, current: "desktop")
        tag = SemanticTag("navigation", "controller", "e-1", EvidenceConfidence.DETERMINISTIC)

        decision, _, _ = gate.decide(click_proposal(), contract(), snap, [tag])

        self.assertEqual(decision.status, PolicyStatus.ALLOW)


class EvidenceAndStateTests(unittest.TestCase):
    def record(self, value, confidence, evidence_id):
        return EvidenceRecord(
            evidence_id, "fixture", utc_now(), {"snapshot_id": "snapshot-1"},
            {"active_window.app_id": value}, confidence, "fixture", True, False, "collect-1",
        )

    def test_model_claim_cannot_pass_assertion_by_itself(self):
        spec = AssertionSpec("opened", "active_window.app_id", "equals", "editor")
        result = AssertionEvaluator().evaluate(
            spec, [self.record("editor", EvidenceConfidence.MODEL_CLAIM, "model-1")]
        )
        self.assertEqual(result.status, AssertionStatus.UNKNOWN)

    def test_deterministic_evidence_overrides_conflicting_model_claim(self):
        spec = AssertionSpec("opened", "active_window.app_id", "equals", "editor")
        result = AssertionEvaluator().evaluate(spec, [
            self.record("editor", EvidenceConfidence.DETERMINISTIC, "api-1"),
            self.record("music", EvidenceConfidence.MODEL_CLAIM, "model-1"),
        ])
        self.assertEqual(result.status, AssertionStatus.PASSED)
        self.assertEqual(result.evidence_refs, ("api-1",))

    def test_equal_quality_conflict_is_not_passed(self):
        spec = AssertionSpec("opened", "active_window.app_id", "equals", "editor")
        result = AssertionEvaluator().evaluate(spec, [
            self.record("editor", EvidenceConfidence.DETERMINISTIC, "api-1"),
            self.record("music", EvidenceConfidence.DETERMINISTIC, "api-2"),
        ])
        self.assertEqual(result.status, AssertionStatus.CONFLICT)

    def test_assertion_provider_allowlist_is_enforced(self):
        spec = AssertionSpec(
            "opened", "active_window.app_id", "equals", "editor",
            providers=("application-api",),
        )
        result = AssertionEvaluator().evaluate(
            spec, [self.record("editor", EvidenceConfidence.DETERMINISTIC, "compositor-1")]
        )
        self.assertEqual(result.status, AssertionStatus.UNKNOWN)

    def test_reducer_alone_can_complete_task(self):
        spec = AssertionSpec("opened", "active_window.app_id", "equals", "editor")
        result = AssertionEvaluator().evaluate(
            spec, [self.record("editor", EvidenceConfidence.DETERMINISTIC, "api-1")]
        )
        reduced = TaskStateReducer().reduce(contract(spec), TaskState("task-1"), [result])
        self.assertEqual(reduced.status, TaskStatus.COMPLETED)


class OrchestratorTests(unittest.TestCase):
    def test_delivered_receipt_does_not_complete_before_evaluation(self):
        spec = AssertionSpec("desktop-active", "active_window.app_id", "equals", "desktop")
        compositor = FakeCompositor([snapshot(), replace(snapshot(), snapshot_id="snapshot-2")])
        runtime = CoreOrchestrator(
            compositor,
            FakeExecutor(),
            evidence_providers=[CompositorWindowEvidenceProvider()],
        )
        runtime.register_task(contract(spec))
        observed = runtime.observe("task-1")
        proposal = click_proposal(observed.snapshot_id)
        runtime.submit_proposal("task-1", proposal)
        tag = SemanticTag("navigation", "fixture", "e-1", EvidenceConfidence.DETERMINISTIC)
        runtime.policy_providers = (type("Policy", (), {"independent_tags": lambda self, p, c: [tag]})(),)

        receipt = runtime.execute(proposal.proposal_id)

        self.assertEqual(receipt.status, ExecutionStatus.DELIVERED)
        self.assertEqual(runtime.status("task-1").status, TaskStatus.CONTINUE)
        _, _, state = runtime.evaluate("task-1")
        self.assertEqual(state.status, TaskStatus.COMPLETED)

    def test_ledger_is_append_only_and_sequences_per_task(self):
        ledger = EventLedger()
        one = ledger.append("t", "proposal.created", "action_proposal", "p-1")
        two = ledger.append("t", "decision.created", "policy_decision", "d-1", caused_by=(one.event_id,))
        self.assertEqual((one.sequence, two.sequence), (1, 2))
        self.assertEqual(ledger.events("t")[0].object_ref, "p-1")

    def test_missing_evidence_records_a_non_error_attribution(self):
        spec = AssertionSpec("opened", "active_window.app_id", "equals", "editor")
        runtime = CoreOrchestrator(FakeCompositor([snapshot()]), FakeExecutor())
        runtime.register_task(contract(spec))

        _, results, state = runtime.evaluate("task-1")

        self.assertEqual(results[0].status, AssertionStatus.UNKNOWN)
        self.assertEqual(state.status, TaskStatus.NEEDS_EVIDENCE)
        attribution = runtime.attributions("task-1")[0]
        self.assertEqual(attribution.code, "INSUFFICIENT_GROUND_TRUTH")
        self.assertFalse(attribution.primary)


class ContextBuilderTests(unittest.TestCase):
    def test_strategies_apply_distinct_event_and_frame_budgets(self):
        ledger = EventLedger()
        for index in range(5):
            ledger.append("task-1", "frame.captured", "evidence", f"frame-{index}")
            ledger.append("task-1", "proposal.created", "action_proposal", f"proposal-{index}")
        builder = ContextBuilder()
        task = contract()
        compact = builder.build(
            task, TaskState("task-1"), ledger.events("task-1"), based_on_snapshot="snapshot-1"
        )
        visual = builder.build(
            task,
            TaskState("task-1"),
            ledger.events("task-1"),
            based_on_snapshot="snapshot-1",
            strategy="visual-heavy",
        )
        reset = builder.build(
            task,
            TaskState("task-1"),
            ledger.events("task-1"),
            based_on_snapshot="snapshot-1",
            strategy="planning-reset",
        )

        self.assertEqual(len(compact.recent_frame_refs), 1)
        self.assertEqual(len(visual.recent_frame_refs), 4)
        proposal_event_ids = {
            event.event_id
            for event in ledger.events("task-1")
            if event.epistemic_type == "action_proposal"
        }
        self.assertTrue(proposal_event_ids.isdisjoint(reset.ledger_event_refs))


if __name__ == "__main__":
    unittest.main()
