#!/usr/bin/env bash

# 加载本机私密配置；文件不存在时继续使用脚本原有默认值。
CLIENT_ENV_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CLIENT_ENV_FILE="${CLIENT_ENV_FILE:-${CLIENT_ENV_DIR}/.env.local}"
if [ -f "${CLIENT_ENV_FILE}" ]; then
    set -a
    # shellcheck disable=SC1090
    source "${CLIENT_ENV_FILE}"
    set +a
fi

MOUSE_SPEED_ACTION="unchanged"
for client_env_arg in "$@"; do
    case "${client_env_arg}" in
        --flat-speed)
            if [[ "${MOUSE_SPEED_ACTION}" != "unchanged" ]]; then
                echo "Only one mouse speed action may be specified." >&2
                exit 2
            fi
            MOUSE_SPEED_ACTION="flat"
            ;;
        --restore-speed)
            if [[ "${MOUSE_SPEED_ACTION}" != "unchanged" ]]; then
                echo "Only one mouse speed action may be specified." >&2
                exit 2
            fi
            MOUSE_SPEED_ACTION="restore"
            ;;
        *)
            echo "Unknown argument: ${client_env_arg}" >&2
            echo "Usage: $0 [--flat-speed | --restore-speed]" >&2
            exit 2
            ;;
    esac
done

#解决部分设备没有~/.Xauthority文件的问题
if [ ! -f ~/.Xauthority ]; then
    touch ~/.Xauthority
    chmod 600 ~/.Xauthority
fi

#关闭签名限制
#sudo dbus-send --print-reply --type=method_call --system --dest=com.deepin.daemon.ACL /org/deepin/security/hierarchical/Control org.deepin.security.hierarchical.Control.SetMode boolean:false

# install missing base packages
apt_packages=(
    python3.12-venv
    wtype
    wayland-utils
    xdotool
    grim
    wl-clipboard
    curl
    build-essential
    pkg-config
    cmake
    ninja-build
    libinput-tools
    gir1.2-atspi-2.0
    python3-pyatspi
    python3-gi
    python3-dev
    libcairo2-dev
    libgirepository-2.0-dev
    scdoc
    wlrctl
)
missing_apt_packages=()
for package in "${apt_packages[@]}"; do
    if ! dpkg-query -W -f='${Status}' "${package}" 2>/dev/null | grep -q '^install ok installed$'; then
        missing_apt_packages+=("${package}")
    fi
done

