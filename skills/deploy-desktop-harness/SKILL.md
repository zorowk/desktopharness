---
name: deploy-desktop-harness
description: >-
  Deploy or repair DesktopHarness on a user-supplied desktop machine over SSH, start its MCP server
  in the real graphical session, and verify approved desktop-control probes across supported
  compositors.
---

# Deploy DesktopHarness

Prepare a supplied desktop machine as a DesktopHarness MCP node. Do not create machines or alter
the local controller. Application testing follows the separate playbook below.

## Inputs and entry point

Require six inputs from the controlling AI: target IP, SSH username, SSH password, Qwen API key,
Treeland repository URL, and branch/tag/commit. Do not infer them or substitute upstream for a fork.
Enter passwords through interactive prompts; never put them in arguments, files, logs, or JSON.
Inject `CUA_MODEL_API_KEY` into the process environment without displaying it or putting it in JSON.
The helper transfers the key over SCP and stores it in the remote user's `.env.local` with mode
`0600`.

```sh
# CUA_MODEL_API_KEY is already injected into the process environment.
scripts/deploy.sh --host <ip> --user <ssh-user> \
  --treeland-source <git-url> --treeland-ref <branch|tag|commit> [--apt-upgrade] [--sudo-password]
```

Use `scripts/deploy.sh` as the sole deployment entry point. It resolves and builds the exact
selected commit in Debug mode, installs under `/usr`, restarts Treeland, and provisions
DesktopHarness. A working existing `treeland-debug` must not bypass the requested build.
The target starts MCP only through its checkout's `client_env.sh`.

Read [references/treeland-build.md](references/treeland-build.md) before deployment for apt,
sudo prerequisites, and build failure handling. Use `--sudo-password` to enter the supplied SSH
password at the sudo prompt when both passwords match; it is sent only over encrypted SSH.
The helper runs `apt-get update`; `--apt-upgrade`
opts into `apt-get upgrade -y`. State the concrete change plan and obtain only authorization still
missing from the user's request before running the entry point.

## Workflow

1. Inventory through read-only `scripts/remote_exec.sh` using `SSH_HOST`, `SSH_USER`, and optional
   `SSH_PORT` / `SSH_IDENTITY_FILE`. Discover the actual graphical user, session, and environment
   through `loginctl` and `/proc/<session-leader>/environ`; do not invent display or bus variables.
   Check sudo access, checkout state, dependencies, running MCP, and backend support. Reuse healthy
   DesktopHarness components; stop at `DEPENDENCY` for an unsupported backend.
2. Run the authorized deployment. DesktopHarness clones into an absent directory or fast-forwards
   a clean checkout, and starts as the discovered desktop user. Add `--prelogin` for a Treeland
   login/lock-screen test. Never change DDM automatic login; use MCP/PyAutoGUI with the supplied
   desktop password when unlocking is needed.
3. Read [references/acceptance-contract.md](references/acceptance-contract.md), then run
   `scripts/verify_mcp.py` against the actual endpoint. Require observe and screenshot evidence.
   `--input-probe` requires an explicitly approved harmless `task_contract` and `proposal` in a
   supplied JSON file; do not invent probe coordinates, keys, or targets. A PID or open port alone
   does not establish `READY`.
4. Fill `assets/deployment-report-template.md` with observed facts; unknowns stay `未采集`.
   Save as `<machine>-<YYYYMMDDTHHMMSSZ>.md`. Read
   [references/report-publishing.md](references/report-publishing.md) and publish with
   `scripts/publish_report.sh` when `REPORT_AUTO_PUBLISH=true`, including failed/partial/blocked
   outcomes. Do not publish if report creation or sanitization fails. Keep `reporting.local.env`
   ignored and out of commits. Return the acceptance contract's result schema.

## Testing after deployment

When asked to test Treeland, the controlling AI must read the remote checkout's
`docs/treeland-mcp-test-playbook.md` in full through read-only `scripts/remote_exec.sh`.
Locate it under `DESKTOPHARNESS_DIR`, defaulting to the discovered desktop user's home plus
`/desktopharness`. Execute the playbook through deployed MCP tools as a simulated human operator;
do not substitute a local runner, SSH window commands, or direct `treeland-debug` control.

## Boundaries

- Retry SSH/HTTP at most twice after the initial attempt.
- Do not reset the system, perform release upgrades, overwrite dirty checkouts, modify project
  source, delete user data, or stop unrelated services.
- Keep network exposure within the user's authorization; report the actual endpoint.
- Stop at the corresponding failure phase when session discovery, privilege, repository state,
  dependencies, or a required probe cannot be established.
