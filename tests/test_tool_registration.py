import asyncio
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

from PIL import Image as PILImage


fake_pyautogui = types.ModuleType("pyautogui")
fake_pyautogui.position = lambda: (100, 100)
fake_pyautogui.size = lambda: (1000, 800)
fake_pyautogui.screenshot = lambda: PILImage.new("RGB", (1000, 800), "white")
sys.modules["pyautogui"] = fake_pyautogui

from mcp_autogui.core.models import ActionType, ExecutionReceipt, ExecutionStatus, new_id, utc_now
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


class Backend:
    def predict(self, *_args, **_kwargs):
        return {"actions": ["pyautogui.moveTo(500, 400)"]}

    def reset(self, _task_id):
        return None

    def close(self):
        return None


async def direct_run_blocking(function, /, *args, **kwargs):
    return function(*args, **kwargs)


class ApplicationLauncher:
    launcher_id = "fake-launcher"

    def __init__(self):
        self.proposals = []

    def launch(self, proposal):
        self.proposals.append(proposal)
        return ExecutionReceipt(
            execution_id=new_id("execution"),
            proposal_id=proposal.proposal_id,
            status=ExecutionStatus.DELIVERED,
            executed_action=proposal.actions[0],
            started_at=utc_now(),
            finished_at=utc_now(),
        )

    def result_for(self, _proposal_id):
        return types.SimpleNamespace(returncode=0, stdout="", stderr="")


def desktop_tree(app_id="desktop"):
    windows = [{
        "id": "desktop",
        "appId": "desktop",
        "title": "Desktop",
        "visible": True,
        "active": app_id == "desktop",
        "geometry": {"x": 0, "y": 0, "width": 1000, "height": 800},
        "container": "background",
    }]
    if app_id != "desktop":
        windows.append({
            "id": app_id,
            "appId": app_id,
            "title": app_id,
            "visible": True,
            "active": True,
            "geometry": {"x": 100, "y": 100, "width": 800, "height": 600},
            "container": "workspace",
        })
    return {"layers": [{"name": "background", "windows": windows, "workspaces": []}]}


