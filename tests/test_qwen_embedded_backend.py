import os
import unittest
from unittest.mock import patch

from PIL import Image

from mcp_autogui.qwen_backend import QwenBackendClient
from mcp_autogui.qwen_cua_backend.agent import (
    AgentPrediction,
    QwenCUAAgent,
    parse_s2_response,
)
from mcp_autogui.qwen_cua_backend.image import prepare_screenshot
from mcp_autogui.qwen_cua_backend.service import QwenCUAConfig, QwenCUAService


def _png(width=1000, height=800):
    from io import BytesIO

    buffer = BytesIO()
    Image.new("RGB", (width, height), "white").save(buffer, format="PNG")
    return buffer.getvalue()


class FakeAgent:
    def __init__(self):
        self.calls = []
        self.closed = False

    def predict(
        self,
        instruction,
        screenshot,
        history,
        *,
        accessibility_tree=None,
        previous_feedback=None,
    ):
        self.calls.append(
            {
                "instruction": instruction,
                "history": list(history),
                "feedback": previous_feedback,
                "tree": accessibility_tree,
            }
        )
        return AgentPrediction(
            assistant_output=(
                "Action: Click Settings\n<tool_call>\n"
                '{"name":"computer_use","arguments":{"action":"left_click","coordinate":[500,500]}}\n'
                "</tool_call>"
            ),
            action_text="Click Settings",
            actions=["pyautogui.click(500, 400)"],
            processed_image="encoded-image",
            original_size=(1000, 800),
            processed_size=(992, 800),
            telemetry={"backend_mode": "embedded"},
        )

    def close(self):
        self.closed = True


def _config(base_url="http://model.invalid/v1"):
    return QwenCUAConfig(
        model="qwen-test",
        base_url=base_url,
        api_key="",
        timeout=10.0,
        verify_tls=True,
        trust_env=False,
        max_tokens=256,
        max_response_chars=16384,
        top_p=0.5,
        temperature=0.1,
        max_history_turns=4,
        coordinate_type="relative",
        resize_factor=32,
    )


