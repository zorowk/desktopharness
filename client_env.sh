#!/usr/bin/env bash

#解决部分设备没有~/.Xauthority文件的问题
if [ ! -f ~/.Xauthority ]; then
    touch ~/.Xauthority
    chmod 600 ~/.Xauthority
fi

#关闭签名限制
#sudo dbus-send --print-reply --type=method_call --system --dest=com.deepin.daemon.ACL /org/deepin/security/hierarchical/Control org.deepin.security.hierarchical.Control.SetMode boolean:false

# install base package
sudo apt-get update
sudo apt-get install -y python3.12-venv
sudo apt-get install -y wtype
sudo apt-get install -y wayland-utils
sudo apt-get install -y xdotool
sudo apt-get install -y grim
sudo apt-get install -y wl-clipboard
sudo apt-get install -y curl
sudo apt-get install -y build-essential pkg-config
sudo apt-get install -y cmake ninja-build
sudo apt-get install -y libinput-tools
sudo apt-get install -y gir1.2-atspi-2.0
sudo apt-get install -y python3-dev build-essential
sudo apt-get install -y libcairo2-dev libgirepository-2.0-dev
sudo apt-get install -y scdoc
sudo apt-get install -y wlrctl

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_DIR="${PROJECT_ROOT}/.venv"
TMP_BASE="$(mktemp -d /tmp/treeland-autotests-deps.XXXXXX)"

REPO_4_URL="https://github.com/ReimuNotMoe/ydotool.git"
REPO_4_DIR="${TMP_BASE}/ydotool"

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

echo "[1/7] Optional ydotool install"
install_ydotool

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

if python - <<'PY'
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

UID="$(id -u)"
GID="$(id -g)"
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

if ! pgrep -x ydotoold >/dev/null 2>&1; then
    echo "Starting ydotoold (UID=${UID}, GID=${GID})..." >&2
    sudo ydotoold "${touch_flag[@]}" -p "${XDG_RUNTIME_DIR}/.ydotool_socket" -o "${UID}:${GID}" >/dev/null 2>&1 &
else
    echo "ydotoold already running; skipping start." >&2
fi

# 启动treeland autogui mcp
export SSE_HOST="0.0.0.0"
export SSE_PORT=8000
export CUA_BACKEND_URL="${CUA_BACKEND_URL:-http://127.0.0.1:8326}"
export CUA_TLS_VERIFY="${CUA_TLS_VERIFY:-1}"
export GUI_OMNIPARSER_ENABLED="${GUI_OMNIPARSER_ENABLED:-0}"
uv run treeland-autogui-mcp

cat <<EOF

Setup completed.
Virtual environment: ${VENV_DIR}
Temporary source path used: ${TMP_BASE}

Run:
  source .venv/bin/activate
  python tests/desktop_demo.py

Set KEEP_TMP=1 before running this tests if you want to keep cloned sources in /tmp.
EOF