if ((${#missing_apt_packages[@]} > 0)); then
    echo "Installing missing apt packages: ${missing_apt_packages[*]}"
    sudo apt-get update
    sudo apt-get install -y "${missing_apt_packages[@]}"
else
    echo "All required apt packages are already installed; skipping apt."
fi

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="${PROJECT_ROOT}/.venv"
TMP_BASE="$(mktemp -d /tmp/treeland-autotests-deps.XXXXXX)"
YDOTOOL_UDEV_RULE_SOURCE="${PROJECT_ROOT}/udev/99-ydotoold-mouse.rules"
YDOTOOL_UDEV_RULE_TARGET="/etc/udev/rules.d/99-ydotoold-mouse.rules"

REPO_4_URL="https://github.com/ReimuNotMoe/ydotool.git"
REPO_4_DIR="${TMP_BASE}/ydotool"
MOUSE_DCONFIG_ARGS=()

# uv index mirrors
export UV_INDEX_URL="https://pypi.tuna.tsinghua.edu.cn/simple/"
export UV_EXTRA_INDEX_URL="https://mirrors.aliyun.com/pypi/simple/"

cleanup() {
  if [[ "${KEEP_TMP:-0}" != "1" && -d "${TMP_BASE}" ]]; then
    rm -rf "${TMP_BASE}"
  fi
}
trap cleanup EXIT

install_ydotool() {
  if command -v ydotool >/dev/null 2>&1; then
    echo "ydotool is already installed; skipping."
    return 0
  fi

  echo "Installing ydotool from source: ${REPO_4_URL}"
  git clone --depth 1 "${REPO_4_URL}" "${REPO_4_DIR}"
  (
    cd "${REPO_4_DIR}"
    cmake -Bbuild -GNinja \
      -DCMAKE_INSTALL_PREFIX=/usr
    sudo ninja -C build/ install
  )
}

install_ydotool_udev_rule() {
  if ! command -v udevadm >/dev/null 2>&1; then
    echo "udevadm is required to classify the ydotoold pointer device." >&2
    return 1
  fi

  if [[ ! -f "${YDOTOOL_UDEV_RULE_SOURCE}" ]]; then
    echo "Missing ydotoold udev rule: ${YDOTOOL_UDEV_RULE_SOURCE}" >&2
    return 1
  fi

  if [[ -f "${YDOTOOL_UDEV_RULE_TARGET}" ]] \
      && cmp -s "${YDOTOOL_UDEV_RULE_SOURCE}" "${YDOTOOL_UDEV_RULE_TARGET}"; then
    echo "ydotoold mouse udev rule is already installed; skipping." >&2
    return 0
  fi

  echo "Installing ydotoold mouse udev rule..." >&2
  sudo install -m 0644 "${YDOTOOL_UDEV_RULE_SOURCE}" "${YDOTOOL_UDEV_RULE_TARGET}"
  sudo udevadm control --reload-rules
}

echo "[1/7] Optional ydotool install"
install_ydotool
install_ydotool_udev_rule || exit 1

echo "[2/7] Install uv"

export PATH="$HOME/.cargo/bin:$HOME/.local/bin:$PATH"
if ! command -v uv >/dev/null 2>&1; then
    curl -LsSf https://astral.sh/uv/install.sh | sh
    command -v uv >/dev/null 2>&1 || {
        echo "uv install failed. Ensure ~/.cargo/bin or ~/.local/bin is in PATH." >&2
        exit 1
    }
fi

echo "[3/7] Install python dependencies via uv"
uv sync

if /usr/bin/python3 - <<'PY'
import pyatspi
print(pyatspi.__name__)
PY
then
  echo "pyatspi import check: OK"
else
  cat <<'EOF'
Warning: pyatspi is not available in the current environment.
dogtail may require system-level AT-SPI packages from your distro.
Example (Debian/Ubuntu):
  sudo apt-get install -y python3-pyatspi python3-gi gir1.2-atspi-2.0
EOF
fi

# 启动测试机ydotoold服务
if [[ "${XDG_SESSION_TYPE:-}" == "tty" && -z "${WAYLAND_DISPLAY:-}" ]]; then
    echo "Variable not set, ready to set."
    export DISPLAY=:0
    export WAYLAND_DISPLAY="${XDG_RUNTIME_DIR}/treeland.socket"
    export XDG_SESSION_TYPE=wayland
    export QT_WAYLAND_SHELL_INTEGRATION="xdg-shell;wl-shell;ivi-shell;qt-shell;"
    export XDG_SESSION_DESKTOP=Deepin
    export GDMSESSION=Wayland
    export YDOTOOL_SOCKET="${XDG_RUNTIME_DIR}/.ydotool_socket"
fi

echo "Wayland environment variables have been set." >&2

USER_UID="$(id -u)"
USER_GID="$(id -g)"
USER_NAME="$(id -un)"
TREELAND_DCONFIG_USER="${TREELAND_DCONFIG_USER:-dde}"
MOUSE_DCONFIG_ARGS=(
    -a org.deepin.dde.treeland
    -r org.deepin.dde.treeland.user.seat
    -s "/${USER_NAME}"
)

run_treeland_dconfig() {
    sudo -u "${TREELAND_DCONFIG_USER}" dde-dconfig "$@"
}

configure_flat_mouse() {
    if ! command -v dde-dconfig >/dev/null 2>&1; then
        echo "dde-dconfig is required to set the Treeland mouse acceleration profile." >&2
        return 1
    fi

    echo "Setting Treeland mouse acceleration to Flat with speed 0 (DConfig user: ${TREELAND_DCONFIG_USER})..." >&2
    if ! run_treeland_dconfig set "${MOUSE_DCONFIG_ARGS[@]}" \
        -k mouseAccelerationProfile -v 1 >/dev/null \
        || ! run_treeland_dconfig set "${MOUSE_DCONFIG_ARGS[@]}" \
        -k mouseAccelSpeed -v 0 >/dev/null; then
        echo "Failed to set the temporary Treeland mouse acceleration configuration." >&2
        return 1
    fi
}

restore_default_mouse_speed() {
    if ! command -v dde-dconfig >/dev/null 2>&1; then
        echo "dde-dconfig is required to restore the Treeland mouse acceleration profile." >&2
        return 1
    fi

    echo "Restoring the default Treeland mouse acceleration configuration (DConfig user: ${TREELAND_DCONFIG_USER})..." >&2
    if ! run_treeland_dconfig reset "${MOUSE_DCONFIG_ARGS[@]}" \
        -k mouseAccelerationProfile >/dev/null \
        || ! run_treeland_dconfig reset "${MOUSE_DCONFIG_ARGS[@]}" \
        -k mouseAccelSpeed >/dev/null; then
        echo "Failed to restore the default Treeland mouse acceleration configuration." >&2
        return 1
    fi
}

touch_flag=()
if command -v libinput >/dev/null 2>&1; then
    echo "Checking touchscreen via libinput..." >&2
    if sudo libinput list-devices 2>/dev/null | grep -qi "Touchscreen"; then
        echo "Touchscreen detected; enabling -T for ydotoold." >&2
        touch_flag=(-T)
    else
        echo "No touchscreen detected via libinput." >&2
    fi
else
    echo "libinput not found; skipping touchscreen detection." >&2
fi

case "${MOUSE_SPEED_ACTION}" in
    flat)
        configure_flat_mouse || exit 1
        ;;
    restore)
        restore_default_mouse_speed || exit 1
        ;;
esac

if ! pgrep -x ydotoold >/dev/null 2>&1; then
    echo "Starting ydotoold (UID=${USER_UID}, GID=${USER_GID})..." >&2
    sudo ydotoold "${touch_flag[@]}" -p "${XDG_RUNTIME_DIR}/.ydotool_socket" -o "${USER_UID}:${USER_GID}" >/dev/null 2>&1 &
else
    echo "ydotoold already running; skipping start." >&2
fi

# 启动treeland autogui mcp
export SSE_HOST="0.0.0.0"
export SSE_PORT=8000
export MCP_TRANSPORT="${MCP_TRANSPORT:-streamable-http}"
export CUA_BACKEND_MODE="${CUA_BACKEND_MODE:-embedded}"
export CUA_MODEL_BASE_URL="${CUA_MODEL_BASE_URL:-http://127.0.0.1:8000/v1}"
export CUA_MODEL="${CUA_MODEL:-qwen3_rl}"
export CUA_MODEL_TLS_VERIFY="${CUA_MODEL_TLS_VERIFY:-1}"
export GUI_OMNIPARSER_ENABLED="${GUI_OMNIPARSER_ENABLED:-0}"
uv run treeland-autogui-mcp || exit $?

cat <<EOF

Setup completed.
Virtual environment: ${VENV_DIR}
Temporary source path used: ${TMP_BASE}

Run:
  source .venv/bin/activate
  python tests/desktop_demo.py

Set KEEP_TMP=1 before running this tests if you want to keep cloned sources in /tmp.
EOF
