import unittest

from mcp_autogui.core.models import (
    AdapterCapabilities,
    AdapterDescriptor,
    CanonicalSnapshot,
    CanonicalWindowFact,
    CoordinateSpace,
    ExecutionReceipt,
    ExecutionStatus,
    FrameReference,
    OutputFact,
    Point,
    Rect,
    StackingCapabilities,
    StackingModel,
    WindowRole,
    new_id,
    utc_now,
)
from mcp_autogui.core.orchestrator import CoreOrchestrator
from mcp_autogui.core.store import ObjectStore
from mcp_autogui.facade import GuiRunFacade
from mcp_autogui.adapters.proposal.qwen_cua import QwenCUAProposalProvider


class Compositor:
    descriptor = AdapterDescriptor(
        "portable-fixture",
        AdapterCapabilities(
            True, True, True,
            StackingCapabilities(StackingModel.HIT_TEST, hit_test=True),
            active_window=True,
            window_identity="stable",
        ),
    )

    def __init__(self):
        self.number = 0

    def observe(self):
        self.number += 1
        bounds = Rect(0, 0, 1000, 800)
        return CanonicalSnapshot(
            f"snapshot-{self.number}", utc_now(), "same-environment",
            CoordinateSpace("desktop-logical", bounds, "geometry-1"),
            (OutputFact("display", bounds),), Point(5, 5),
            (CanonicalWindowFact("desktop", bounds, app_id="desktop", visible=True, active=True, role=WindowRole.DESKTOP),),
        )

    def hit_test(self, point, snapshot=None):
        return "desktop"


class Executor:
    def execute(self, proposal):
        now = utc_now()
        return ExecutionReceipt(new_id("execution"), proposal.proposal_id, ExecutionStatus.DELIVERED, proposal.action, now, now)


class ProposalProvider:
    provider_id = "fixture-proposal"

    def propose(self, context):
        from mcp_autogui.core.models import Action, ActionProposal, ActionType

        return ActionProposal(
            new_id("proposal"),
            self.provider_id,
            context.based_on_snapshot,
            Action(ActionType.POINTER_CLICK, Point(100, 100), "desktop-logical"),
        )


class PolicyProvider:
    provider_id = "fixture-policy"

    def independent_tags(self, proposal, contract):
        from mcp_autogui.core.models import EvidenceConfidence, SemanticTag

        return [SemanticTag("navigation", self.provider_id, None, EvidenceConfidence.DETERMINISTIC)]


TASK = {
    "task_id": "portable-task",
    "goal": "click a visual target",
    "permissions": {
        "actions": ["pointer.click"],
        "semantic_intents": ["navigation"],
    },
    "assertions": [],
    "limits": {"max_steps": 2, "max_retries": 0},
    "policy_profile": "desktop-safe-default",
}


class FacadeTests(unittest.TestCase):
    def setUp(self):
        self.runtime = CoreOrchestrator(Compositor(), Executor())
        self.facade = GuiRunFacade(self.runtime)

    def test_describe_exposes_capabilities_separately_from_task_permissions(self):
        response = self.facade.handle("describe", diagnostic=True)
        self.assertEqual(response["protocol_version"], 2)
        self.assertEqual(response["object"]["adapter"]["adapter_id"], "portable-fixture")
        self.assertIn("pointer.click", response["object"]["actions"])

    def test_compact_operations_return_references_and_trace_expands_them(self):
        observed = self.facade.handle("observe", task_contract=TASK)
        proposed = self.facade.handle(
            "propose",
            task_id="portable-task",
            proposal={
                "source": "controller",
                "based_on_snapshot": observed["object_ref"],
                "action": {
                    "type": "pointer.click",
                    "coordinate": {"space": "desktop-logical", "x": 100, "y": 100},
                },
                "semantic_intent": "navigation",
            },
        )
        decided = self.facade.handle(
            "decide", task_id="portable-task", proposal_id=proposed["object_ref"]
        )

        self.assertEqual(proposed["status"], "needs-execution")
        # Controller intent is still a claim without independent semantic evidence.
        self.assertEqual(decided["status"], "needs-confirmation")
        expanded = self.facade.handle(
            "trace", task_id="portable-task", object_ref=proposed["object_ref"]
        )
        self.assertEqual(expanded["object"]["action"]["type"], "pointer.click")

        pending = self.facade.handle(
            "execute", task_id="portable-task", proposal_id=proposed["object_ref"]
        )
        delivered = self.facade.handle(
            "execute", task_id="portable-task", proposal_id=proposed["object_ref"], confirmed=True
        )
        repeated = self.facade.handle(
            "execute", task_id="portable-task", proposal_id=proposed["object_ref"], confirmed=True
        )
        self.assertEqual(pending["status"], "needs-confirmation")
        self.assertEqual(delivered["status"], "needs-evidence")
        self.assertEqual(repeated["object_ref"], delivered["object_ref"])

    def test_run_exposes_the_bounded_automatic_transaction_loop(self):
        runtime = CoreOrchestrator(
            Compositor(),
            Executor(),
            proposal_provider=ProposalProvider(),
            policy_providers=(PolicyProvider(),),
        )
        facade = GuiRunFacade(runtime)
        response = facade.handle("run", task_contract=TASK, max_iterations=1, diagnostic=True)

        self.assertEqual(response["status"], "partial")
        self.assertEqual(len(response["object"]["iterations"]), 1)
        self.assertEqual(response["retry"]["required_action"], "continue-run")


class Backend:
    def __init__(self, actions):
        self.actions = actions

    def predict(self, *args, **kwargs):
        return {"actions": self.actions, "observation_text": "claim"}


class QwenProposalAdapterTests(unittest.TestCase):
    def context_and_store(self):
        from mcp_autogui.core.models import ModelContext

        store = ObjectStore()
        bounds = Rect(-100, 0, 2000, 1000)
        snap = CanonicalSnapshot(
            "snapshot-q", utc_now(), "env", CoordinateSpace("desktop-logical", bounds),
            (OutputFact("display", bounds),), None, (),
        )
        store.put(snap, object_ref=snap.snapshot_id)
        store.put(b"png", object_ref="image-q")
        frame = FrameReference("frame-q", utc_now(), "image-q", (1000, 500))
        context = ModelContext(
            "context-q", "task-q", "snapshot-q", frame, "click", 0, (), (), None, (),
            {"single_action_only": True}, (),
        )
        return context, store

    def test_qwen_coordinate_is_mapped_to_desktop_logical_space(self):
        context, store = self.context_and_store()
        provider = QwenCUAProposalProvider(Backend(["pyautogui.click(500, 250)"]), store)
        proposal = provider.propose(context)
        self.assertEqual(proposal.action.coordinate, Point(900, 500))
        self.assertEqual(proposal.action.coordinate_space, "desktop-logical")

    def test_qwen_multiple_actions_are_rejected(self):
        context, store = self.context_and_store()
        provider = QwenCUAProposalProvider(
            Backend(["pyautogui.click(1, 1)", "pyautogui.click(2, 2)"]), store
        )
        with self.assertRaisesRegex(ValueError, "exactly one action"):
            provider.propose(context)


if __name__ == "__main__":
    unittest.main()
