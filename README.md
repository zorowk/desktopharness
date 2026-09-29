# treeland-autogui-mcp

（[中文版](README_zh.md)）

This is an [MCP server](https://modelcontextprotocol.io/introduction) for safe, verifiable desktop automation. Its current implementation is centered on the compositor-neutral AutoUI v2 transaction core; Treeland is the first compositor adapter.

On Treeland, window-tree fusion uses the compositor-provided `treeland-debug --json tree`
command. Ensure `treeland-debug` is available in the MCP server's `PATH`.

## AutoUI v2 generic transaction core

The server registers the compositor-neutral `gui_run` lifecycle facade. The
shipped configuration enables full recording diagnostics, which additionally
registers the `gui_diagnostic` inspection facade; a deployment that turns that
mode off exposes `gui_run` alone. Its core depends only on
canonical models and replaceable ports; Treeland,
Qwen-CUA, PyAutoGUI, Deepin keybindings, and `dde-am` are adapters selected by
the application composition root.

The default v2 lifecycle is:

1. `gui_run(operation="run", task_contract=...)`
2. `gui_run(operation="status", task_id=...)`
3. `gui_run(operation="reset", task_id=...)` to begin again

Normal responses are compact envelopes containing a public task status. Persistent
`object_ref` values and the `gui_diagnostic` tool are available only when both
`recording.audit` and `recording.diagnostic` are enabled. Diagnostics provide `observe`, `propose`, `prepare`,
`execute`, `evaluate`, and `trace` when inspecting one controller stage or an
object. `claimed_intent` is diagnostic model output and does not control execution. An
execution receipt with `status=delivered` confirms input injection, not
application or task success.

Task contracts contain only the goal, assertions, limits, and verification profile.
Before injection, `ProposalValidator` checks action parameters, coordinates,
capabilities, targets, focus, and current desktop dependencies. Deployments can
optionally deny canonical actions through `deployment.denied_actions`.

See the [v2 implementation and extension guide](docs/treeland-autoui-mcp-v2-implementation.md)
and the [v2 design](docs/treeland-autoui-mcp-v2-design.md).

## Qwen-CUA

The default path uses the embedded Qwen-CUA service in this project; the
`gui-mcp` backend does not need to be started. Configure the OpenAI-compatible
Qwen model endpoint in `config/mcp-autoui.json`. Only the model API key remains
an environment secret:

```bash
export CUA_MODEL_API_KEY=your-model-api-key   # Optional when model auth is disabled
```

The former gui-mcp HTTP backend, binary/JSON fallback, and `CUA_BACKEND_*`
configuration are no longer supported.

All Qwen interaction goes through the unified lifecycle and diagnostic MCP
tools; the legacy `qwen_cua_*` tools were removed. The embedded backend is
addressed by the task contract, and each round produces one canonical Proposal
containing a non-empty ordered action sequence:

1. `gui_run(operation="run", task_contract={"task_id": ..., "goal": ...,
   "limits": {"max_steps": 5, "max_retries": 2}})`
   executes bounded Proposal transactions (each Proposal may contain an ordered action sequence):
   observe -> propose (Qwen) -> prepare -> recheck -> execute ->
   evaluate -> reduce state, until the task blocks or terminates.
2. Fine-grained inspection uses `gui_diagnostic` instead: `observe`,
   `propose`, `prepare`, `execute`, `evaluate`, and `trace`.
   It expands stored objects such as model output (`debug_ref`), execution
   receipts, assertion results, and Attribution. `status` and `reset` remain
   lifecycle operations on `gui_run`.
3. `gui_run(operation="reset", task_id=...)` resets the runtime task and the
   embedded Qwen session for a new task.

Raw-action restriction is opt-in and disabled in the default configuration:

```json
"deployment": {
  "denied_actions": ["keyboard.text", "keyboard.shortcut"]
}
```

Recording, unlike raw-action restriction, is enabled in the shipped
`config/mcp-autoui.json` so the diagnostic and screenshot probes are
available. Audit-only mode keeps the minimal task,
proposal, receipt, assertion, and transition history without screenshots, raw
model output, desktop trees, or full evidence. Diagnostic mode requires audit:

```json
"recording": {
  "audit": true,
  "diagnostic": true,
  "directory": ".autoui-audit",
  "retention_days": 7,
  "max_gib": 16
}
```

Window-level completion conditions are declared as task-contract assertions,
for example `assertions: [{"assertion_id": "application-active", "path":
"active_window.app_id", "operator": "equals", "expected": "deepin-editor"}]`.
The evaluator collects evidence after each action; a Qwen `DONE` action cannot
mark the task complete while an assertion is unverified.

The embedded service keeps each prediction pending and commits it to Qwen
history only after receiving the actual local execution result. Success,
partial execution, rejection, and failure are fed back explicitly.

OmniParser is disabled by default. When enabled, it is a read-only v2
Evidence/Grounding Provider: it registers no legacy direct-execution tools and
cannot bypass Proposal validation, Receipt, or Assertion processing. Install
its optional HTTP dependency before enabling it: `uv sync --extra omniparser`.

Start with the [documentation index](docs/README.md). Manual acceptance and repeatable tests are defined in the [AutoUI MCP v2 manual acceptance and regression plan](docs/manual-test-guide.md).

## Codex connection

`client_env.sh` prepares the Treeland session, clears obsolete runtime
environment settings, and starts the server with `config/mcp-autoui.json`.
Override the configuration path only with `AUTOUI_MCP_CONFIG`; it keeps session
variables and secrets such as `CUA_MODEL_API_KEY`, but not runtime behaviour.
Configure Codex with:

```bash
codex mcp add desktop_harness_mcp --url http://127.0.0.1:8651/mcp
```

The server binds to loopback by default. A trusted reverse proxy must keep its
upstream on loopback and provide TLS and authentication. A direct non-loopback
bind requires `transport.auth.mode="bearer-token"`, a `token_env` setting, and
an `Authorization: Bearer ...` header from every client.

## Installation

Package SemVer is independent from the v2.2 architecture revision. This
breaking pre-1.0 release is package version `0.2.0`; the domain and JSON
configuration schema versions remain `2`.

1. Please do the following:

```
git clone https://github.com/zorowk/treeland-aitests.git
cd treeland-aitests
uv sync
```

## Remote Deployment + LangChain Agent Connection

Run the MCP server on a **test machine** and connect from another machine via
streamable HTTP.

### 1) Test machine (run MCP server)

```bash
uv sync
uv run treeland-autogui-mcp --config config/mcp-autoui.json
```

Do not expose the unauthenticated MCP process. Use a TLS/authenticated reverse
proxy with a loopback upstream, or configure the server's bearer-token mode.

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
      "transport": "streamable-http",
      "url": "http://TEST_MACHINE_1_IP:8651/mcp"
    }
  }
}
```

Then run your LangChain agent (for example `langchain_example.py`). Install its
optional dependencies with `uv sync --extra langchain`. For bearer-token
deployments, provide the Authorization header through the client environment;
do not place a token in this file.

## Deployment reports

The `deploy-desktop-harness` skill writes a concise, evidence-based report after
deployment verification and publishes it to its configured report repository.
The report records only the result, environment, key checks, and next action.
Its GitLab token belongs in the untracked local skill configuration
`skills/deploy-desktop-harness/reporting.local.env`, never in project config,
source control, or a report. See the skill reference for the report format and
local configuration.