class ToolRegistrationTests(unittest.TestCase):
    def setUp(self):
        self.recording_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.recording_directory.cleanup)

    def diagnostic_recording(self):
        return {
            "audit": True,
            "diagnostic": True,
            "directory": self.recording_directory.name,
        }

    def compose(self):
        return FakeMCP()

    def test_only_unified_and_desktop_tools_are_registered(self):
        mcp = self.compose()
        with patch("mcp_autogui.adapters.providers.QwenBackendClient", return_value=Backend()):
            mcp_autogui_main(mcp, run_blocking_override=direct_run_blocking)

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
        described = asyncio.run(mcp.functions["gui_run"]("describe"))
        self.assertIsNone(described["object_ref"])
        self.assertEqual(described["object"]["diagnostic_operations"], [])

    def test_diagnostic_tool_is_registered_only_when_enabled(self):
        mcp = self.compose()
        with patch("mcp_autogui.adapters.providers.QwenBackendClient", return_value=Backend()):
            mcp_autogui_main(
                mcp,
                recording_config=self.diagnostic_recording(),
                run_blocking_override=direct_run_blocking,
            )
        self.assertIn("gui_diagnostic", mcp.tools)

        audit_only = self.compose()
        with patch("mcp_autogui.adapters.providers.QwenBackendClient", return_value=Backend()):
            mcp_autogui_main(
                audit_only,
                recording_config={
                    "audit": True,
                    "diagnostic": False,
                    "directory": self.recording_directory.name,
                },
                run_blocking_override=direct_run_blocking,
            )
        self.assertNotIn("gui_diagnostic", audit_only.tools)

    def test_desktop_shortcut_uses_the_v2_transaction(self):
        mcp = self.compose()
        calls = []
        fake_pyautogui.hotkey = lambda *keys: calls.append(keys)
        fake_pyautogui.press = lambda key: calls.append((key,))
        with patch("mcp_autogui.adapters.providers.QwenBackendClient", return_value=Backend()), patch(
            "mcp_autogui.adapters.backends.treeland_deepin.find_capability",
            return_value={
                "enabled": True,
                "auto_invokable": True,
                "normalized_hotkeys": [["win"]],
                "capability_id": "desktop.launcher.toggle",
            },
        ), patch(
            "mcp_autogui.adapters.backends.treeland_deepin.read_treeland_tree",
            return_value=desktop_tree(),
        ):
            mcp_autogui_main(mcp, run_blocking_override=direct_run_blocking)
            result = asyncio.run(
                mcp.functions["desktop_shortcut_invoke"]("desktop.launcher.toggle")
            )

        self.assertEqual(result["status"], "delivered-unverified")
        self.assertEqual(calls, [("win",)])

    def test_application_launch_returns_receipt_and_compositor_evidence(self):
        mcp = self.compose()
        launcher = ApplicationLauncher()
        with patch("mcp_autogui.adapters.providers.QwenBackendClient", return_value=Backend()), patch(
            "mcp_autogui.adapters.backends.treeland_deepin.DdeApplicationLauncher",
            return_value=launcher,
        ), patch(
            "mcp_autogui.adapters.backends.treeland_deepin.read_treeland_tree",
            return_value=desktop_tree("dde-file-manager"),
        ):
            mcp_autogui_main(mcp, run_blocking_override=direct_run_blocking)
            result = asyncio.run(
                mcp.functions["desktop_application_launch"](
                    "dde-computer", expected_active_app_id="dde-file-manager"
                )
            )

        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["returncode"], 0)
        self.assertEqual(launcher.proposals[0].actions[0].parameters["app_id"], "dde-computer")

    def test_application_launch_without_assertion_is_delivered_unverified(self):
        mcp = self.compose()
        launcher = ApplicationLauncher()
        with patch("mcp_autogui.adapters.providers.QwenBackendClient", return_value=Backend()), patch(
            "mcp_autogui.adapters.backends.treeland_deepin.DdeApplicationLauncher",
            return_value=launcher,
        ), patch(
            "mcp_autogui.adapters.backends.treeland_deepin.read_treeland_tree",
            return_value=desktop_tree(),
        ):
            mcp_autogui_main(mcp, run_blocking_override=direct_run_blocking)
            result = asyncio.run(mcp.functions["desktop_application_launch"]("dde-computer"))

        self.assertEqual(result["status"], "delivered-unverified")

    def test_omniparser_configuration_registers_no_legacy_execution_tools(self):
        mcp = self.compose()
        with patch("mcp_autogui.adapters.providers.QwenBackendClient", return_value=Backend()), patch(
            "mcp_autogui.adapters.evidence.omniparser._requests_post",
            return_value=lambda *_args, **_kwargs: None,
        ):
            mcp_autogui_main(
                mcp,
                evidence_provider_config={"omniparser": {"enabled": True, "endpoint": "parser.example:8000"}},
            )

        self.assertNotIn("omniparser_click", mcp.functions)
        self.assertEqual(len(mcp.tools), 5)

    def test_json_evidence_configuration_can_disable_compositor_provider(self):
        mcp = self.compose()
        with patch("mcp_autogui.adapters.providers.QwenBackendClient", return_value=Backend()):
            mcp_autogui_main(
                mcp,
                evidence_provider_config={"compositor_window": {"enabled": False}},
                recording_config=self.diagnostic_recording(),
                run_blocking_override=direct_run_blocking,
            )

        response = asyncio.run(mcp.functions["gui_diagnostic"]("describe"))
        self.assertEqual(response["status"], "ok")
        self.assertEqual(response["object"]["providers"]["evidence"], [])

    def test_deployment_action_restriction_is_described(self):
        mcp = self.compose()
        with patch("mcp_autogui.adapters.providers.QwenBackendClient", return_value=Backend()):
            mcp_autogui_main(
                mcp,
                denied_actions=frozenset({ActionType.KEYBOARD_TEXT}),
                recording_config=self.diagnostic_recording(),
                run_blocking_override=direct_run_blocking,
            )

        response = asyncio.run(mcp.functions["gui_diagnostic"]("describe"))
        self.assertEqual(response["status"], "ok")
        self.assertEqual(
            response["object"]["deployment"]["denied_actions"], ["keyboard.text"]
        )

    def test_json_omniparser_configuration_requires_an_explicit_endpoint(self):
        with patch("mcp_autogui.adapters.providers.QwenBackendClient", return_value=Backend()):
            with self.assertRaisesRegex(ValueError, "endpoint is required"):
                mcp_autogui_main(
                    self.compose(),
                    evidence_provider_config={"omniparser": {"enabled": True, "endpoint": ""}},
                )


if __name__ == "__main__":
    unittest.main()