class EmbeddedAgentTests(unittest.TestCase):
    def test_agent_calls_openai_compatible_client_and_parses_action(self):
        response_text = (
            "Action: Move to the center\n<tool_call>\n"
            '{"name":"computer_use","arguments":{"action":"mouse_move","coordinate":[500,500]}}\n'
            "</tool_call>"
        )

        class FakeCompletions:
            def __init__(self):
                self.payload = None

            def create(self, **kwargs):
                self.payload = kwargs
                message = type("Message", (), {"content": response_text})()
                choice = type("Choice", (), {"message": message})()
                return type("Response", (), {"choices": [choice]})()

        completions = FakeCompletions()
        fake_client = type(
            "Client",
            (),
            {
                "chat": type("Chat", (), {"completions": completions})(),
                "close": lambda self: None,
            },
        )()
        agent = QwenCUAAgent(
            model="qwen-test",
            base_url="http://model.invalid/v1",
            api_key="",
        )
        agent._client = fake_client
        prediction = agent.predict("move cursor", _png(), [])

        self.assertEqual(prediction.actions, ["pyautogui.moveTo(500, 400)"])
        self.assertEqual(completions.payload["model"], "qwen-test")
        self.assertEqual(completions.payload["max_tokens"], 1024)
        self.assertEqual(completions.payload["messages"][-1]["role"], "user")

    def test_parse_s2_relative_coordinate_and_text(self):
        response = """Action: Click the center
<tool_call>
{"name":"computer_use","arguments":{"action":"left_click","coordinate":[500,250]}}
</tool_call>"""
        action_text, actions = parse_s2_response(
            response,
            original_size=(1920, 1080),
            processed_size=(1920, 1088),
            coordinate_type="relative",
        )
        self.assertEqual(action_text, "Click the center")
        self.assertEqual(actions, ["pyautogui.click(960, 270)"])

    def test_parse_s2_rejects_unknown_tool_action(self):
        response = (
            "Action: Run code\n<tool_call>"
            '{"name":"computer_use","arguments":{"action":"shell","text":"rm"}}'
            "</tool_call>"
        )
        with self.assertRaisesRegex(ValueError, "Unsupported"):
            parse_s2_response(
                response,
                original_size=(100, 100),
                processed_size=(96, 96),
                coordinate_type="relative",
            )

    def test_parse_s2_rejects_multiple_tool_calls(self):
        response = """Action: Repeated move
<tool_call>
{"name":"computer_use","arguments":{"action":"mouse_move","coordinate":[500,500]}}
</tool_call>
<tool_call>
{"name":"computer_use","arguments":{"action":"mouse_move","coordinate":[500,500]}}
</tool_call>"""
        with self.assertRaisesRegex(ValueError, "exactly one"):
            parse_s2_response(
                response,
                original_size=(1000, 800),
                processed_size=(992, 800),
                coordinate_type="relative",
            )

    def test_agent_rejects_oversized_model_response(self):
        response_text = "x" * 1025

        class FakeCompletions:
            def create(self, **kwargs):
                message = type("Message", (), {"content": response_text})()
                choice = type("Choice", (), {"message": message})()
                return type("Response", (), {"choices": [choice]})()

        fake_client = type(
            "Client",
            (),
            {
                "chat": type("Chat", (), {"completions": FakeCompletions()})(),
                "close": lambda self: None,
            },
        )()
        agent = QwenCUAAgent(
            model="qwen-test",
            base_url="http://model.invalid/v1",
            api_key="",
            max_response_chars=1024,
        )
        agent._client = fake_client
        with self.assertRaisesRegex(RuntimeError, "CUA_MAX_RESPONSE_CHARS"):
            agent.predict("move cursor", _png(), [])

    def test_prepare_screenshot_preserves_original_size(self):
        encoded, original, processed = prepare_screenshot(_png(1000, 801), factor=32)
        self.assertTrue(encoded)
        self.assertEqual(original, (1000, 801))
        self.assertEqual(processed[0] % 32, 0)
        self.assertEqual(processed[1] % 32, 0)


class EmbeddedServiceTests(unittest.TestCase):
    def test_success_is_committed_only_after_execution_feedback(self):
        agent = FakeAgent()
        service = QwenCUAService(_config(), agent=agent)
        first = service.predict("open settings", _png(), "session-1", client_step=1)
        self.assertTrue(first["proposal_pending"])
        self.assertEqual(agent.calls[0]["history"], [])
        with self.assertRaisesRegex(RuntimeError, "pending proposal"):
            service.predict("open settings", _png(), "session-1", client_step=2)

        feedback = service.record_execution(
            "session-1",
            status="success",
            execution=[{"status": "success"}],
        )
        self.assertTrue(feedback["committed"])
        service.predict("open settings", _png(), "session-1", client_step=2)
        self.assertEqual(len(agent.calls[1]["history"]), 1)
        self.assertEqual(agent.calls[1]["feedback"]["status"], "success")

    def test_rejection_is_feedback_but_not_committed_history(self):
        agent = FakeAgent()
        service = QwenCUAService(_config(), agent=agent)
        service.predict("open settings", _png(), "session-1")
        result = service.record_execution(
            "session-1",
            status="rejected",
            reason="target window moved",
        )
        self.assertFalse(result["committed"])
        service.predict("open settings", _png(), "session-1")
        self.assertEqual(agent.calls[1]["history"], [])
        self.assertEqual(agent.calls[1]["feedback"]["status"], "rejected")

    def test_dynamic_controller_prompt_does_not_reset_task_history(self):
        agent = FakeAgent()
        service = QwenCUAService(_config(), agent=agent)
        service.predict(
            "open settings\n\nController precision constraint: move to [100, 100]",
            _png(),
            "session-1",
            session_instruction="open settings",
        )
        service.record_execution("session-1", status="success", execution=[])

        service.predict(
            "open settings",
            _png(),
            "session-1",
            session_instruction="open settings",
        )

        self.assertEqual(agent.calls[0]["instruction"], "open settings\n\nController precision constraint: move to [100, 100]")
        self.assertEqual(agent.calls[1]["instruction"], "open settings")
        self.assertEqual(len(agent.calls[1]["history"]), 1)

    def test_failed_first_prediction_does_not_leave_empty_session(self):
        class FailingAgent:
            def predict(self, *args, **kwargs):
                raise ValueError("invalid model response")

            def close(self):
                return None

        service = QwenCUAService(_config(), agent=FailingAgent())
        with self.assertRaisesRegex(ValueError, "invalid model response"):
            service.predict("open settings", _png(), "unknown-to-caller")

        self.assertEqual(service.health()["sessions"], 0)

    def test_health_does_not_require_model_connection(self):
        service = QwenCUAService(_config(base_url=""), agent=FakeAgent())
        health = service.health()
        self.assertFalse(health["configured"])
        self.assertEqual(health["backend_mode"], "embedded")

    def test_close_clears_agent(self):
        agent = FakeAgent()
        service = QwenCUAService(_config(), agent=agent)
        service.close()
        self.assertTrue(agent.closed)

    def test_reset_and_sessions_are_independent(self):
        agent = FakeAgent()
        service = QwenCUAService(_config(), agent=agent)
        self.assertTrue(service.init("session-a")["ok"])
        service.predict("task a", _png(), "session-a")
        service.record_execution("session-a", status="success", execution=[])
        service.predict("task b", _png(), "session-b")
        self.assertEqual(agent.calls[1]["history"], [])

        service.reset("session-a")
        service.predict("task a", _png(), "session-a")
        self.assertEqual(agent.calls[2]["history"], [])


