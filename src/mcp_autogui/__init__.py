import argparse
import os
import sys

from .desktop_backend import DEFAULT_DESKTOP_BACKEND
from .server_config import apply_server_config, load_server_config


def main(argv: list[str] | None = None):
    parser = argparse.ArgumentParser(description="Run the AutoUI MCP server")
    parser.add_argument(
        "--config",
        help="path to the server JSON configuration file; environment settings remain a legacy fallback",
    )
    args = parser.parse_args([] if argv is None else argv)
    server_config = load_server_config(args.config) if args.config else None
    if server_config is not None:
        apply_server_config(server_config)

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
        mcp_autogui_main(
            mcp_main,
            desktop_backend_kind=(server_config.desktop_backend if server_config else DEFAULT_DESKTOP_BACKEND),
            proposal_provider_config=(server_config.proposal_provider if server_config else None),
            evidence_provider_config=(server_config.evidence_providers if server_config else None),
        )
        mcp_main.run(transport)
    else:
        mcp_main = FastMCP("treeland_autogui_mcp")
        mcp_autogui_main(
            mcp_main,
            desktop_backend_kind=(server_config.desktop_backend if server_config else DEFAULT_DESKTOP_BACKEND),
            proposal_provider_config=(server_config.proposal_provider if server_config else None),
            evidence_provider_config=(server_config.evidence_providers if server_config else None),
        )
        mcp_main.run()


def cli_main() -> None:
    main(sys.argv[1:])
