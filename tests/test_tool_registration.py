import asyncio
import os
import sys
import types
import unittest
from unittest.mock import patch

from PIL import Image as PILImage


fake_pyautogui = types.ModuleType("pyautogui")
fake_pyautogui.position = lambda: (100, 100)
fake_pyautogui.size = lambda: (1000, 800)
fake_pyautogui.screenshot = lambda: PILImage.new("RGB", (1000, 800), "white")
sys.modules["pyautogui"] = fake_pyautogui

from mcp_autogui.mcp_autogui_main import mcp_autogui_main, register_omniparser_tools


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


class Backend:
    def predict(self, *_args, **_kwargs):
        return {"actions": ["pyautogui.moveTo(500, 400)"]}

    def reset(self, _task_id):
        return None

    def close(self):
        return None


def desktop_tree(app_id="desktop"):
    windows = [{
        "windowId": "desktop",
        "appId": "desktop",
        "title": "Desktop",
        "visible": True,
        "active": app_id == "desktop",
        "geometry": {"x": 0, "y": 0, "width": 1000, "height": 800},
        "container": "background",
    }]
    if app_id != "desktop":
        windows.append({
            "windowId": app_id,
            "appId": app_id,
            "title": app_id,
            "visible": True,
            "active": True,
            "geometry": {"x": 100, "y": 100, "width": 800, "height": 600},
            "container": "workspace",
        })
    return {"layers": [{"name": "background", "windows": windows, "workspaces": []}]}


class ToolRegistrationTests(unittest.TestCase):
    def compose(self):
        return FakeMCP()

    def test_only_unified_and_desktop_tools_are_registered(self):
        mcp = self.compose()
        with patch("mcp_autogui.mcp_autogui_main.QwenBackendClient", return_value=Backend()), patch.dict(
            os.environ, {"GUI_OMNIPARSER_ENABLED": "0"}, clear=False
        ):
            mcp_autogui_main(mcp)

        self.assertEqual(
            set(mcp.tools),
            {
                "gui_run",
                "desktop_capabilities_list",
                "desktop_shortcut_invoke",
                "desktop_applications_list",
                "desktop_application_launch",
            },
        )
        self.assertNotIn("qwen_cua_predict", mcp.functions)

    def test_desktop_shortcut_uses_the_v2_transaction(self):
        mcp = self.compose()
        calls = []
        fake_pyautogui.hotkey = lambda *keys: calls.append(keys)
        fake_pyautogui.press = lambda key: calls.append((key,))
        with patch("mcp_autogui.mcp_autogui_main.QwenBackendClient", return_value=Backend()), patch(
            "mcp_autogui.mcp_autogui_main.find_capability",
            return_value={
                "enabled": True,
                "auto_invokable": True,
                "normalized_hotkeys": [["win"]],
                "capability_id": "desktop.launcher.toggle",
            },
        ), patch(
            "mcp_autogui.mcp_autogui_main.get_treeland_layout_tree",
            return_value=desktop_tree(),
        ), patch.dict(os.environ, {"GUI_OMNIPARSER_ENABLED": "0"}, clear=False):
            mcp_autogui_main(mcp)
            result = asyncio.run(
                mcp.functions["desktop_shortcut_invoke"]("desktop.launcher.toggle")
            )

        self.assertEqual(result["status"], "success")
        self.assertEqual(calls, [("win",)])

    def test_omniparser_enablement_registers_no_legacy_execution_tools(self):
        mcp = self.compose()
        with patch("mcp_autogui.mcp_autogui_main.QwenBackendClient", return_value=Backend()), patch.dict(
            os.environ,
            {"GUI_OMNIPARSER_ENABLED": "1", "OMNI_PARSER_SERVER": "parser.example:8000"},
            clear=False,
        ):
            mcp_autogui_main(mcp)

        self.assertNotIn("omniparser_click", mcp.functions)
        self.assertEqual(len(mcp.tools), 5)

    def test_legacy_omniparser_registration_is_hard_disabled(self):
        with self.assertRaisesRegex(RuntimeError, "direct execution tools are removed"):
            register_omniparser_tools(self.compose())


if __name__ == "__main__":
    unittest.main()
