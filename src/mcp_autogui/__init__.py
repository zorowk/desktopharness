import os

def main():
    from mcp.server.fastmcp import FastMCP
    from .mcp_autogui_main import mcp_autogui_main
    if 'SSE_HOST' in os.environ:
        mcp_main = FastMCP("treeland_autogui_mcp",
            host=os.environ['SSE_HOST'],
            port=os.environ['SSE_PORT'] if 'SSE_PORT' in os.environ else 8000,
        )
        mcp_autogui_main(mcp_main)
        mcp_main.run('sse')
    else:
        mcp_main = FastMCP("treeland_autogui_mcp")
        mcp_autogui_main(mcp_main)
        mcp_main.run()
