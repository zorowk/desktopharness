# treeland-autogui-mcp

（[中文版](README_zh.md)）

This is an [MCP server](https://modelcontextprotocol.io/introduction) that analyzes the screen with [OmniParser](https://github.com/microsoft/OmniParser) and automatically operates the GUI.
Confirmed on Windows.

On Treeland, window-tree fusion uses the compositor-provided `treeland-debug --tree`
command. Ensure `treeland-debug` is available in the MCP server's `PATH`.

## Installation

1. Please do the following:

```
git clone https://github.com/zorowk/treeland-aitests.git
cd treeland-aitests
uv sync
```

## Remote Deployment + LangChain Agent Connection (SSE)

Run the MCP server on a **test machine** and connect from another machine via SSE.

### 1) Test machine (run MCP server)

```bash
uv sync
SSE_HOST=0.0.0.0 SSE_PORT=8000 uv run treeland-autogui-mcp
```

Expose port `8000` and note the test machine IP.

### 2) Control machine (LangChain agent)

Use the remote config template:

```bash
cp langchain_settings/mcp_config.remote.json langchain_settings/mcp_config.json
```

Edit `langchain_settings/mcp_config.json`:

```json
{
  "mcpServers": {
    "mcp_machine_01": {
      "transport": "sse",
      "url": "http://TEST_MACHINE_1_IP:8000/sse"
    }
  }
}
```

Then run your LangChain agent (for example `langchain_example.py`) to connect via SSE.
(If you want ``langchain_example.py`` to work, ``uv sync --extra langchain`` instead.)
