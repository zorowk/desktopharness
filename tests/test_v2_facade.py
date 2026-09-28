import unittest

from mcp_autogui.core.models import Action, ActionProposal, ActionType, Point, TaskStatus
from mcp_autogui.core.orchestrator import CoreOrchestrator
from mcp_autogui.facade import AutoUIFacade, parse_action_proposal, parse_task_contract
from mcp_autogui.runtime_description import RuntimeDescription
from tests.test_v2_core import FakeCompositor, FakeExecutor, snapshot


class ClickProvider:
    provider_id = "fixture"

    def __init__(self):
        self.feedback = []

    def propose(self, context):
        return ActionProposal(
            "proposal-1", self.provider_id, context.based_on_snapshot,
            (Action(ActionType.POINTER_CLICK, Point(100, 100), "desktop-logical"),),
            claimed_intent="content_edit",
        )

    def record_execution(self, task_id, receipt):
        self.feedback.append((task_id, receipt.status.value))

    def reset(self, _task_id):
        return None


def facade(*, denied=frozenset(), snapshots=None, diagnostic=True):
    compositor = FakeCompositor(snapshots or [snapshot(), snapshot("s2"), snapshot("s3")])
    executor = FakeExecutor()
    provider = ClickProvider()
    runtime = CoreOrchestrator(
        compositor, executor, proposal_provider=provider, denied_actions=denied,
        diagnostic_enabled=diagnostic,
    )
    description = RuntimeDescription.from_components(
        compositor=compositor,
        executor=executor,
        proposal_provider=provider,
        frame_provider=None,
        evidence_providers=(),
        denied_actions=denied,
        context_strategies={"compact", "recovery"},
    )
    return AutoUIFacade(runtime, description), runtime, executor, provider


MINIMAL_TASK = {
    "task_id": "task-1",
    "goal": "click the desktop",
    "assertions": [],
    "limits": {"max_steps": 2, "max_retries": 1},
}


