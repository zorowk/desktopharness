#!/usr/bin/env bash
# Deploy or repair DesktopHarness on the user-supplied remote machine.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
: "${SSH_HOST:?Set SSH_HOST.}"
: "${SSH_USER:?Set SSH_USER.}"
PROJECT_URL="${DESKTOPHARNESS_REPO_URL:-https://github.com/zorowk/desktopharness.git}"
PROJECT_DIR="${DESKTOPHARNESS_DIR:-}"
: "${CUA_MODEL_API_KEY:?Set CUA_MODEL_API_KEY in the controlling AI environment.}"

port="${SSH_PORT:-22}"
identity_args=()
if [[ -n "${SSH_IDENTITY_FILE:-}" ]]; then
  identity_args=(-i "${SSH_IDENTITY_FILE}")
fi
local_secret_file="$(mktemp)"
remote_secret_file="/tmp/.desktopharness-qwen-${RANDOM}-${RANDOM}"
trap 'rm -f -- "$local_secret_file"' EXIT
umask 077
printf 'CUA_MODEL_API_KEY=%s\n' "$CUA_MODEL_API_KEY" > "$local_secret_file"
chmod 600 "$local_secret_file"
scp -p \
  -o ConnectTimeout=15 \
  -o ConnectionAttempts=1 \
  -o ServerAliveInterval=10 \
  -o ServerAliveCountMax=2 \
  -o StrictHostKeyChecking=ask \
  -P "$port" \
  "${identity_args[@]}" \
  "$local_secret_file" "${SSH_USER}@${SSH_HOST}:${remote_secret_file}" \
  || { printf 'PROVISION_FAILURE phase=SSH_CONNECT reason=qwen-api-key-transfer-failed\n' >&2; exit 1; }

"${SCRIPT_DIR}/remote_exec.sh" \
  "PROJECT_URL=$(printf '%q' "$PROJECT_URL") PROJECT_DIR=$(printf '%q' "$PROJECT_DIR") REMOTE_SECRET_FILE=$(printf '%q' "$remote_secret_file") bash -se" \
  <<'REMOTE'
set -euo pipefail

fail() { printf 'PROVISION_FAILURE phase=%s reason=%s\n' "$1" "$2" >&2; exit 1; }
secret_file="${REMOTE_SECRET_FILE:-}"
[[ "$secret_file" == /tmp/.desktopharness-qwen-* && -r "$secret_file" ]] \
  || fail DESKTOPHARNESS_START qwen-api-key-unavailable
trap 'rm -f -- "$secret_file"' EXIT
IFS= read -r CUA_MODEL_API_KEY < "$secret_file" || fail DESKTOPHARNESS_START qwen-api-key-unavailable
CUA_MODEL_API_KEY="${CUA_MODEL_API_KEY#CUA_MODEL_API_KEY=}"
[[ -n "$CUA_MODEL_API_KEY" ]] || fail DESKTOPHARNESS_START qwen-api-key-unavailable
rm -f -- "$secret_file"
command -v loginctl >/dev/null || fail SYSTEM_CHECK loginctl-unavailable
command -v git >/dev/null || fail DEPENDENCY git-unavailable
command -v ss >/dev/null || fail SYSTEM_CHECK ss-unavailable

session=""
session_type=""
while read -r id _; do
  [[ -n "$id" ]] || continue
  name="$(loginctl show-session "$id" -p Name --value 2>/dev/null || true)"
  type="$(loginctl show-session "$id" -p Type --value 2>/dev/null || true)"
  state="$(loginctl show-session "$id" -p State --value 2>/dev/null || true)"
  remote="$(loginctl show-session "$id" -p Remote --value 2>/dev/null || true)"
  leader_value="$(loginctl show-session "$id" -p Leader --value 2>/dev/null || true)"
  if [[ ( "$type" == wayland || "$type" == x11 ) && "$state" == active \
    && "$remote" == no && -n "$leader_value" ]]; then
    session="$id"
    session_type="$type"
    break
  fi
done < <(loginctl list-sessions --no-legend)
[[ -n "$session" ]] || fail DESKTOP_SESSION active-local-graphical-session-not-found

desktop_user="$(loginctl show-session "$session" -p Name --value)"
leader="$(loginctl show-session "$session" -p Leader --value)"
[[ -n "$desktop_user" && -n "$leader" ]] || fail DESKTOP_SESSION session-metadata-unreadable

run_as_desktop() {
  if [[ "$(id -un)" == "$desktop_user" ]]; then
    "$@"
  else
    command -v sudo >/dev/null || fail DESKTOP_SESSION desktop-user-switch-requires-sudo
    sudo -n -u "$desktop_user" "$@"
  fi
}

if [[ -z "$PROJECT_DIR" ]]; then
  desktop_home="$(getent passwd "$desktop_user" | cut -d: -f6)"
  [[ -n "$desktop_home" ]] || fail SYSTEM_CHECK desktop-user-home-not-found
  PROJECT_DIR="$desktop_home/desktopharness"
fi

environment_pid="$leader"
if [[ ! -r "/proc/$environment_pid/environ" ]]; then
  environment_pid="$(run_as_desktop sh -c '
    for proc in /proc/[0-9]*; do
      [ -r "$proc/environ" ] || continue
      tr "\0" "\n" < "$proc/environ" 2>/dev/null | grep -q "^WAYLAND_DISPLAY=" || continue
      basename "$proc"; exit 0
    done
  ' || true)"
