#!/usr/bin/env bash
# Start DesktopHarness against the Treeland login or lock screen.
# This script never reads or changes DDM automatic-login configuration.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
: "$SSH_HOST"
: "$SSH_USER"
: "$CUA_MODEL_API_KEY"

PROJECT_URL="${DESKTOPHARNESS_REPO_URL:-https://github.com/zorowk/desktopharness.git}"
PROJECT_DIR="${DESKTOPHARNESS_DIR:-}"
port="${SSH_PORT:-22}"
local_secret_file="$(mktemp)"
remote_secret_file="/tmp/.desktopharness-qwen-$RANDOM-$RANDOM"
trap 'rm -f -- "$local_secret_file"' EXIT
umask 077
printf 'CUA_MODEL_API_KEY=%s\n' "$CUA_MODEL_API_KEY" > "$local_secret_file"
chmod 600 "$local_secret_file"

scp_args=(-p -o ConnectTimeout=15 -o ConnectionAttempts=1
  -o ServerAliveInterval=10 -o ServerAliveCountMax=2 -o StrictHostKeyChecking=ask
  -P "$port")
if [[ -n "${SSH_IDENTITY_FILE:-}" ]]; then scp_args+=(-i "$SSH_IDENTITY_FILE"); fi
scp "${scp_args[@]}" "$local_secret_file" \
  "$SSH_USER@$SSH_HOST:$remote_secret_file" \
  || { printf 'PROVISION_FAILURE phase=SSH_CONNECT reason=qwen-api-key-transfer-failed\n' >&2; exit 1; }

proxy_assignments=()
for proxy_name in http_proxy https_proxy HTTP_PROXY HTTPS_PROXY; do
  proxy_value="${!proxy_name:-}"
  if [[ -n "$proxy_value" ]]; then
    proxy_assignments+=("$proxy_name=$(printf '%q' "$proxy_value")")
  fi
done
remote_proxy_prefix="${proxy_assignments[*]:-}"

"$SCRIPT_DIR/remote_exec.sh" \
  "$remote_proxy_prefix PROJECT_URL=$(printf '%q' "$PROJECT_URL") PROJECT_DIR=$(printf '%q' "$PROJECT_DIR") REMOTE_SECRET_FILE=$(printf '%q' "$remote_secret_file") TARGET_USER=$(printf '%q' "$SSH_USER") bash -se" \
  <<'REMOTE'
set -euo pipefail
fail() { printf 'PROVISION_FAILURE phase=%s reason=%s\n' "$1" "$2" >&2; exit 1; }

target_user="$TARGET_USER"
secret_file="$REMOTE_SECRET_FILE"
[[ "$secret_file" == /tmp/.desktopharness-qwen-* && -r "$secret_file" ]] \
  || fail DESKTOPHARNESS_START qwen-api-key-unavailable
trap 'rm -f -- "$secret_file"' EXIT
IFS= read -r key < "$secret_file" || fail DESKTOPHARNESS_START qwen-api-key-unavailable
rm -f -- "$secret_file"
[[ "$key" == CUA_MODEL_API_KEY=* ]] || fail DESKTOPHARNESS_START qwen-api-key-unavailable

command -v git >/dev/null || fail DEPENDENCY git-unavailable
id "$target_user" >/dev/null 2>&1 || fail SYSTEM_CHECK target-user-unavailable
target_home="$(getent passwd "$target_user" | cut -d: -f6)"
[[ -n "$target_home" ]] || fail SYSTEM_CHECK target-user-home-not-found
if [[ -z "$PROJECT_DIR" ]]; then PROJECT_DIR="$target_home/desktopharness"; fi

run_as_target() {
  if [[ "$(id -un)" == "$target_user" ]]; then "$@"; else sudo -n -u "$target_user" "$@"; fi
}
if [[ -e "$PROJECT_DIR" && ! -d "$PROJECT_DIR/.git" ]]; then
  fail DESKTOPHARNESS_INSTALL project-path-exists-but-is-not-a-git-checkout
elif [[ ! -e "$PROJECT_DIR" ]]; then
  run_as_target git clone "$PROJECT_URL" "$PROJECT_DIR" || fail DESKTOPHARNESS_INSTALL clone-failed
else
  run_as_target git -C "$PROJECT_DIR" diff --quiet || fail DESKTOPHARNESS_INSTALL checkout-has-local-changes
  run_as_target git -C "$PROJECT_DIR" pull --ff-only || fail DESKTOPHARNESS_INSTALL fast-forward-update-failed
fi

uv_bin="$(run_as_target sh -lc 'command -v uv || printf %s "$HOME/.local/bin/uv"')"
[[ -x "$uv_bin" ]] || fail DEPENDENCY uv-unavailable
run_as_target "$uv_bin" sync --project "$PROJECT_DIR" --frozen || fail DESKTOPHARNESS_INSTALL uv-sync-failed
[[ -f "$PROJECT_DIR/config/mcp-autoui.json" ]] || fail DESKTOPHARNESS_START config-missing
printf '%s\n' "$key" | run_as_target sh -c 'umask 077; cat > "$1"; chmod 600 "$1"' _ "$PROJECT_DIR/.env.local" \
  || fail DESKTOPHARNESS_START qwen-api-key-write-failed

treeland_pid="$(pgrep -o -x treeland || true)"
session_env=""
if [[ -n "$treeland_pid" && -r "/proc/$treeland_pid/environ" ]]; then
  session_env="$(tr '\0' '\n' < "/proc/$treeland_pid/environ" | grep -E '^(WAYLAND_DISPLAY|XDG_RUNTIME_DIR|DBUS_SESSION_BUS_ADDRESS)=' || true)"
fi
if ! grep -q '^WAYLAND_DISPLAY=' <<<"$session_env" || ! grep -q '^XDG_RUNTIME_DIR=' <<<"$session_env"; then
  mapfile -t treeland_sockets < <(find /run/user -maxdepth 3 -user "$target_user" -type s -name treeland.socket -print 2>/dev/null)
  ((${#treeland_sockets[@]} == 1)) || fail DESKTOP_SESSION prelogin-treeland-socket-ambiguous
  session_env="WAYLAND_DISPLAY=treeland.socket
XDG_RUNTIME_DIR=$(dirname "${treeland_sockets[0]}")"
fi
mapfile -t session_env_args <<<"$session_env"
session_env_args+=("XDG_SESSION_TYPE=wayland")

run_as_target env "$session_env_args[@]" timeout 10 treeland-debug --json tree >/dev/null \
  || fail DEPENDENCY treeland-login-screen-unavailable
if ! pgrep -u "$target_user" -f '[t]reeland-autogui-mcp.*--config' >/dev/null; then
  run_as_target env "$session_env_args[@]" AUTOUI_MCP_CONFIG="$PROJECT_DIR/config/mcp-autoui.json" \
    sh -c 'cd "$1"; export XDG_SESSION_TYPE=wayland; nohup ./client_env.sh >"$1/desktopharness-mcp.log" 2>&1 &' _ "$PROJECT_DIR" \
    || fail DESKTOPHARNESS_START prelogin-mcp-start-failed
fi
printf 'PROVISION_OK mode=prelogin target_user=%s project_dir=%s\n' "$target_user" "$PROJECT_DIR"
REMOTE
