# Changelog

## 0.2.0 — Unreleased

This pre-1.0 release makes the v2.2 protocol and architecture changes
intentional breaking changes. Package SemVer is independent from the runtime
revision (`2.2`) and domain/configuration schema version (`2`).

- Remove task permissions, policy overrides, semantic confirmation, and the
  legacy ActionGate flow from normal execution.
- Make `ProposalValidator` the pre-injection boundary and make recording and
  diagnostics optional.
- Make the task repository the single runtime-state authority and remove the
  transaction recorder.
- Move LangChain, Google, and OmniParser client dependencies to optional
  extras; align clients, deployment verification, and smoke with streamable
  HTTP MCP sessions.

## 0.1.0

Historical baseline before the v2.2 simplification work.