fi
[[ -n "$environment_pid" && -r "/proc/$environment_pid/environ" ]] \
  || fail DESKTOP_SESSION session-environment-unreadable
session_env="$(
  tr '\0' '\n' < "/proc/$environment_pid/environ" \
    | grep -E '^(WAYLAND_DISPLAY|DISPLAY|XAUTHORITY|XDG_RUNTIME_DIR|DBUS_SESSION_BUS_ADDRESS|XDG_SESSION_TYPE)=' \
    || true
)"
mapfile -t session_env_args <<<"$session_env"
session_env_args+=("XDG_SESSION_TYPE=$session_type")
if [[ "$session_type" == wayland ]]; then
  grep -q '^WAYLAND_DISPLAY=' <<<"$session_env" || fail DESKTOP_SESSION WAYLAND_DISPLAY-missing
else
  grep -q '^DISPLAY=' <<<"$session_env" || fail DESKTOP_SESSION DISPLAY-missing
fi
grep -q '^XDG_RUNTIME_DIR=' <<<"$session_env" || fail DESKTOP_SESSION XDG_RUNTIME_DIR-missing
grep -q '^DBUS_SESSION_BUS_ADDRESS=' <<<"$session_env" \
  || fail DESKTOP_SESSION DBUS_SESSION_BUS_ADDRESS-missing

if [[ -e "$PROJECT_DIR" && ! -d "$PROJECT_DIR/.git" ]]; then
  fail DESKTOPHARNESS_INSTALL project-path-exists-but-is-not-a-git-checkout
elif [[ ! -e "$PROJECT_DIR" ]]; then
  run_as_desktop git clone "$PROJECT_URL" "$PROJECT_DIR" \
    || fail DESKTOPHARNESS_INSTALL clone-failed
else
  run_as_desktop git -C "$PROJECT_DIR" diff --quiet \
    || fail DESKTOPHARNESS_INSTALL checkout-has-local-changes
  run_as_desktop git -C "$PROJECT_DIR" pull --ff-only \
    || fail DESKTOPHARNESS_INSTALL fast-forward-update-failed
fi

uv_bin="$(run_as_desktop sh -lc 'command -v uv || true')"
if [[ -z "$uv_bin" ]]; then
  command -v curl >/dev/null || fail DEPENDENCY uv-and-curl-unavailable
  run_as_desktop sh -lc 'curl -LsSf https://astral.sh/uv/install.sh | sh' \
    || fail DEPENDENCY uv-install-failed
  uv_bin="$(run_as_desktop sh -lc 'command -v uv || printf %s "$HOME/.local/bin/uv"')"
fi
[[ -x "$uv_bin" ]] || fail DEPENDENCY uv-unavailable-after-install
run_as_desktop "$uv_bin" sync --project "$PROJECT_DIR" --frozen \
  || fail DESKTOPHARNESS_INSTALL uv-sync-failed

config="$PROJECT_DIR/config/mcp-autoui.json"
[[ -f "$config" ]] || fail DESKTOPHARNESS_START config-missing
env_file="$PROJECT_DIR/.env.local"
printf '%s\n' "$CUA_MODEL_API_KEY" | run_as_desktop sh -c \
  'umask 077; IFS= read -r key; printf "CUA_MODEL_API_KEY=%s\\n" "$key" > "$1"; chmod 600 "$1"' _ "$env_file" \
  || fail DESKTOPHARNESS_START qwen-api-key-write-failed
endpoint_port="$(
  sed -n 's/.*"port"[[:space:]]*:[[:space:]]*\([0-9][0-9]*\).*/\1/p' "$config" \
    | head -n 1
)"
backend="$(sed -n 's/.*"kind"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$config" | head -n 1)"
[[ -n "$endpoint_port" && -n "$backend" ]] || fail DEPENDENCY invalid-desktopharness-config
if [[ "$backend" == treeland-* ]]; then
  [[ "$session_type" == wayland ]] || fail DEPENDENCY treeland-backend-requires-wayland
  run_as_desktop env "${session_env_args[@]}" timeout 10 treeland-debug --json tree >/dev/null \
    || fail DEPENDENCY treeland-backend-unavailable-in-desktop-session
fi

if ! pgrep -u "$desktop_user" -f '[t]reeland-autogui-mcp.*--config' >/dev/null; then
  run_as_desktop env "${session_env_args[@]}" AUTOUI_MCP_CONFIG="$config" sh -c \
    'IFS= read -r line < "$1" || exit 1; CUA_MODEL_API_KEY=${line#CUA_MODEL_API_KEY=}; [ "$CUA_MODEL_API_KEY" != "$line" ] || exit 1; export CUA_MODEL_API_KEY; cd "$2"; nohup ./client_env.sh >"$3" 2>&1 &' _ \
    "$env_file" "$PROJECT_DIR" \
    "$PROJECT_DIR/desktopharness-mcp.log"
fi

sleep 2
ss -ltn "sport = :$endpoint_port" | grep -q LISTEN \
  || fail DESKTOPHARNESS_START mcp-port-not-listening
printf 'PROVISION_OK hostname=%s desktop_user=%s session_type=%s backend=%s ' \
  "$(hostname)" "$desktop_user" "$session_type" "$backend"
printf 'endpoint=http://127.0.0.1:%s/mcp\n' "$endpoint_port"
REMOTE
