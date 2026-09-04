import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from mcp_autogui.desktop_backend import available_desktop_backends
from mcp_autogui.server_config import apply_server_config, load_server_config


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
        with patch.dict(os.environ, {}, clear=True):
            apply_server_config(config)
            self.assertEqual(os.environ["MCP_TRANSPORT"], "streamable-http")
            self.assertEqual(os.environ["SSE_PORT"], "8651")
            self.assertEqual(os.environ["CUA_MODEL"], "qwen3_rl")
            self.assertEqual(os.environ["GUI_OMNIPARSER_ENABLED"], "0")
            self.assertEqual(os.environ["GUI_AUDIT_RETENTION_DAYS"], "3")

    def test_unknown_backend_is_rejected_before_server_start(self):
        with self.assertRaisesRegex(ValueError, "desktop_backend.kind"):
            load_server_config(self.write_config(config_payload(backend="other-desktop")))