class BackendSelectionTests(unittest.TestCase):
    def test_json_provider_configuration_overrides_legacy_model_environment(self):
        with patch.dict(
            os.environ,
            {
                "CUA_BACKEND_MODE": "http",
                "CUA_MODEL": "legacy-model",
                "CUA_MODEL_BASE_URL": "http://legacy",
                "CUA_MODEL_TRUST_ENV": "1",
                "CUA_MAX_TOKENS": "99",
                "CUA_TEMPERATURE": "0.9",
            },
            clear=False,
        ):
            backend = QwenBackendClient(
                {
                    "kind": "qwen-cua",
                    "mode": "embedded",
                    "model": "configured-model",
                    "base_url": "http://configured/v1",
                    "timeout_seconds": 30,
                    "tls_verify": False,
                    "trust_env": False,
                    "max_tokens": 512,
                    "temperature": 0.2,
                }
            )

        self.assertEqual(backend.mode, "embedded")
        self.assertEqual(backend._delegate.config.model, "configured-model")
        self.assertEqual(backend._delegate.config.base_url, "http://configured/v1")
        self.assertEqual(backend._delegate.config.timeout, 30)
        self.assertFalse(backend._delegate.config.verify_tls)
        self.assertFalse(backend._delegate.config.trust_env)
        self.assertEqual(backend._delegate.config.max_tokens, 512)
        self.assertEqual(backend._delegate.config.temperature, 0.2)

    def test_embedded_is_default_and_does_not_require_backend_url(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("CUA_BACKEND_MODE", None)
            os.environ.pop("CUA_BACKEND_URL", None)
            os.environ.pop("CUA_MODEL_BASE_URL", None)
            backend = QwenBackendClient()
        health = backend.health()
        self.assertEqual(backend.mode, "embedded")
        self.assertEqual(health["backend_mode"], "embedded")
        self.assertFalse(health["configured"])

    def test_invalid_backend_mode_is_rejected(self):
        with patch.dict(os.environ, {"CUA_BACKEND_MODE": "invalid"}, clear=False):
            with self.assertRaisesRegex(ValueError, "embedded.*http"):
                QwenBackendClient()


if __name__ == "__main__":
    unittest.main()
