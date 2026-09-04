# treeland-autogui-mcp

（[中文版](README_zh.md)）

This is an [MCP server](https://modelcontextprotocol.io/introduction) for safe, verifiable desktop automation. Its current implementation is centered on the compositor-neutral AutoUI v2 transaction core; Treeland is the first compositor adapter.

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

All Qwen interaction goes through the unified `gui_run` tool; the legacy
`qwen_cua_*` tools were removed. The embedded backend is addressed by the
task contract, and each round produces exactly one canonical action:

1. `gui_run(operation="run", task_contract={"task_id": ..., "goal": ...,
   "permissions": {...}, "limits": {"max_steps": 5, "max_retries": 2},
   "policy_overrides": {"unknown": "allow", "content_edit": "allow"}})`
   executes bounded single-action transactions:
   observe -> propose (Qwen) -> decide -> guard recheck -> execute ->
   evaluate -> reduce state, until the task blocks or terminates.
2. Fine-grained control uses the explicit operations instead: `observe`,
   `propose`, `decide`, `execute`, `evaluate` (or `verify`), `status`,
   `reset`, and `trace`. Responses return object references; pass
   `diagnostic=true` or use `trace` to expand a stored object such as the
   model output (`debug_ref`), the execution receipt, or the assertion
   results.
3. `gui_run(operation="reset", task_id=...)` resets the runtime task and the
   embedded Qwen session for a new task.

Window-level completion conditions are declared as task-contract assertions,
for example `assertions: [{"assertion_id": "application-active", "path":
"active_window.app_id", "operator": "equals", "expected": "deepin-editor"}]`.
The evaluator collects evidence after each action; a Qwen `DONE` action cannot
mark the task complete while an assertion is unverified.

The embedded service keeps each prediction pending and commits it to Qwen
history only after receiving the actual local execution result. Success,
partial execution, rejection, and failure are fed back explicitly. The old HTTP
compatibility backend still resets a session when it cannot accept execution
feedback.

OmniParser is disabled by default. When enabled, it is a read-only v2 Evidence/Grounding Provider: it registers no legacy direct-execution tools and cannot bypass Proposal, PolicyDecision, Guard, Receipt, or Assertion processing.

Start with the [documentation index](docs/README.md). Manual acceptance and repeatable tests are defined in the [AutoUI MCP v2 manual acceptance and regression plan](docs/manual-test-guide.md).

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
