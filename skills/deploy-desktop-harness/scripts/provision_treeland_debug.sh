#!/usr/bin/env bash
# Internal helper: build the exact user-selected Debug Treeland revision.
set -euo pipefail
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
: "${SSH_HOST:?}"
: "${SSH_USER:?}"
: "${TREELAND_SOURCE_URL:?Specify the repository under test}"
: "${TREELAND_REF:?Specify the ref under test}"
: "${TREELAND_APT_UPGRADE:=0}"
: "${TREELAND_SOURCE_DEPS:=0}"
: "${DDM_REF:=HEAD}"
: "${TREELAND_PROTOCOLS_REF:=HEAD}"
: "${TREELAND_BUILD_JOBS:=2}"
[[ "$TREELAND_APT_UPGRADE" == 0 || "$TREELAND_APT_UPGRADE" == 1 ]] || {
  echo 'TREELAND_APT_UPGRADE must be 0 or 1' >&2; exit 2;
}
[[ "$TREELAND_SOURCE_DEPS" == 0 || "$TREELAND_SOURCE_DEPS" == 1 ]] || exit 2
[[ "$TREELAND_BUILD_JOBS" =~ ^[1-9][0-9]*$ ]] || exit 2
commit="$("${script_dir}/resolve_treeland_ref.sh")"
[[ "$commit" =~ ^[0-9a-f]{40,64}$ ]] || { echo 'Invalid resolved Treeland commit' >&2; exit 1; }
printf 'TREELAND_TARGET source=%s ref=%s commit=%s\n' \
  "$TREELAND_SOURCE_URL" "$TREELAND_REF" "$commit"
remote_command=""
for proxy_name in http_proxy https_proxy HTTP_PROXY HTTPS_PROXY; do
  proxy_value="${!proxy_name:-}"
  if [[ -n "$proxy_value" ]]; then
    remote_command+="$proxy_name=$(printf '%q' "$proxy_value") "
  fi
done
remote_command+="TREELAND_SOURCE_URL=$(printf '%q' "$TREELAND_SOURCE_URL") "
remote_command+="TREELAND_REF=$(printf '%q' "$TREELAND_REF") TREELAND_COMMIT=$commit "
remote_command+="TREELAND_APT_UPGRADE=$TREELAND_APT_UPGRADE "
remote_command+="TREELAND_SOURCE_DEPS=$TREELAND_SOURCE_DEPS "
remote_command+="DDM_REF=$(printf '%q' "$DDM_REF") "
remote_command+="TREELAND_PROTOCOLS_REF=$(printf '%q' "$TREELAND_PROTOCOLS_REF") "
remote_command+="TREELAND_BUILD_JOBS=$TREELAND_BUILD_JOBS bash -se"
{
cat "${script_dir}/treeland_source_deps.sh"
cat <<'REMOTE'
set -Eeuo pipefail
export LC_ALL=C
phase=TREELAND_SOURCE
trap 'printf "PROVISION_FAILURE phase=%s reason=command-failed line=%s\n" \
  "$phase" "$LINENO" >&2' ERR
# Keep each source tree and build log for diagnosing dependency or compiler failures.
src="$(mktemp -d /var/tmp/treeland-debug-build.XXXXXXXX)"
git -C "$src" init --quiet
git -C "$src" remote add origin "$TREELAND_SOURCE_URL"
git -C "$src" fetch --no-tags origin "$TREELAND_COMMIT"
git -C "$src" checkout --detach "$TREELAND_COMMIT"
[[ "$(git -C "$src" rev-parse HEAD)" == "$TREELAND_COMMIT" ]]
git -C "$src" submodule update --init --recursive
phase=DEPENDENCY
sudo apt-get update
if [[ "$TREELAND_APT_UPGRADE" == 1 ]]; then
  sudo apt-get upgrade -y
fi
sudo apt-get install -y build-essential cmake ninja-build pkg-config python3
# Use this checkout's debian/control, including dependencies introduced by a fork.
source_dependencies_built=0
if ! sudo apt-get build-dep -y "$src" 2>&1 | tee "$src/dependencies.log"; then
  if [[ "$TREELAND_SOURCE_DEPS" != 1 ]] \
    || ! grep -Eq 'Depends: (libddm(-dev)?|ddm|treeland-protocols)([ :]|$)' \
      "$src/dependencies.log"; then
    printf 'PROVISION_FAILURE phase=DEPENDENCY reason=apt-build-dep-failed\n' >&2
    exit 1
  fi
  # Resolve all other Debian dependencies without pretending source installs
  # changed dpkg's recorded DDM/protocol package versions.
  install_remaining_build_deps "$src"
  build_treeland_source_dependencies
fi
phase=TREELAND_BUILD
configure_treeland() {
  cmake --fresh -S "$src" -B "$src/build" -GNinja -DCMAKE_BUILD_TYPE=Debug \
    -DBUILD_TREELAND_DEBUG=ON -DTREELAND_DEBUG_SOURCE=ON \
    -DBUILD_EXAMPLES=OFF -DBUILD_TESTS=OFF -DBUILD_TREELAND_PROTOCOL_TESTS=OFF \
    -DWITH_SUBMODULE_WAYLIB=ON -DCMAKE_INSTALL_PREFIX=/usr 2>&1 | tee "$src/configure.log"
}
if ! configure_treeland; then
  if [[ "$TREELAND_SOURCE_DEPS" != 1 || "$source_dependencies_built" == 1 ]] \
    || ! grep -A8 -E 'CMake Error|Could NOT find|Could not find|Found unsuitable' \
      "$src/configure.log" | grep -E '(DDM|TreelandProtocols|treeland-protocols)' >/dev/null; then
    printf 'PROVISION_FAILURE phase=TREELAND_BUILD reason=cmake-configure-failed\n' >&2
    exit 1
  fi
  phase=DEPENDENCY
  build_treeland_source_dependencies
  phase=TREELAND_BUILD
  configure_treeland
fi
cmake --build "$src/build" --parallel "$TREELAND_BUILD_JOBS" 2>&1 | tee "$src/build.log"
phase=TREELAND_INSTALL
sudo cmake --install "$src/build" 2>&1 | tee "$src/install.log"
sudo ldconfig
sudo systemctl daemon-reload
printf 'TREELAND_INSTALLED source=%s ref=%s commit=%s build_dir=%s\n' \
  "$TREELAND_SOURCE_URL" "$TREELAND_REF" "$TREELAND_COMMIT" "$src"
if systemctl is-active --quiet treeland.service; then
  sudo systemctl restart ddm.service treeland.service
  systemctl is-active --quiet ddm.service treeland.service
else
  systemctl --user restart treeland.service
  sudo systemctl restart ddm.service
  systemctl --user is-active --quiet treeland.service
  systemctl is-active --quiet ddm.service
fi
printf 'TREELAND_RESTARTED services=ddm,treeland\n'
REMOTE
} | "${script_dir}/remote_sudo_exec.sh" "$remote_command"
