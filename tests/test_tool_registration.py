import os
import sys
import types
import asyncio
import json
import unittest
from unittest.mock import patch

from PIL import Image as PILImage

fake_pyautogui = types.ModuleType("pyautogui")
fake_pyautogui.position = lambda: (0, 0)
fake_pyautogui.screenshot = lambda: PILImage.new("RGB", (1000, 800), "white")
sys.modules["pyautogui"] = fake_pyautogui

from mcp_autogui.mcp_autogui_main import mcp_autogui_main


class FakeMCP:
    def __init__(self):
        self.tools = []
        self.functions = {}

    def tool(self):
        def register(function):
            self.tools.append(function.__name__)
            self.functions[function.__name__] = function
            return function

        return register


class ToolRegistrationTests(unittest.TestCase):
    def test_qwen_tools_are_default_and_omniparser_is_disabled(self):
        mcp = FakeMCP()
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("GUI_OMNIPARSER_ENABLED", None)
            os.environ.pop("OMNI_PARSER_SERVER", None)
            mcp_autogui_main(mcp)

        self.assertIn("qwen_cua_predict", mcp.tools)
        self.assertIn("qwen_cua_execute", mcp.tools)
        self.assertNotIn("omniparser_details_on_screen", mcp.tools)

    def test_enabling_omniparser_requires_server(self):
        with patch.dict(os.environ, {"GUI_OMNIPARSER_ENABLED": "1"}, clear=False):
            os.environ.pop("OMNI_PARSER_SERVER", None)
            with self.assertRaises(RuntimeError):
                mcp_autogui_main(FakeMCP())

    def test_prediction_returns_qwen_action_with_treeland_target(self):
        async def immediate_to_thread(function, *args, **kwargs):
            return function(*args, **kwargs)

        class FakeBackend:
            def __init__(self):
                self.feedback = []

            def predict(self, *args, **kwargs):
                return {
                    "agent_type": "cua",
                    "actions": ["pyautogui.click(500, 400)"],
                    "observation_text": "Settings is visible",
                    "action_text": "Click settings",
                    "assistant_output": "",
                    "telemetry": {},
                }

            def reset(self, session_id):
                return None

            def health(self):
                return {"ok": True}

            def record_execution(self, session_id, **kwargs):
                self.feedback.append({"session_id": session_id, **kwargs})
                return {"ok": True, "committed": kwargs.get("status") == "success"}

        tree = {
            "currentMode": "Normal",
            "layers": [
                {
                    "name": "background",
                    "layer": 0,
                    "windows": [
                        {
                            "appId": "desktop",
                            "title": "Desktop",
                            "visible": True,
                            "z": 0,
                            "geometry": {"x": 0, "y": 0, "width": 1000, "height": 800},
                        }
                    ],
                    "workspaces": [],
                },
                {
                    "name": "workspace",
                    "layer": 1,
                    "windows": [],
                    "workspaces": [
                        {
                            "isActive": True,
                            "windows": [
                                {
                                    "appId": "settings",
                                    "title": "Settings",
                                    "visible": True,
                                    "active": True,
                                    "z": 1,
                                    "geometry": {
                                        "x": 100,
                                        "y": 100,
                                        "width": 800,
                                        "height": 600,
                                    },
                                }
                            ],
                        }
                    ],
                },
            ],
        }
        mcp = FakeMCP()
        backend = FakeBackend()
        with patch(
            "mcp_autogui.mcp_autogui_main.QwenBackendClient",
            return_value=backend,
        ), patch(
            "mcp_autogui.mcp_autogui_main.get_treeland_layout_tree",
            return_value=tree,
        ), patch(
            "mcp_autogui.mcp_autogui_main.asyncio.to_thread",
            side_effect=immediate_to_thread,
        ), patch.dict(os.environ, {"GUI_OMNIPARSER_ENABLED": "0"}, clear=False):
            mcp_autogui_main(mcp)
            result = asyncio.run(
                mcp.functions["qwen_cua_predict"]("click settings", "test-session")
            )
            payload = json.loads(result[0])
            target = payload["fused_actions"]["actions"][0]["target_window"]
            self.assertEqual(target["appId"], "settings")
            fake_pyautogui.click = lambda *args, **kwargs: None
            execution = asyncio.run(mcp.functions["qwen_cua_execute"]("test-session"))

        self.assertEqual(execution["status"], "success")
        actual = backend.feedback[0]["execution"]
        self.assertEqual(actual["frame_id"], payload["frame_id"])
        self.assertEqual(
            actual["actual_actions"][0]["coordinate"],
            {"x": 500.0, "y": 400.0},
        )

    def test_qwen_coordinate_constraint_uses_qwen_and_checks_its_result(self):
        async def immediate_to_thread(function, *args, **kwargs):
            return function(*args, **kwargs)

        class FakeBackend:
            def __init__(self):
                self.instructions = []
                self.predict_kwargs = []

            def predict(self, instruction, *args, **kwargs):
                self.instructions.append(instruction)
                self.predict_kwargs.append(kwargs)
                return {
                    "agent_type": "cua",
                    "actions": ["pyautogui.moveTo(100, 100)"],
                    "observation_text": "",
                    "action_text": "Move cursor",
                    "assistant_output": "",
                    "telemetry": {},
                }

            def reset(self, session_id):
                return None

            def health(self):
                return {"ok": True}

            def record_execution(self, session_id, **kwargs):
                return {"ok": True, "committed": False}

        tree = {
            "currentMode": "Normal",
            "layers": [
                {
                    "name": "background",
                    "layer": 0,
                    "windows": [
                        {
                            "appId": "desktop",
                            "title": "Desktop",
                            "visible": True,
                            "z": 0,
                            "geometry": {"x": 0, "y": 0, "width": 1000, "height": 800},
                        }
                    ],
                    "workspaces": [],
                }
            ],
        }
        backend = FakeBackend()
        mcp = FakeMCP()
        with patch(
            "mcp_autogui.mcp_autogui_main.QwenBackendClient",
            return_value=backend,
        ), patch(
            "mcp_autogui.mcp_autogui_main.get_treeland_layout_tree",
            return_value=tree,
        ), patch(
            "mcp_autogui.mcp_autogui_main.asyncio.to_thread",
            side_effect=immediate_to_thread,
        ), patch.dict(os.environ, {"GUI_OMNIPARSER_ENABLED": "0"}, clear=False):
            mcp_autogui_main(mcp)
            result = asyncio.run(
                mcp.functions["qwen_cua_predict"](
                    "move to the calibration point",
                    "constraint-session",
                    False,
                    "mouse_move",
                    [100, 100],
                    2.0,
                )
            )
        payload = json.loads(result[0])
        self.assertEqual(
            payload["coordinate_constraint"]["qwen_normalized_coordinate"],
            {"x": 100, "y": 125},
        )
        self.assertIn("Return coordinate [100, 125] exactly", backend.instructions[0])
        self.assertEqual(backend.predict_kwargs[0]["session_instruction"], "move to the calibration point")

    def test_invalid_post_prediction_action_is_rejected_and_released(self):
        async def immediate_to_thread(function, *args, **kwargs):
            return function(*args, **kwargs)

        class FakeBackend:
            def __init__(self):
                self.feedback = []

            def predict(self, *args, **kwargs):
                return {
                    "agent_type": "cua",
                    "actions": ["pyautogui.not_allowed(1)"],
                    "observation_text": "",
                    "action_text": "",
                    "assistant_output": "",
                    "telemetry": {},
                }

            def reset(self, session_id):
                return None

            def health(self):
                return {"ok": True}

            def record_execution(self, session_id, **kwargs):
                self.feedback.append({"session_id": session_id, **kwargs})
                return {"ok": True, "committed": False}

        mcp = FakeMCP()
        backend = FakeBackend()
        with patch(
            "mcp_autogui.mcp_autogui_main.QwenBackendClient",
            return_value=backend,
        ), patch(
            "mcp_autogui.mcp_autogui_main.get_treeland_layout_tree",
            return_value={"layers": []},
        ), patch(
            "mcp_autogui.mcp_autogui_main.asyncio.to_thread",
            side_effect=immediate_to_thread,
        ), patch.dict(os.environ, {"GUI_OMNIPARSER_ENABLED": "0"}, clear=False):
            mcp_autogui_main(mcp)
            with self.assertRaisesRegex(ValueError, "controller validation"):
                asyncio.run(
                    mcp.functions["qwen_cua_predict"](
                        "invalid action test", "explicit-session"
                    )
                )

        self.assertEqual(backend.feedback[0]["status"], "rejected")
        self.assertIn("pyautogui.not_allowed is not allowed", backend.feedback[0]["reason"])


if __name__ == "__main__":
    unittest.main()
