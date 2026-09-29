import unittest

from mcp_autogui.adapters.executor.pyautogui import PyAutoGUIExecutor
from mcp_autogui.core.models import Action, ActionProposal, ActionType, Point, new_id
from unittest.mock import patch


class RecordingPyAutoGUI:
    def __init__(self):
        self.calls = []

    def dragTo(self, *args, **kwargs):
        self.calls.append((args, kwargs))


class PyAutoGUIExecutorTests(unittest.TestCase):
    def test_wait_uses_bounded_controller_sleep(self):
        action = Action(ActionType.WAIT, parameters={"seconds": 0.5})
        proposal = ActionProposal(new_id("proposal"), "test", "snapshot-1", (action,))

        with patch("mcp_autogui.adapters.executor.pyautogui.time.sleep") as sleep:
            receipt = PyAutoGUIExecutor(RecordingPyAutoGUI()).execute(proposal)

        self.assertEqual(receipt.status.value, "delivered")
        sleep.assert_called_once_with(0.5)

    def test_drag_uses_calibrated_default_duration(self):
        module = RecordingPyAutoGUI()
        action = Action(ActionType.POINTER_DRAG, Point(20, 30), "desktop-logical")
        proposal = ActionProposal(new_id("proposal"), "test", "snapshot-1", (action,))

        receipt = PyAutoGUIExecutor(module).execute(proposal)

        self.assertEqual(receipt.status.value, "delivered")
        self.assertEqual(module.calls, [((20, 30), {"duration": 1.2, "button": "left"})])

    def test_drag_preserves_an_explicit_duration(self):
        module = RecordingPyAutoGUI()
        action = Action(
            ActionType.POINTER_DRAG, Point(20, 30), "desktop-logical", {"duration": 2}
        )
        proposal = ActionProposal(new_id("proposal"), "test", "snapshot-1", (action,))

        PyAutoGUIExecutor(module).execute(proposal)

        self.assertEqual(module.calls[0][1]["duration"], 2)
