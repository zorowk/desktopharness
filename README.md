# treeland-autogui-mcp

（[中文版](README_zh.md)）

This is an [MCP server](https://modelcontextprotocol.io/introduction) that analyzes the screen with [OmniParser](https://github.com/microsoft/OmniParser) and automatically operates the GUI.
Confirmed on Windows.

On Treeland, window-tree fusion uses the compositor-provided `treeland-debug --tree`
command. Ensure `treeland-debug` is available in the MCP server's `PATH`.

## AutoUI v2 generic transaction core

The server now also registers the compositor-neutral `gui_run` facade. Its
core depends only on canonical models and replaceable ports; Treeland,
Qwen-CUA, PyAutoGUI, Deepin keybindings, and `dde-am` are adapters selected by
the application composition root.

The explicit v2 flow is:

1. `gui_run(operation="observe", task_contract=...)`
2. `gui_run(operation="propose", task_id=...)` for Qwen, or submit one controller proposal with the same operation
3. `gui_run(operation="decide", task_id=..., proposal_id=...)`
4. `gui_run(operation="execute", task_id=..., proposal_id=...)`
5. `gui_run(operation="evaluate", task_id=...)`

Normal responses are compact envelopes containing an `object_ref`. Use
`gui_run(operation="trace", object_ref=...)` for diagnostic expansion. The
default policy requires confirmation when an action has no independent
semantic evidence; a model's `semantic_intent` is only a claim. An execution
receipt with `status=delivered` confirms input injection, not application or
task success.

See the [v2 implementation and extension guide](docs/treeland-autoui-mcp-v2-implementation.md)
and the [v2 design](docs/treeland-autoui-mcp-v2-design.md).

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

These `qwen_cua_*` tools remain migration-compatible. New integrations should
use `gui_run`; its Qwen adapter rejects multi-action proposals.

For tasks with a window-level completion condition, such as opening an
application, pass `expected_active_app_id="deepin-editor"` on the first
prediction. After execution, the MCP polls the Treeland tree for
`application_wait_timeout_s` (three seconds by default), then captures one
final screenshot and tree. It returns
`task_completed: true` when the expected app becomes active; a wrong app or a
timeout returns `status: "partial"` with structured `task_validation` while
keeping the session available for correction. A Qwen `DONE` action cannot
bypass this assertion. Later predictions in the same session inherit the
expected appId when the argument is omitted.

For a controller-led coordinate calibration while still using Qwen, pass
`expected_action="mouse_move"` and
`expected_screenshot_coordinate=[x, y]` to `qwen_cua_predict`. The control
layer converts the screenshot target to Qwen's native 0..999 coordinate space,
requires that single Qwen proposal, and rejects a result outside the configured
pixel tolerance. Ordinary visual tasks should omit these optional constraints.

The embedded service keeps each prediction pending and commits it to Qwen
history only after receiving the actual local execution result. Success,
partial execution, rejection, and failure are fed back explicitly. The old HTTP
compatibility backend still resets a session when it cannot accept execution
feedback.

The legacy OmniParser tools remain available for comparison tests but are not registered by default. Enable them with `GUI_OMNIPARSER_ENABLED=1` and `OMNI_PARSER_SERVER=host:port`.

See [Qwen-CUA and Treeland architecture](docs/qwen-cua-architecture.md) for the design and evaluation plan.
See the [manual test guide](docs/manual-test-guide.md) for safe acceptance and repeatability tests.

## Codex connection

`client_env.sh` starts the server using Streamable HTTP. Configure Codex with:

```bash
codex mcp add treeland_autogui_mcp --url http://127.0.0.1:8000/mcp
```

Set `MCP_TRANSPORT=sse` only for a legacy SSE client; its endpoint remains
`http://127.0.0.1:8000/sse`.

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
