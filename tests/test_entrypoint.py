import json
import os
import sys
import types
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from mcp_autogui import _configure_plain_server_logging, main


class EntrypointTests(unittest.TestCase):
    def test_server_logging_uses_a_plain_forced_formatter(self):
        with patch("mcp_autogui.logging.basicConfig") as configure:
            _configure_plain_server_logging()

        self.assertEqual(configure.call_args.kwargs["format"], "%(asctime)s %(levelname)s %(name)s: %(message)s")
        self.assertTrue(configure.call_args.kwargs["force"])

    def test_config_is_required_even_when_legacy_environment_is_present(self):
        with patch.dict(os.environ, {"SSE_HOST": "127.0.0.1", "MCP_TRANSPORT": "sse"}, clear=True):
            with self.assertRaisesRegex(SystemExit, "2"):
                main([])

    def test_json_config_selects_the_desktop_backend(self):
        instances = []
        selected_backends = []

        class FakeFastMCP:
            def __init__(self, name, **kwargs):
                self.name = name
                self.kwargs = kwargs
                self.transport = None
                instances.append(self)

            def run(self, selected_transport=None):
                self.transport = selected_transport

        config = {
            "schema_version": 2,
            "transport": {
                "mode": "streamable-http", "host": "127.0.0.1", "port": 8651,
                "auth": {"mode": "loopback"},
            },
            "desktop_backend": {"kind": "treeland-deepin"},
            "proposal_provider": {"kind": "qwen-cua"},
        }
        with TemporaryDirectory() as directory:
            path = Path(directory) / "mcp-autoui.json"
            path.write_text(json.dumps(config), encoding="utf-8")
            fastmcp_module = types.ModuleType("mcp.server.fastmcp")
            fastmcp_module.FastMCP = FakeFastMCP
            main_module = types.ModuleType("mcp_autogui.mcp_autogui_main")
            main_module.mcp_autogui_main = lambda _mcp, **kwargs: selected_backends.append(
                (
                    kwargs["desktop_backend_kind"],
                    kwargs["proposal_provider_config"],
                    kwargs["denied_actions"],
                    kwargs["evidence_provider_config"],
                    kwargs["recording_config"],
                    kwargs["effective_config"],
                )
            )
            with patch.dict(os.environ, {"SSE_HOST": "legacy-host", "SSE_PORT": "9000"}, clear=True), patch.dict(
                sys.modules,
                {
                    "mcp.server.fastmcp": fastmcp_module,
                    "mcp_autogui.mcp_autogui_main": main_module,
                },
            ):
                main(["--config", str(path)])

        self.assertEqual(
            selected_backends,
            [
                (
                    "treeland-deepin",
                    {"kind": "qwen-cua"},
                    frozenset(),
                    {},
                    {
                        "audit": False,
                        "diagnostic": False,
                        "directory": ".autoui-audit",
                        "retention_days": 7,
                        "max_gib": 16,
                    },
                    {
                        "config_path": str(path),
                        "transport": {
                            "mode": "streamable-http", "host": "127.0.0.1", "port": 8651,
                            "auth": {"mode": "loopback"},
                        },
                        "desktop_backend": "treeland-deepin",
                        "proposal_provider": {"kind": "qwen-cua"},
                        "deployment": {"denied_actions": []},
                        "evidence_providers": {},
                        "recording": {
                            "audit": False,
                            "diagnostic": False,
                            "directory": ".autoui-audit",
                            "retention_days": 7,
                            "max_gib": 16,
                        },
                    },
                )
            ],
        )
        self.assertEqual(instances[0].kwargs, {"host": "127.0.0.1", "port": 8651})
        self.assertEqual(instances[0].transport, "streamable-http")


if __name__ == "__main__":
    unittest.main()
