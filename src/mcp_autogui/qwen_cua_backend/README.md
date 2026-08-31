# Embedded Qwen-CUA backend

This package is the in-process Qwen-CUA inference and session layer for
`treeland-autoui-mcp`. The runtime does not import from or start `~/gui-mcp`.

Migration source: `/home/uos/gui-mcp/cua_mcp_backend`, inspected on 2026-08-31.
The source checkout did not expose usable Git commit metadata or a license file,
so this package records the path and date rather than inventing a revision. The
implementation was reduced to the Qwen S2 path and adapted to this project's
proposal/feedback state model.

Migrated behavior:

- Qwen S2 `computer_use` prompt and OpenAI-compatible chat request;
- image resize aligned to the model factor;
- normalized or processed-image coordinate projection;
- conversion of structured model actions to pyautogui-shaped strings;
- bounded per-session screenshot/response history.

Not migrated:

- FastAPI/uvicorn transport;
- non-CUA agents and their dependencies;
- trajectory images, historical logs, caches and native client utilities;
- rollout voting (kept as a later experiment in `play.md`).

Default configuration uses `CUA_BACKEND_MODE=embedded`. Model configuration is
read from `CUA_MODEL_BASE_URL`, `CUA_MODEL`, `CUA_MODEL_API_KEY`,
`CUA_MODEL_TIMEOUT`, `CUA_MODEL_TLS_VERIFY`, and `CUA_MODEL_TRUST_ENV`.
