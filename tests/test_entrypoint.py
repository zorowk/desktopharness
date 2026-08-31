import os
import sys
import types
import unittest
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
        main_module.mcp_autogui_main = lambda _mcp: None
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


if __name__ == "__main__":
    unittest.main()
