---
name: deploy-desktop-harness
description: >-
  Deploy or repair DesktopHarness on a user-supplied desktop machine over SSH, start its MCP server
  in the real graphical session, and verify approved desktop-control probes across supported
  compositors.
---

# Deploy DesktopHarness

Use this skill only when the user wants a supplied physical desktop machine prepared or repaired as
a DesktopHarness MCP node. It does not create machines, run application tests, or alter the local
controller.

## Required connection data

Do not begin deployment until the controlling AI has all four minimum inputs: the target IP address,
SSH username, SSH password, and Qwen API key. Do not infer any of them. Enter the SSH password only
through SSH's interactive prompt; never place it in an argument, shell variable, file, log, result,
or JSON.

The controlling AI must set `CUA_MODEL_API_KEY` in the provisioning process environment before a
Qwen-enabled deployment. The helper transfers it in a temporary `0600` file over encrypted SCP,
deletes the remote temporary copy immediately after reading it, stores it only in the remote desktop
user's `.env.local` with mode `0600`, and loads it only for the MCP server process. Never ask the
user to paste it into a command, put it in JSON, or display it. This does not require SSHD `AcceptEnv`.
The direct deployment start path explicitly exports the key to `treeland-autogui-mcp`; the project's
`client_env.sh` also exports `.env.local` before it starts that process and refuses startup if the
key is absent.

## Entry point

Run only `scripts/deploy.sh`. It first verifies `treeland-debug --json tree`; only a failed check
triggers a matching Debug Treeland build, in-place install, and service restart. All lower-level
helpers are internal.

DesktopHarness is started through the target checkout's `client_env.sh`; do not invoke the MCP
binary directly, because `client_env.sh` owns the ydotool, udev, and desktop-session setup.

## Treeland simulated-manual testing

When the user asks to test Treeland after deployment, the **controlling AI** must read
[`references/treeland-mcp-test-playbook.md`](references/treeland-mcp-test-playbook.md) in full and
execute it in order through the deployed MCP endpoint. This is not a second deployment mechanism:
the target still starts only through `client_env.sh`, while the controlling AI uses the normal MCP
tools as a simulated human operator. Do not replace the playbook with a local runner, SSH window
commands, or direct `treeland-debug` control.

```sh
CUA_MODEL_API_KEY=... scripts/deploy.sh --host <ip> --user <ssh-user> \
  [--treeland-source <git-url>] [--treeland-ref <branch|tag|commit>]
```

The facade calls, in order: Treeland preflight; conditional ref resolution and Debug build; then
DesktopHarness provisioning. Do not invoke internal helpers directly.

State the exact remote change plan and obtain confirmation immediately before running
`scripts/provision_remote.sh`. Checking connectivity and system state is read-only; installation,
updating, or starting a service is not.

## Workflow

1. Run `scripts/remote_exec.sh` for a bounded, read-only inventory. Collect the actual
   graphical-session user, session type, and environment from `loginctl` and
   `/proc/<session-leader>/environ`; do not invent `WAYLAND_DISPLAY`, `DISPLAY`,
   `XDG_RUNTIME_DIR`, or `DBUS_SESSION_BUS_ADDRESS`.
2. Check the current repository, `uv`, Python, dependencies, running process, configured backend,
   endpoint, and MCP describe request. Reuse healthy components. Run compositor-specific checks
   only when the selected backend requires them.
3. After approval, run `scripts/provision_remote.sh`. It clones only when the selected project
   directory is absent, updates only a clean existing checkout via fast-forward, and starts the
   server as the discovered desktop-session user. It requires `CUA_MODEL_API_KEY` when the checked
   configuration enables the embedded Qwen provider.
4. Run `scripts/verify_mcp.py` against the endpoint. Observe and screenshot evidence are required.
   An input probe is deliberately opt-in: it must use a user-approved, harmless `task_contract` and
   `proposal` supplied in a JSON file; do not make up coordinates, keys, or a target application.
5. After verification, fill `assets/deployment-report-template.md` from the observed facts into a
   local file named `<machine>-<YYYYMMDDTHHMMSSZ>.md`, then run
   `scripts/publish_report.sh` for that report. Publish `READY`, `FAILED`, `PARTIAL`, and `BLOCKED`
   results alike when `REPORT_AUTO_PUBLISH=true`; do not publish if sanitization or report creation
   fails.
6. Return only the result schema in
   [references/acceptance-contract.md](references/acceptance-contract.md). `READY` requires every
   listed gate, not merely a PID or open port.

## Boundaries

- Retry SSH and HTTP requests at most twice after the initial attempt. Do not wait indefinitely.
- Never reset the system, upgrade the OS, overwrite a dirty checkout, modify project source, delete
  user data, or stop unrelated services.
- An explicitly authorized Debug test build may replace Treeland under `/usr` and restart its
  matching service.
- Do not expose a network endpoint wider than the user approved. The project configuration currently
  defaults to streamable HTTP at `/mcp`; report the endpoint actually configured.
- A generic deployment skill cannot make an unsupported compositor work. If the installed
  DesktopHarness backend does not support the detected compositor, stop at `DEPENDENCY` and report
  the backend mismatch.
- If desktop session discovery, required privilege, repository state, or a probe cannot be
  established, stop at the corresponding failure phase rather than guessing or compensating with
  broad system changes.

## Helpers

- `scripts/remote_exec.sh`: bounded SSH transport. Inputs come from `SSH_HOST`, `SSH_USER`,
  optional `SSH_PORT`, and optional `SSH_IDENTITY_FILE`.
- `scripts/provision_remote.sh`: idempotent remote inventory/install/start routine. It requires
  `SSH_HOST` and `SSH_USER`; review its generated remote plan before execution.
- `scripts/verify_mcp.py`: MCP protocol and evidence probe. Use `--input-probe` only with explicit
  authorization and a supplied JSON probe definition.
- `scripts/deploy.sh`: the only public deployment facade.

## Report publishing

After deployment verification, the controlling model writes and publishes one report to the
separate, user-owned Git repository by default. Read
[`references/report-publishing.md`](references/report-publishing.md) before configuring or using it.

`reporting.local.env` lives beside this skill and is deliberately ignored by Git. It contains the
repository destination and optional local identity/token settings; never add it to a commit. Use
`assets/deployment-report-template.md` as the report structure. The controlling model fills it
only from observed deployment and probe facts; unknown values remain `未采集`, never inferred.
Use `scripts/publish_report.sh` with the completed, sanitized report. It creates a
report-repository commit and pushes it automatically after testing; `--dry-run` is available for
local checks.
