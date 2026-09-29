import unittest

from mcp_autogui.adapters.proposal.qwen_cua import QwenCUAProposalProvider
from mcp_autogui.core.context_builder import ContextBuilder
from mcp_autogui.core.models import AssertionSpec, TaskContract, TaskLimits, TaskState


class CompletionRequirementProjectionTests(unittest.TestCase):
    def test_only_the_current_plan_step_is_projected_as_the_qwen_goal(self):
        contract = TaskContract(
            "TMC-101",
            "Open an editor, enter text, and save it.",
            assertions=(
                AssertionSpec("saved", "active_window.title", "contains", "TMC-101.md"),
            ),
            steps=("Open the editor.", "Enter the text.", "Save the document."),
            step_assertions=(
                (AssertionSpec("editor-open", "active_window.app_id", "equals", "deepin-editor"),),
                (), (),
            ),
        )
        context = ContextBuilder().build(
            contract,
            TaskState("TMC-101", plan_step=1),
            (),
            based_on_snapshot="snapshot-1",
        )

        self.assertEqual(context.goal, "Enter the text.")
        self.assertEqual(context.pending_assertions, ())
        self.assertEqual(context.constraints["completion_requirements"], ())
        self.assertEqual(context.constraints["plan"]["current_index"], 1)
        self.assertEqual(context.constraints["plan"]["step_completion_requirements"], ())
        instruction = QwenCUAProposalProvider._instruction(context)
        self.assertIn("work only on its current_step", instruction)

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
