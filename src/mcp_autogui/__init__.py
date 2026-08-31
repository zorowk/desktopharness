import os

def main():
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
        mcp_autogui_main(mcp_main)
        mcp_main.run(transport)
    else:
        mcp_main = FastMCP("treeland_autogui_mcp")
        mcp_autogui_main(mcp_main)
        mcp_main.run()
