import os
import sys
import types
import asyncio
import json
import unittest
from unittest.mock import patch
from subprocess import CompletedProcess

from PIL import Image as PILImage

fake_pyautogui = types.ModuleType("pyautogui")
fake_pyautogui.position = lambda: (0, 0)
fake_pyautogui.screenshot = lambda: PILImage.new("RGB", (1000, 800), "white")
sys.modules["pyautogui"] = fake_pyautogui

from mcp_autogui.mcp_autogui_main import mcp_autogui_main
from mcp_autogui.mcp_autogui_main import _window_manager_drag_to


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


class RecordingQwenBackend:
    def __init__(self):
        self.feedback = []
        self.predict_calls = []

    def predict(self, instruction, *args, **kwargs):
        self.predict_calls.append({"instruction": instruction, "kwargs": kwargs})
        return {
            "agent_type": "cua",
            "actions": ["pyautogui.click(500, 400)"],
            "observation_text": "",
            "action_text": "Click the application icon",
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


def tree_with_active_app(app_id="desktop", title=None):
    background = {
        "name": "background",
        "layer": 0,
        "windows": [
            {
                "appId": "desktop",
                "title": "Desktop",
                "visible": True,
                "active": app_id == "desktop",
                "z": 0,
                "geometry": {"x": 0, "y": 0, "width": 1000, "height": 800},
            }
        ],
        "workspaces": [],
    }
    layers = [background]
    if app_id != "desktop":
        layers.append(
            {
                "name": "workspace",
                "layer": 1,
                "windows": [],
                "workspaces": [
                    {
                        "isActive": True,
                        "windows": [
                            {
                                "appId": app_id,
                                "title": title or app_id,
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
            }
        )
    return {"currentMode": "Normal", "layers": layers}


class ToolRegistrationTests(unittest.TestCase):
    def test_qwen_tools_are_default_and_omniparser_is_disabled(self):
        mcp = FakeMCP()
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("GUI_OMNIPARSER_ENABLED", None)
            os.environ.pop("OMNI_PARSER_SERVER", None)
            mcp_autogui_main(mcp)

        self.assertIn("qwen_cua_predict", mcp.tools)
        self.assertIn("qwen_cua_execute", mcp.tools)
        self.assertIn("desktop_capabilities_list", mcp.tools)
        self.assertIn("desktop_shortcut_invoke", mcp.tools)
        self.assertIn("desktop_applications_list", mcp.tools)
        self.assertIn("desktop_application_launch", mcp.tools)
        self.assertNotIn("omniparser_details_on_screen", mcp.tools)

    def test_dragto_moves_active_window_via_wm_without_mouse_down(self):
        async def immediate_to_thread(function, *args, **kwargs):
            return function(*args, **kwargs)

        class DragBackend:
            def __init__(self):
                self.feedback = []

            def predict(self, *args, **kwargs):
                return {
                    "agent_type": "cua",
                    "actions": ["pyautogui.dragTo(600, 400, duration=0.5)"],
                    "observation_text": "",
                    "action_text": "Move the editor window",
                    "assistant_output": "",
                    "telemetry": {},
                }

            def reset(self, session_id):
                return None

            def health(self):
                return {"ok": True}

            def record_execution(self, session_id, **kwargs):
                self.feedback.append(kwargs)
                return {"ok": True, "committed": kwargs.get("status") == "success"}

        before = tree_with_active_app("deepin-editor", "Editor")
        editor_before = before["layers"][1]["workspaces"][0]["windows"][0]
        editor_before["titlebarGeometry"] = {"x": 0, "y": 0, "width": 800, "height": 40}
        after = json.loads(json.dumps(before))
        editor_after = after["layers"][1]["workspaces"][0]["windows"][0]
        editor_after["geometry"] = {"x": 200, "y": 200, "width": 800, "height": 600}
        calls = []
        backend = DragBackend()
        mcp = FakeMCP()
        fake_pyautogui.position = lambda: (500, 110)
        fake_pyautogui.size = lambda: (1000, 800)
        fake_pyautogui.hotkey = lambda *keys: calls.append(("hotkey", keys))
        fake_pyautogui.moveTo = lambda x, y: calls.append(("moveTo", (x, y)))
        fake_pyautogui.click = lambda x, y: calls.append(("click", (x, y)))
        with patch(
            "mcp_autogui.mcp_autogui_main.QwenBackendClient", return_value=backend
        ), patch(
            "mcp_autogui.mcp_autogui_main.get_treeland_layout_tree",
            side_effect=[before, before, before, after],
        ), patch(
            "mcp_autogui.mcp_autogui_main.asyncio.to_thread",
            side_effect=immediate_to_thread,
        ), patch.dict(os.environ, {"GUI_OMNIPARSER_ENABLED": "0"}, clear=False):
            mcp_autogui_main(mcp)
            asyncio.run(mcp.functions["qwen_cua_predict"]("move editor", "drag-session"))
            result = asyncio.run(mcp.functions["qwen_cua_execute"]("drag-session"))

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["post_validation"]["window_moves"][0]["status"], "verified")
        self.assertEqual(
            calls,
            [("hotkey", ("alt", "f7")), ("moveTo", (600.0, 400.0)), ("click", (600.0, 400.0))],
        )

    def test_window_drag_refuses_a_content_or_tab_source(self):
        tree = tree_with_active_app("deepin-editor", "Editor")
        editor = tree["layers"][1]["workspaces"][0]["windows"][0]
        editor["titlebarGeometry"] = {"x": 0, "y": 0, "width": 800, "height": 40}

        class FakeInput:
            def position(self):
                return (500, 200)  # Inside the editor, below its titlebar.

            def size(self):
                return (1000, 800)

            def hotkey(self, *keys):
                self.fail("WM move must not start from window content")

            def moveTo(self, x, y):
                self.fail("WM move must not start from window content")

            def click(self, x, y):
                self.fail("WM move must not start from window content")

        with patch(
            "mcp_autogui.mcp_autogui_main.get_treeland_layout_tree", return_value=tree
        ):
            with self.assertRaisesRegex(ValueError, "drag_source_not_on_titlebar_or_resize_border"):
                _window_manager_drag_to(
                    {"coordinate": {"x": 600, "y": 400}}, FakeInput(), (1000, 800)
                )

    def test_window_drag_uses_native_drag_for_an_active_resize_border(self):
        tree = tree_with_active_app("deepin-editor", "Editor")
        editor = tree["layers"][1]["workspaces"][0]["windows"][0]
        editor["titlebarGeometry"] = {"x": 0, "y": 0, "width": 800, "height": 40}
        calls = []

        class FakeInput:
            def position(self):
                return (895, 400)  # 5 px inside the right edge at x=900.

            def size(self):
                return (1000, 800)

            def dragTo(self, *args, **kwargs):
                calls.append((args, kwargs))

        with patch(
            "mcp_autogui.mcp_autogui_main.get_treeland_layout_tree", return_value=tree
        ):
            result = _window_manager_drag_to(
                {"args": [700, 400], "kwargs": {"duration": 0.5}, "coordinate": {"x": 700, "y": 400}},
                FakeInput(),
                (1000, 800),
            )

        self.assertEqual(result["kind"], "native_window_resize")
        self.assertEqual(calls, [((700, 400), {"duration": 0.5})])

    def test_enabling_omniparser_requires_server(self):
        with patch.dict(os.environ, {"GUI_OMNIPARSER_ENABLED": "1"}, clear=False):
            os.environ.pop("OMNI_PARSER_SERVER", None)
            with self.assertRaises(RuntimeError):
                mcp_autogui_main(FakeMCP())

    def test_platform_shortcut_uses_controller_capability_not_schema_command(self):
        mcp = FakeMCP()
        pressed = []
        capability = {
            "capability_id": "desktop.launcher.toggle",
            "enabled": True,
            "auto_invokable": True,
            "normalized_hotkeys": [["winleft"]],
        }
        fake_pyautogui.press = lambda key: pressed.append(key)
        with patch(
            "mcp_autogui.mcp_autogui_main.find_capability",
            return_value=capability,
        ), patch(
            "mcp_autogui.mcp_autogui_main.get_treeland_layout_tree",
            return_value=tree_with_active_app(),
        ), patch.dict(os.environ, {"GUI_OMNIPARSER_ENABLED": "0"}, clear=False):
            mcp_autogui_main(mcp)
            result = asyncio.run(
                mcp.functions["desktop_shortcut_invoke"]("desktop.launcher.toggle")
            )

        self.assertEqual(pressed, ["winleft"])
        self.assertEqual(result["status"], "success")

    def test_application_launch_rejects_path_before_invoking_dde_am(self):
        mcp = FakeMCP()
        with patch.dict(os.environ, {"GUI_OMNIPARSER_ENABLED": "0"}, clear=False):
            mcp_autogui_main(mcp)
            with self.assertRaises(ValueError):
                asyncio.run(
                    mcp.functions["desktop_application_launch"]("/usr/bin/editor")
                )

    def test_application_launch_uses_dde_am_and_validates_active_window(self):
        async def immediate_to_thread(function, *args, **kwargs):
            return function(*args, **kwargs)

        mcp = FakeMCP()
        completed = CompletedProcess(["dde-am", "deepin-editor"], 0, "", "")
        with patch(
            "mcp_autogui.mcp_autogui_main.subprocess.run",
            return_value=completed,
        ) as run, patch(
            "mcp_autogui.mcp_autogui_main.get_treeland_layout_tree",
            return_value=tree_with_active_app("deepin-editor"),
        ), patch(
            "mcp_autogui.mcp_autogui_main.asyncio.to_thread",
            side_effect=immediate_to_thread,
        ), patch.dict(os.environ, {"GUI_OMNIPARSER_ENABLED": "0"}, clear=False):
            mcp_autogui_main(mcp)
            result = asyncio.run(
                mcp.functions["desktop_application_launch"](
                    "deepin-editor", "deepin-editor", 0
                )
            )

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["task_validation"]["status"], "passed")
        self.assertEqual(run.call_args.args[0], ["dde-am", "deepin-editor"])

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
        self.assertIsNone(execution["task_validation"])
        self.assertIsNone(execution["task_completed"])
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

    def test_predict_returns_stage_timings_in_telemetry(self):
        async def immediate_to_thread(function, *args, **kwargs):
            return function(*args, **kwargs)

        class FakeBackend:
            def predict(self, *args, **kwargs):
                return {
                    "agent_type": "cua",
                    "actions": ["pyautogui.click(500, 400)"],
                    "observation_text": "",
                    "action_text": "",
                    "assistant_output": "",
                    "telemetry": {"llm_request_ms": 12},
                }

            def reset(self, session_id):
                return None

            def health(self):
                return {"ok": True}

            def record_execution(self, session_id, **kwargs):
                return {"ok": True, "committed": True}

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
                mcp.functions["qwen_cua_predict"]("click settings", "timing-session")
            )
        payload = json.loads(result[0])
        timings = payload["telemetry"]["stage_timings_ms"]
        for stage in (
            "frame_capture_ms",
            "constraint_ms",
            "model_predict_ms",
            "parse_validate_ms",
            "fusion_ms",
            "total_ms",
        ):
            self.assertIn(stage, timings)
        self.assertEqual(payload["telemetry"]["llm_request_ms"], 12)

    def test_status_pending_sessions_reflects_execution_state(self):
        async def immediate_to_thread(function, *args, **kwargs):
            return function(*args, **kwargs)

        class FakeBackend:
            def predict(self, *args, **kwargs):
                return {
                    "agent_type": "cua",
                    "actions": ["pyautogui.click(500, 400)"],
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
                }
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
                mcp.functions["qwen_cua_predict"]("click settings", "status-session")
            )
            status_before = asyncio.run(mcp.functions["qwen_cua_status"]())
            self.assertEqual(len(status_before["pending_sessions"]), 1)
            self.assertEqual(
                status_before["pending_sessions"][0]["session_id"],
                "status-session",
            )
            fake_pyautogui.click = lambda *args, **kwargs: None
            asyncio.run(mcp.functions["qwen_cua_execute"]("status-session"))
            status_after = asyncio.run(mcp.functions["qwen_cua_status"]())

        self.assertEqual(status_after["pending_sessions"], [])
        self.assertEqual(
            [entry["session_id"] for entry in status_after["known_sessions"]],
            ["status-session"],
        )

    def test_execute_feedback_failure_requires_reset_on_next_predict(self):
        async def immediate_to_thread(function, *args, **kwargs):
            return function(*args, **kwargs)

        class FakeBackend:
            def __init__(self):
                self.reset_calls = []

            def predict(self, *args, **kwargs):
                return {
                    "agent_type": "cua",
                    "actions": ["pyautogui.click(500, 400)"],
                    "observation_text": "",
                    "action_text": "",
                    "assistant_output": "",
                    "telemetry": {},
                }

            def reset(self, session_id):
                self.reset_calls.append(session_id)
                return None

            def health(self):
                return {"ok": True}

            def record_execution(self, session_id, **kwargs):
                return {"ok": False, "committed": False, "message": "backend unavailable"}

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
            asyncio.run(
                mcp.functions["qwen_cua_predict"]("click settings", "feedback-fail-session")
            )
            fake_pyautogui.click = lambda *args, **kwargs: None
            execution = asyncio.run(
                mcp.functions["qwen_cua_execute"]("feedback-fail-session")
            )
            self.assertEqual(execution["session_continuable"], False)
            self.assertIn("reset", execution["next"])
            asyncio.run(
                mcp.functions["qwen_cua_predict"]("click settings", "feedback-fail-session")
            )

        self.assertIn("feedback-fail-session", backend.reset_calls)

    def test_execution_error_requires_reset_even_when_feedback_is_recorded(self):
        async def immediate_to_thread(function, *args, **kwargs):
            return function(*args, **kwargs)

        class FakeBackend:
            def __init__(self):
                self.reset_calls = []
                self.feedback = []

            def predict(self, *args, **kwargs):
                return {
                    "agent_type": "cua",
                    "actions": ["pyautogui.click(500, 400)"],
                    "observation_text": "",
                    "action_text": "",
                    "assistant_output": "",
                    "telemetry": {},
                }

            def reset(self, session_id):
                self.reset_calls.append(session_id)

            def health(self):
                return {"ok": True}

            def record_execution(self, session_id, **kwargs):
                self.feedback.append(kwargs)
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
        ), patch.object(
            fake_pyautogui,
            "click",
            side_effect=RuntimeError("injected click failure"),
            create=True,
        ), patch.dict(os.environ, {"GUI_OMNIPARSER_ENABLED": "0"}, clear=False):
            mcp_autogui_main(mcp)
            asyncio.run(
                mcp.functions["qwen_cua_predict"]("click settings", "execution-error-session")
            )
            execution = asyncio.run(
                mcp.functions["qwen_cua_execute"]("execution-error-session")
            )
            self.assertEqual(execution["status"], "error")
            self.assertFalse(execution["session_continuable"])
            self.assertIn("next prediction will reset", execution["next"])
            self.assertEqual(backend.feedback[0]["status"], "error")
            asyncio.run(
                mcp.functions["qwen_cua_predict"]("click settings", "execution-error-session")
            )

        self.assertIn("execution-error-session", backend.reset_calls)

    def test_post_action_missing_target_requires_reset(self):
        async def immediate_to_thread(function, *args, **kwargs):
            return function(*args, **kwargs)

        class FakeBackend:
            def __init__(self):
                self.feedback = []
                self.reset_calls = []

            def predict(self, *args, **kwargs):
                return {
                    "agent_type": "cua",
                    "actions": ["pyautogui.click(500, 400)"],
                    "observation_text": "",
                    "action_text": "",
                    "assistant_output": "",
                    "telemetry": {},
                }

            def reset(self, session_id):
                self.reset_calls.append(session_id)

            def health(self):
                return {"ok": True}

            def record_execution(self, session_id, **kwargs):
                self.feedback.append(kwargs)
                return {"ok": True, "committed": kwargs.get("status") == "success"}

        initial_tree = {
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
                                    "geometry": {"x": 100, "y": 100, "width": 800, "height": 600},
                                }
                            ],
                        }
                    ],
                },
            ]
        }
        post_tree = {"layers": [initial_tree["layers"][0]]}
        backend = FakeBackend()
        mcp = FakeMCP()
        with patch(
            "mcp_autogui.mcp_autogui_main.QwenBackendClient", return_value=backend
        ), patch(
            "mcp_autogui.mcp_autogui_main.get_treeland_layout_tree",
            side_effect=[initial_tree, initial_tree, post_tree, post_tree],
        ), patch(
            "mcp_autogui.mcp_autogui_main.pyautogui.screenshot",
            side_effect=[
                PILImage.new("RGB", (1000, 800), "white"),
                PILImage.new("RGB", (1000, 800), "black"),
                PILImage.new("RGB", (1000, 800), "black"),
            ],
        ), patch(
            "mcp_autogui.mcp_autogui_main.asyncio.to_thread",
            side_effect=immediate_to_thread,
        ), patch.object(fake_pyautogui, "click", lambda *args, **kwargs: None, create=True), patch.dict(
            os.environ, {"GUI_OMNIPARSER_ENABLED": "0"}, clear=False
        ):
            mcp_autogui_main(mcp)
            asyncio.run(mcp.functions["qwen_cua_predict"]("click settings", "post-observation"))
            execution = asyncio.run(mcp.functions["qwen_cua_execute"]("post-observation"))
            asyncio.run(mcp.functions["qwen_cua_predict"]("click settings", "post-observation"))

        self.assertEqual(execution["status"], "error")
        self.assertFalse(execution["session_continuable"])
        self.assertEqual(
            execution["post_validation"]["validation_failures"],
            ["target_identity_missing_after"],
        )
        self.assertTrue(execution["post_validation"]["screenshot_changed"])
        self.assertEqual(backend.feedback[0]["status"], "error")
        self.assertIn("post-observation", backend.reset_calls)

    def test_expected_application_waits_until_target_app_is_active(self):
        async def immediate_to_thread(function, *args, **kwargs):
            return function(*args, **kwargs)

        desktop = tree_with_active_app()
        editor = tree_with_active_app("deepin-editor", "Text Editor")
        backend = RecordingQwenBackend()
        mcp = FakeMCP()
        with patch(
            "mcp_autogui.mcp_autogui_main.QwenBackendClient", return_value=backend
        ), patch(
            "mcp_autogui.mcp_autogui_main.get_treeland_layout_tree",
            side_effect=[desktop, desktop, desktop, editor, editor],
        ), patch(
            "mcp_autogui.mcp_autogui_main.APPLICATION_WAIT_POLL_INTERVAL_S", 0
        ), patch(
            "mcp_autogui.mcp_autogui_main.asyncio.to_thread",
            side_effect=immediate_to_thread,
        ), patch.object(
            fake_pyautogui, "click", lambda *args, **kwargs: None, create=True
        ), patch.dict(os.environ, {"GUI_OMNIPARSER_ENABLED": "0"}, clear=False):
            mcp_autogui_main(mcp)
            prediction = asyncio.run(
                mcp.functions["qwen_cua_predict"](
                    "open Text Editor",
                    "open-editor",
                    expected_active_app_id="deepin-editor",
                    application_wait_timeout_s=0.1,
                )
            )
            payload = json.loads(prediction[0])
            execution = asyncio.run(mcp.functions["qwen_cua_execute"]("open-editor"))

        self.assertEqual(
            payload["task_expectation"]["expected_active_app_id"], "deepin-editor"
        )
        self.assertEqual(execution["status"], "success")
        self.assertTrue(execution["task_completed"])
        self.assertEqual(execution["task_validation"]["status"], "passed")
        self.assertEqual(execution["task_validation"]["attempts"], 2)
        self.assertIn("task is complete", execution["next"])
        self.assertEqual(backend.feedback[0]["status"], "success")

    def test_wrong_application_is_feedback_and_session_can_continue(self):
        async def immediate_to_thread(function, *args, **kwargs):
            return function(*args, **kwargs)

        desktop = tree_with_active_app()
        music = tree_with_active_app("deepin-music", "Music")
        backend = RecordingQwenBackend()
        mcp = FakeMCP()
        with patch(
            "mcp_autogui.mcp_autogui_main.QwenBackendClient", return_value=backend
        ), patch(
            "mcp_autogui.mcp_autogui_main.get_treeland_layout_tree",
            side_effect=[desktop, desktop, music, music, music],
        ), patch(
            "mcp_autogui.mcp_autogui_main.asyncio.to_thread",
            side_effect=immediate_to_thread,
        ), patch.object(
            fake_pyautogui, "click", lambda *args, **kwargs: None, create=True
        ), patch.dict(os.environ, {"GUI_OMNIPARSER_ENABLED": "0"}, clear=False):
            mcp_autogui_main(mcp)
            asyncio.run(
                mcp.functions["qwen_cua_predict"](
                    "open Text Editor",
                    "wrong-app",
                    expected_active_app_id="deepin-editor",
                    application_wait_timeout_s=0,
                )
            )
            execution = asyncio.run(mcp.functions["qwen_cua_execute"]("wrong-app"))
            next_prediction = asyncio.run(
                mcp.functions["qwen_cua_predict"]("open Text Editor", "wrong-app")
            )

        next_payload = json.loads(next_prediction[0])
        self.assertEqual(execution["status"], "partial")
        self.assertFalse(execution["task_completed"])
        self.assertTrue(execution["session_continuable"])
        self.assertEqual(
            execution["task_validation"]["reason"], "wrong_application_active"
        )
        self.assertEqual(
            execution["task_validation"]["actual_active_app_id"], "deepin-music"
        )
        self.assertEqual(backend.feedback[0]["status"], "partial")
        self.assertEqual(
            backend.feedback[0]["execution"]["post_validation"]["task_validation"],
            execution["task_validation"],
        )
        self.assertEqual(
            next_payload["task_expectation"]["expected_active_app_id"], "deepin-editor"
        )

    def test_expected_application_timeout_reports_no_observable_completion(self):
        async def immediate_to_thread(function, *args, **kwargs):
            return function(*args, **kwargs)

        desktop = tree_with_active_app()
        backend = RecordingQwenBackend()
        mcp = FakeMCP()
        with patch(
            "mcp_autogui.mcp_autogui_main.QwenBackendClient", return_value=backend
        ), patch(
            "mcp_autogui.mcp_autogui_main.get_treeland_layout_tree",
            side_effect=[desktop, desktop, desktop, desktop],
        ), patch(
            "mcp_autogui.mcp_autogui_main.asyncio.to_thread",
            side_effect=immediate_to_thread,
        ), patch.object(
            fake_pyautogui, "click", lambda *args, **kwargs: None, create=True
        ), patch.dict(os.environ, {"GUI_OMNIPARSER_ENABLED": "0"}, clear=False):
            mcp_autogui_main(mcp)
            asyncio.run(
                mcp.functions["qwen_cua_predict"](
                    "open Text Editor",
                    "app-timeout",
                    expected_active_app_id="deepin-editor",
                    application_wait_timeout_s=0,
                )
            )
            execution = asyncio.run(mcp.functions["qwen_cua_execute"]("app-timeout"))

        self.assertEqual(execution["status"], "partial")
        self.assertFalse(execution["task_completed"])
        self.assertTrue(execution["session_continuable"])
        self.assertEqual(
            execution["task_validation"]["reason"],
            "expected_application_not_observed",
        )
        self.assertEqual(backend.feedback[0]["status"], "partial")

    def test_done_cannot_bypass_expected_application_assertion(self):
        async def immediate_to_thread(function, *args, **kwargs):
            return function(*args, **kwargs)

        class DoneBackend(RecordingQwenBackend):
            def predict(self, instruction, *args, **kwargs):
                result = super().predict(instruction, *args, **kwargs)
                result["actions"] = ["DONE"]
                result["action_text"] = "DONE"
                return result

        desktop = tree_with_active_app()
        backend = DoneBackend()
        mcp = FakeMCP()
        with patch(
            "mcp_autogui.mcp_autogui_main.QwenBackendClient", return_value=backend
        ), patch(
            "mcp_autogui.mcp_autogui_main.get_treeland_layout_tree",
            side_effect=[desktop, desktop, desktop, desktop],
        ), patch(
            "mcp_autogui.mcp_autogui_main.asyncio.to_thread",
            side_effect=immediate_to_thread,
        ), patch.dict(os.environ, {"GUI_OMNIPARSER_ENABLED": "0"}, clear=False):
            mcp_autogui_main(mcp)
            asyncio.run(
                mcp.functions["qwen_cua_predict"](
                    "open Text Editor",
                    "premature-done",
                    expected_active_app_id="deepin-editor",
                    application_wait_timeout_s=0,
                )
            )
            execution = asyncio.run(
                mcp.functions["qwen_cua_execute"]("premature-done")
            )

        self.assertEqual(execution["status"], "partial")
        self.assertFalse(execution["task_completed"])
        self.assertTrue(execution["session_continuable"])
        self.assertEqual(
            execution["task_validation"]["reason"],
            "expected_application_not_observed",
        )
        self.assertEqual(backend.feedback[0]["status"], "partial")


if __name__ == "__main__":
    unittest.main()
