# treeland-autogui-mcp

（[中文版](README_zh.md)）

This is an [MCP server](https://modelcontextprotocol.io/introduction) that analyzes the screen with [OmniParser](https://github.com/microsoft/OmniParser) and automatically operates the GUI.
Confirmed on Windows.

On Treeland, window-tree fusion uses the compositor-provided `treeland-debug --tree`
command. Ensure `treeland-debug` is available in the MCP server's `PATH`.

## Qwen-CUA

The default path uses the embedded Qwen-CUA service in this project; the
`gui-mcp` backend does not need to be started. Configure the actual
OpenAI-compatible Qwen model endpoint:

```bash
export CUA_BACKEND_MODE=embedded
export CUA_MODEL_BASE_URL=http://127.0.0.1:8000/v1
export CUA_MODEL=qwen3_rl
export CUA_MODEL_API_KEY=your-model-api-key   # Optional when model auth is disabled
export CUA_MODEL_TLS_VERIFY=1                 # Set 0 only for a self-signed test endpoint
```

For comparison with the old deployment, set `CUA_BACKEND_MODE=http` and use
`CUA_BACKEND_URL`, `CUA_BACKEND_API_KEY`, and `CUA_TLS_VERIFY`. HTTP mode is an
optional compatibility path, not the default dependency.

The Qwen tools use an explicit two-stage flow:

1. Call `qwen_cua_predict(instruction)` to receive a `session_id`, proposed Qwen actions, and deterministic Treeland window context. Prediction does not execute actions.
2. Inspect `fused_actions`, then call `qwen_cua_execute(session_id, action_indexes)` to execute all or selected allowlisted actions.
3. Call `qwen_cua_predict` again with the same session for the next step. Use a new session or call `qwen_cua_reset` for a new task.

The embedded service keeps each prediction pending and commits it to Qwen
history only after receiving the actual local execution result. Success,
partial execution, rejection, and failure are fed back explicitly. The old HTTP
compatibility backend still resets a session when it cannot accept execution
feedback.

The legacy OmniParser tools remain available for comparison tests but are not registered by default. Enable them with `GUI_OMNIPARSER_ENABLED=1` and `OMNI_PARSER_SERVER=host:port`.

See [Qwen-CUA and Treeland architecture](docs/qwen-cua-architecture.md) for the design and evaluation plan.

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
