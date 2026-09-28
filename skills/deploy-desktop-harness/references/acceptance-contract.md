# DesktopHarness deployment acceptance contract

Report one failed phase and its safe, concrete reason when a gate cannot be met:

`SSH_CONNECT`, `SYSTEM_CHECK`, `DESKTOP_SESSION`, `DEPENDENCY`, `DESKTOPHARNESS_INSTALL`,
`DESKTOPHARNESS_START`, `MCP_CONNECT`, or `DESKTOP_PROBE`.

`READY` is permitted only when all of these are evidenced: `MACHINE_REACHABLE`,
`DESKTOP_SESSION_READY`, `COMPOSITOR_BACKEND_AVAILABLE`, `DESKTOPHARNESS_INSTALLED`,
`DESKTOPHARNESS_RUNNING`, `MCP_REACHABLE`, `DESKTOP_OBSERVE_WORKS`, `SCREENSHOT_WORKS`, and
`INPUT_WORKS`.

The default AutoUI configuration intentionally does not register
`gui_diagnostic`. The deployment verifier therefore always proves MCP reachability
through `gui_run(operation="describe")`; it performs observe, screenshot, and
opt-in input probes only with a separate test configuration that enables both
`recording.audit` and `recording.diagnostic`. Probe task contracts must contain
only `task_id`, `goal`, assertions when needed, and limits—never legacy
permissions, policy overrides, confirmation fields, or secrets.

Do not include credentials, private keys, tokens, raw environment dumps, or application content in
the report.

```json
{
  "state": "READY",
  "hostname": "desktop-test-01",
  "ip": "192.168.1.103",
  "desktop_harness": {"endpoint": "http://192.168.1.103:8651/mcp"},
  "capabilities": {"observe": true, "screenshot": true, "pointer": true, "keyboard": true}
}
```
