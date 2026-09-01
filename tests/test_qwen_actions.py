import unittest

from mcp_autogui.qwen_actions import (
    execute_parsed_actions,
    parse_qwen_actions,
    set_absolute_coordinate,
)


class FakePyAutoGUI:
    def __init__(self):
        self.calls = []

    def click(self, *args, **kwargs):
        self.calls.append(("click", args, kwargs))

    def write(self, *args, **kwargs):
        self.calls.append(("write", args, kwargs))


class FailSafePyAutoGUI(FakePyAutoGUI):
    FAILSAFE = True

    def position(self):
        return (1919, 1079)

    def size(self):
        return (1920, 1080)

    def hotkey(self, *args, **kwargs):
        self.calls.append(("hotkey", args, kwargs))


class QwenActionTests(unittest.TestCase):
    def test_parse_coordinate_and_terminal_actions(self):
        actions = parse_qwen_actions(
            ["pyautogui.click(100, 200)", "pyautogui.write('hello')", "DONE"]
        )

        self.assertEqual(actions[0]["coordinate"], {"x": 100.0, "y": 200.0})
        self.assertIsNone(actions[1]["coordinate"])
        self.assertEqual(actions[2]["type"], "done")

    def test_parse_expands_safe_multistatement_action(self):
        actions = parse_qwen_actions(
            ["pyautogui.write('line one')\npyautogui.press('enter')"]
        )

        self.assertEqual([action["type"] for action in actions], ["write", "press"])

    def test_parse_rejects_arbitrary_python(self):
        with self.assertRaises(ValueError):
            parse_qwen_actions(["import os; os.system('false')"])

        with self.assertRaises(ValueError):
            parse_qwen_actions(["pyautogui.click(get_x(), 20)"])

    def test_coordinate_can_be_reprojected_before_execution(self):
        actions = parse_qwen_actions(["pyautogui.click(100, 200)"])
        set_absolute_coordinate(actions[0], {"x": 300, "y": 400})
        fake = FakePyAutoGUI()

        result = execute_parsed_actions(actions, fake)

        self.assertEqual(result[0]["status"], "success")
        self.assertEqual(fake.calls, [("click", (300.0, 400.0), {})])

    def test_failsafe_corner_refuses_pyautogui_action_without_disabling_failsafe(self):
        actions = parse_qwen_actions(["pyautogui.hotkey('super', 'd')"])
        fake = FailSafePyAutoGUI()

        result = execute_parsed_actions(actions, fake)

        self.assertEqual(result[0]["status"], "error")
        self.assertEqual(result[0]["error_code"], "cursor_in_failsafe_corner")
        self.assertEqual(result[0]["cursor"], {"x": 1919.0, "y": 1079.0})
        self.assertEqual(fake.calls, [])


if __name__ == "__main__":
    unittest.main()
