import argparse
import os
import sys

from .desktop_backend import DEFAULT_DESKTOP_BACKEND
from .server_config import load_server_config


def main(argv: list[str] | None = None):
    parser = argparse.ArgumentParser(description="Run the AutoUI MCP server")
    parser.add_argument(
        "--config",
        help="path to the server JSON configuration file; environment settings remain a legacy fallback",
    )
    args = parser.parse_args([] if argv is None else argv)
    server_config = load_server_config(args.config) if args.config else None
    if server_config is not None:
        from mcp.server.fastmcp import FastMCP
        from .mcp_autogui_main import mcp_autogui_main

        mcp_main = FastMCP("treeland_autogui_mcp",
            host=server_config.transport_host,
            port=server_config.transport_port,
        )
        mcp_autogui_main(
            mcp_main,
            desktop_backend_kind=server_config.desktop_backend,
            proposal_provider_config=server_config.proposal_provider,
            evidence_provider_config=server_config.evidence_providers,
            audit_config=server_config.audit,
        )
        mcp_main.run(server_config.transport_mode)
        return

    from mcp.server.fastmcp import FastMCP
    from .mcp_autogui_main import mcp_autogui_main
    if 'SSE_HOST' in os.environ:
        transport = os.environ.get('MCP_TRANSPORT', 'sse')
        if transport not in {'sse', 'streamable-http'}:
            raise ValueError("MCP_TRANSPORT must be 'sse' or 'streamable-http'")
        mcp_main = FastMCP("treeland_autogui_mcp",
            host=os.environ['SSE_HOST'],
            port=os.environ['SSE_PORT'] if 'SSE_PORT' in os.environ else 8000,
        )
        mcp_autogui_main(mcp_main, desktop_backend_kind=DEFAULT_DESKTOP_BACKEND)
        mcp_main.run(transport)
        return

    mcp_main = FastMCP("treeland_autogui_mcp")
    mcp_autogui_main(mcp_main, desktop_backend_kind=DEFAULT_DESKTOP_BACKEND)
    mcp_main.run()


def cli_main() -> None:
    main(sys.argv[1:])
