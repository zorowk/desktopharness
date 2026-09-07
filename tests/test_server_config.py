import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from mcp_autogui.desktop_backend import (
    DesktopBackend,
    available_desktop_backends,
    create_desktop_backend,
    register_desktop_backend,
)
from mcp_autogui.server_config import load_server_config


def config_payload(*, backend="treeland-deepin"):
    return {
        "schema_version": 1,
        "transport": {"mode": "streamable-http", "host": "127.0.0.1", "port": 8651},
        "desktop_backend": {"kind": backend},
        "proposal_provider": {
            "kind": "qwen-cua",
            "mode": "embedded",
            "model": "qwen3_rl",
            "base_url": "http://127.0.0.1:8000/v1",
            "timeout_seconds": 120,
            "tls_verify": True,
        },
        "evidence_providers": {"omniparser": {"enabled": False, "endpoint": ""}},
        "audit": {"directory": "/tmp/autoui-audit", "retention_days": 3, "max_gib": 16},
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
        self.assertEqual(config.proposal_provider["model"], "qwen3_rl")
        self.assertFalse(config.evidence_providers["omniparser"]["enabled"])
        self.assertEqual(config.audit["retention_days"], 3)

    def test_unknown_backend_is_rejected_before_server_start(self):
        with self.assertRaisesRegex(ValueError, "desktop_backend.kind"):
            load_server_config(self.write_config(config_payload(backend="other-desktop")))

    def test_unknown_or_invalid_nested_configuration_is_rejected(self):
        payload = config_payload()
        payload["evidence_providers"]["compositor_window"] = {"enabled": "yes"}
        with self.assertRaisesRegex(ValueError, "enabled must be true or false"):
            load_server_config(self.write_config(payload))

        payload = config_payload()
        payload["audit"]["unexpected"] = True
        with self.assertRaisesRegex(ValueError, "audit has unknown fields"):
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
                read_raw_tree=lambda: {},
                capture_observation=lambda: (b"", (0, 0), {}),
                application_launcher=None,
                policy_providers=(),
                list_capabilities=lambda: [],
                find_capability=lambda _identifier: None,
                list_applications=lambda: [],
                validate_application_id=lambda value: value,
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
