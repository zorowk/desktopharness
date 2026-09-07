import json
import os
import sys
import types
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from mcp_autogui import main


class EntrypointTests(unittest.TestCase):
    def _run_server(self, transport):
        instances = []

        class FakeFastMCP:
            def __init__(self, name, **kwargs):
                self.name = name
                self.kwargs = kwargs
                self.transport = None
                instances.append(self)

            def run(self, selected_transport=None):
                self.transport = selected_transport

        fastmcp_module = types.ModuleType("mcp.server.fastmcp")
        fastmcp_module.FastMCP = FakeFastMCP
        main_module = types.ModuleType("mcp_autogui.mcp_autogui_main")
        main_module.mcp_autogui_main = lambda _mcp, **_kwargs: None
        environment = {"SSE_HOST": "127.0.0.1", "SSE_PORT": "8000"}
        if transport is not None:
            environment["MCP_TRANSPORT"] = transport
        with patch.dict(os.environ, environment, clear=True), patch.dict(
            sys.modules,
            {
                "mcp.server.fastmcp": fastmcp_module,
                "mcp_autogui.mcp_autogui_main": main_module,
            },
        ):
            main()
        return instances[0]

    def test_streamable_http_can_be_selected(self):
        server = self._run_server("streamable-http")

        self.assertEqual(server.transport, "streamable-http")
        self.assertEqual(server.kwargs, {"host": "127.0.0.1", "port": "8000"})

    def test_sse_remains_the_compatibility_default(self):
        server = self._run_server(None)

        self.assertEqual(server.transport, "sse")

    def test_invalid_transport_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "MCP_TRANSPORT"):
            self._run_server("invalid")

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
            "schema_version": 1,
            "transport": {"mode": "streamable-http", "host": "127.0.0.1", "port": 8651},
            "desktop_backend": {"kind": "treeland-deepin"},
            "proposal_provider": {"kind": "qwen-cua", "mode": "embedded"},
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
                    kwargs["evidence_provider_config"],
                )
            )
            with patch.dict(os.environ, {}, clear=True), patch.dict(
                sys.modules,
                {
                    "mcp.server.fastmcp": fastmcp_module,
                    "mcp_autogui.mcp_autogui_main": main_module,
                },
            ):
                main(["--config", str(path)])

        self.assertEqual(
            selected_backends,
            [("treeland-deepin", {"kind": "qwen-cua", "mode": "embedded"}, {})],
        )
        self.assertEqual(instances[0].kwargs, {"host": "127.0.0.1", "port": "8651"})
        self.assertEqual(instances[0].transport, "streamable-http")


if __name__ == "__main__":
    unittest.main()
