import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from mcp_autogui.desktop_backend import (
    DesktopBackend,
    available_desktop_backends,
    create_desktop_backend,
    register_desktop_backend,
)
from mcp_autogui.server_config import load_server_config


def config_payload(*, backend="treeland-deepin"):
    return {
        "schema_version": 2,
        "transport": {
            "mode": "streamable-http", "host": "127.0.0.1", "port": 8651,
            "auth": {"mode": "loopback"},
        },
        "desktop_backend": {"kind": backend},
        "proposal_provider": {
            "kind": "qwen-cua",
            "model": "qwen3_rl",
            "base_url": "http://127.0.0.1:8000/v1",
            "timeout_seconds": 120,
            "tls_verify": True,
        },
        "deployment": {"denied_actions": []},
        "evidence_providers": {"omniparser": {"enabled": False, "endpoint": ""}},
        "recording": {
            "audit": True,
            "diagnostic": False,
            "directory": "/tmp/autoui-audit",
            "retention_days": 3,
            "max_gib": 16,
        },
    }


class ServerConfigTests(unittest.TestCase):
    def write_config(self, payload):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = Path(directory.name) / "mcp-autoui.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def test_json_config_selects_the_registered_backend_and_runtime_settings(self):
        config = load_server_config(self.write_config(config_payload()))

        self.assertEqual(config.desktop_backend, "treeland-deepin")
        self.assertIn(config.desktop_backend, available_desktop_backends())
        self.assertEqual(config.transport_mode, "streamable-http")
        self.assertEqual(config.transport_port, 8651)
        self.assertEqual(config.transport_auth_mode, "loopback")
        self.assertEqual(config.proposal_provider["model"], "qwen3_rl")
        self.assertEqual(config.deployment_denied_actions, frozenset())
        self.assertFalse(config.evidence_providers["omniparser"]["enabled"])
        self.assertEqual(config.recording["retention_days"], 3)
        self.assertTrue(config.recording["audit"])
        self.assertFalse(config.recording["diagnostic"])

    def test_unknown_backend_is_rejected_before_server_start(self):
        with self.assertRaisesRegex(ValueError, "desktop_backend.kind"):
            load_server_config(self.write_config(config_payload(backend="other-desktop")))

    def test_removed_config_versions_and_qwen_modes_are_rejected(self):
        payload = config_payload()
        payload["schema_version"] = 1
        with self.assertRaisesRegex(ValueError, "schema_version must be 2"):
            load_server_config(self.write_config(payload))

        payload = config_payload()
        payload["proposal_provider"]["mode"] = "embedded"
        with self.assertRaisesRegex(ValueError, "unknown fields: mode"):
            load_server_config(self.write_config(payload))

    def test_unknown_or_invalid_nested_configuration_is_rejected(self):
        payload = config_payload()
        payload["evidence_providers"]["compositor_window"] = {"enabled": "yes"}
        with self.assertRaisesRegex(ValueError, "enabled must be true or false"):
            load_server_config(self.write_config(payload))

        payload = config_payload()
        payload["recording"]["unexpected"] = True
        with self.assertRaisesRegex(ValueError, "recording has unknown fields"):
            load_server_config(self.write_config(payload))

        payload = config_payload()
        payload["proposal_provider"]["timeout_seconds"] = "120"
        with self.assertRaisesRegex(ValueError, "timeout_seconds must be a positive integer"):
            load_server_config(self.write_config(payload))

        payload = config_payload()
        payload["evidence_providers"]["atspi"] = {"enabled": "yes"}
        with self.assertRaisesRegex(ValueError, "enabled must be true or false"):
            load_server_config(self.write_config(payload))

        payload = config_payload()
        payload["proposal_provider"]["temperature"] = 1.5
        with self.assertRaisesRegex(ValueError, "temperature must be a number from 0 to 1"):
            load_server_config(self.write_config(payload))

        payload = config_payload()
        payload["deployment"] = {"denied_actions": ["not.an.action"]}
        with self.assertRaisesRegex(ValueError, "contains an unknown action"):
            load_server_config(self.write_config(payload))

        payload = config_payload()
        payload["deployment"] = {"denied_actions": ["keyboard.text", "keyboard.text"]}
        with self.assertRaisesRegex(ValueError, "must not contain duplicates"):
            load_server_config(self.write_config(payload))

        payload = config_payload()
        payload["policy_providers"] = {}
        with self.assertRaisesRegex(ValueError, "unknown fields: policy_providers"):
            load_server_config(self.write_config(payload))

        payload = config_payload()
        payload["recording"] = {"audit": False, "diagnostic": True}
        with self.assertRaisesRegex(ValueError, "diagnostic=true requires"):
            load_server_config(self.write_config(payload))

        payload = config_payload()
        payload["audit"] = {}
        with self.assertRaisesRegex(ValueError, "unknown fields: audit"):
            load_server_config(self.write_config(payload))

    def test_effective_config_is_non_secret(self):
        config = load_server_config(self.write_config(config_payload()))
        self.assertNotIn("api_key", config.effective_config()["proposal_provider"])
        self.assertEqual(config.effective_config()["transport"]["auth"], {"mode": "loopback"})

    def test_external_bind_requires_a_configured_bearer_token(self):
        payload = config_payload()
        payload["transport"]["host"] = "0.0.0.0"
        with self.assertRaisesRegex(ValueError, "requires a loopback host"):
            load_server_config(self.write_config(payload))

        payload["transport"]["auth"] = {
            "mode": "bearer-token", "token_env": "AUTOUI_TEST_TOKEN"
        }
        with patch.dict("os.environ", {}, clear=True):
            with self.assertRaisesRegex(ValueError, "AUTOUI_TEST_TOKEN"):
                load_server_config(self.write_config(payload))
        with patch.dict("os.environ", {"AUTOUI_TEST_TOKEN": "secret"}, clear=True):
            config = load_server_config(self.write_config(payload))
        self.assertEqual(config.transport_auth_mode, "bearer-token")
        self.assertEqual(config.transport_token_env, "AUTOUI_TEST_TOKEN")

    def test_registered_backend_factory_is_selected_without_a_platform_branch(self):
        backend_id = "test-desktop-registry"
        captured = {}

        def factory(**kwargs):
            captured.update(kwargs)
            return DesktopBackend(
                backend_id=backend_id,
                compositor=object(),
                executor=object(),
                frame_provider=object(),
                capture_observation=lambda: (b"", (0, 0), {}),
                create_tools=lambda _transactions, _run_blocking: object(),
            )

        register_desktop_backend(backend_id, factory)
        backend = create_desktop_backend(
            backend_id,
            tree_reader=lambda: {},
            cursor_reader=lambda: (0, 0),
            artifact_store=object(),
            capability_loader=lambda: [],
            capability_resolver=lambda _identifier: None,
            input_module=object(),
        )

        self.assertEqual(backend.backend_id, backend_id)
        self.assertIn(backend_id, available_desktop_backends())
        self.assertEqual(captured["tree_reader"](), {})
        self.assertTrue(callable(backend.create_tools))
