import unittest

from mcp_autogui.adapters.proposal.qwen_cua import QwenCUAProposalProvider
from mcp_autogui.core.context_builder import ContextBuilder
from mcp_autogui.core.models import AssertionSpec, TaskContract, TaskLimits, TaskState


class CompletionRequirementProjectionTests(unittest.TestCase):
    def test_required_active_window_title_is_actionable_to_qwen(self):
        contract = TaskContract(
            "TMC-020",
            "Complete the specified click, double-click, and scroll sequence.",
            assertions=(
                AssertionSpec(
                    "pass",
                    "active_window.title",
                    "contains",
                    "PASS TMC-020",
                ),
            ),
            limits=TaskLimits(max_steps=10, max_retries=1),
        )
        context = ContextBuilder().build(
            contract,
            TaskState("TMC-020", step=8),
            (),
            based_on_snapshot="snapshot-1",
        )

        requirement = context.constraints["completion_requirements"][0]
        self.assertEqual(
            requirement,
            {
                "assertion_id": "pass",
                "path": "active_window.title",
                "operator": "contains",
                "expected": "PASS TMC-020",
                "required": True,
            },
        )
        instruction = QwenCUAProposalProvider._instruction(context)
        self.assertIn("PASS TMC-020", instruction)
        self.assertIn("make the window whose visible title matches expected active", instruction)
        self.assertIn('"remaining_steps": 2', instruction)


if __name__ == "__main__":
    unittest.main()