class FacadeTests(unittest.TestCase):
    def test_public_responses_have_no_trace_refs_when_diagnostic_is_disabled(self):
        api, _, _, _ = facade(diagnostic=False)
        described = api.handle("describe")
        status = api.handle("status", task_contract=MINIMAL_TASK)

        self.assertIsNone(described["object_ref"])
        self.assertIn("object", described)
        self.assertEqual(described["object"]["diagnostic_operations"], [])
        self.assertIsNone(status["object_ref"])

    def test_description_exposes_current_operations_and_deployment_limits(self):
        api, runtime, _, _ = facade(denied=frozenset({ActionType.KEYBOARD_TEXT}))
        response = api.handle("describe")
        description = runtime.store.require(response["object_ref"])
        self.assertEqual(description["operations"], ["describe", "reset", "run", "status"])
        self.assertEqual(
            description["diagnostic_operations"],
            ["describe", "evaluate", "execute", "observe", "prepare", "propose", "trace"],
        )
        self.assertEqual(description["deployment"]["denied_actions"], ["keyboard.text"])
        self.assertEqual(
            description["transport"],
            {"mode": "unconfigured", "auth": {"mode": "unconfigured"}},
        )
        self.assertNotIn("policy_profiles", description)

    def test_description_exposes_only_public_transport_configuration(self):
        compositor = FakeCompositor([snapshot()])
        description = RuntimeDescription.from_components(
            compositor=compositor,
            executor=FakeExecutor(),
            proposal_provider=None,
            frame_provider=None,
            evidence_providers=(),
            denied_actions=(),
            context_strategies={"compact"},
            transport={
                "mode": "streamable-http",
                "host": "127.0.0.1",
                "port": 8651,
                "auth": {"mode": "bearer-token", "token": "must-not-leak"},
            },
        ).to_dict()
        self.assertEqual(
            description["transport"],
            {
                "mode": "streamable-http",
                "host": "127.0.0.1",
                "port": 8651,
                "auth": {"mode": "bearer-token"},
            },
        )
        self.assertNotIn("must-not-leak", str(description))

    def test_removed_confirm_operation_is_rejected(self):
        api, _, _, _ = facade()
        response = api.handle("confirm", task_id="task-1", proposal_id="p")
        self.assertEqual(response["status"], "failed")
        self.assertEqual(response["error"]["code"], "UNSUPPORTED_OPERATION")

    def test_old_task_policy_fields_are_rejected(self):
        for field, value in (
            ("permissions", {"actions": ["pointer.click"]}),
            ("policy_profile", "desktop-safe-default"),
            ("policy_overrides", {"unknown": "allow"}),
        ):
            with self.subTest(field=field):
                payload = dict(MINIMAL_TASK)
                payload[field] = value
                with self.assertRaisesRegex(ValueError, "unknown fields"):
                    parse_task_contract(payload)

    def test_content_edit_claim_executes_without_confirmation(self):
        api, runtime, executor, provider = facade()
        response = api.handle("run", task_contract=MINIMAL_TASK, max_iterations=1)
        self.assertEqual(response["task_state"], TaskStatus.RUNNING.value)
        self.assertEqual(len(executor.actions), 1)
        self.assertEqual(provider.feedback, [("task-1", "delivered")])
        self.assertNotEqual(runtime.status("task-1").status.value, "needs-confirmation")

    def test_diagnostic_prepare_and_execute_share_validator(self):
        api, runtime, executor, _ = facade()
        observed = api.handle_diagnostic("observe", task_contract=MINIMAL_TASK)
        proposal = api.handle_diagnostic(
            "propose",
            task_id="task-1",
            proposal={
                "proposal_id": "manual",
                "based_on_snapshot": observed["object_ref"],
                "actions": [{
                    "type": "pointer.click",
                    "coordinate": {"x": 100, "y": 100, "space": "desktop-logical"},
                }],
            },
        )
        prepared = api.handle_diagnostic(
            "prepare", task_id="task-1", proposal_id=proposal["object_ref"]
        )
        self.assertEqual(prepared["status"], TaskStatus.RUNNING.value)
        executed = api.handle_diagnostic(
            "execute", task_id="task-1", proposal_id=proposal["object_ref"]
        )
        self.assertEqual(executed["status"], TaskStatus.RUNNING.value)
        self.assertEqual(len(executor.actions), 1)
        self.assertEqual(runtime.status("task-1").step, 1)

    def test_diagnostic_restriction_returns_failure_and_zero_injection(self):
        api, _, executor, _ = facade(denied=frozenset({ActionType.POINTER_CLICK}))
        observed = api.handle_diagnostic("observe", task_contract=MINIMAL_TASK)
        proposed = api.handle_diagnostic(
            "propose", task_id="task-1",
            proposal={
                "proposal_id": "blocked",
                "based_on_snapshot": observed["object_ref"],
                "actions": [{
                    "type": "pointer.click",
                    "coordinate": {"x": 100, "y": 100, "space": "desktop-logical"},
                }],
            },
        )
        response = api.handle_diagnostic(
            "execute", task_id="task-1", proposal_id=proposed["object_ref"]
        )
        self.assertEqual(response["error"]["code"], "ACTION_RESTRICTED")
        self.assertEqual(executor.actions, [])

    def test_status_and_reset_use_the_single_task_state(self):
        api, runtime, _, _ = facade()
        api.handle_diagnostic("observe", task_contract=MINIMAL_TASK)
        status = api.handle("status", task_id="task-1")
        self.assertEqual(status["task_state"], TaskStatus.RUNNING.value)
        reset = api.handle("reset", task_id="task-1")
        self.assertEqual(reset["status"], "completed")
        self.assertFalse(runtime.has_task("task-1"))


class ProposalParsingTests(unittest.TestCase):
    def test_actions_are_required_and_legacy_action_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "proposal.action is not supported"):
            parse_action_proposal({"action": {"type": "done"}}, "snapshot-1")
        with self.assertRaisesRegex(ValueError, "non-empty list"):
            parse_action_proposal({"actions": []}, "snapshot-1")

    def test_parser_preserves_an_ordered_sequence(self):
        proposal = parse_action_proposal(
            {"actions": [
                {"type": "keyboard.text", "parameters": {"text": "hello"}},
                {"type": "done"},
            ]},
            "snapshot-1",
        )
        self.assertEqual(
            [action.type for action in proposal.actions],
            [ActionType.KEYBOARD_TEXT, ActionType.DONE],
        )


if __name__ == "__main__":
    unittest.main()
