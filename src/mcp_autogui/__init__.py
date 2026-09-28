import argparse
import json
import logging
import sys

from .server_config import load_server_config


def _configure_plain_server_logging() -> None:
    """Use stable plain-text logs instead of FastMCP's Rich path renderer."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        force=True,
    )


def main(argv: list[str] | None = None):
    parser = argparse.ArgumentParser(description="Run the AutoUI MCP server")
    parser.add_argument(
        "--config",
        help="path to the required server JSON configuration file",
    )
    args = parser.parse_args([] if argv is None else argv)
    if not args.config:
        parser.error("--config is required; move non-secret runtime settings into the JSON configuration file")
    from mcp.server.fastmcp import FastMCP
    from .mcp_autogui_main import mcp_autogui_main
    server_config = load_server_config(args.config)
    _configure_plain_server_logging()
    effective_config = server_config.effective_config()
    logging.getLogger(__name__).info(
        "AutoUI MCP effective configuration: %s",
        json.dumps(effective_config, ensure_ascii=False, sort_keys=True),
    )
    from .transport_auth import fastmcp_auth_kwargs
    mcp_main = FastMCP("desktop_harness_mcp",
        host=server_config.transport_host,
        port=server_config.transport_port,
        **fastmcp_auth_kwargs(server_config),
    )
    mcp_autogui_main(
        mcp_main,
        desktop_backend_kind=server_config.desktop_backend,
        proposal_provider_config=server_config.proposal_provider,
        denied_actions=server_config.deployment_denied_actions,
        evidence_provider_config=server_config.evidence_providers,
        recording_config=server_config.recording,
        effective_config=effective_config,
    )
    mcp_main.run(server_config.transport_mode)


def cli_main() -> None:
    main(sys.argv[1:])
