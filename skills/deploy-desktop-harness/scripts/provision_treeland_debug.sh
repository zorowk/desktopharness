#!/usr/bin/env bash
# Internal helper: build the exact user-selected Debug Treeland revision.
set -euo pipefail
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
: "${SSH_HOST:?}"
: "${SSH_USER:?}"
: "${TREELAND_SOURCE_URL:?Specify the repository under test}"
: "${TREELAND_REF:?Specify the ref under test}"
: "${TREELAND_APT_UPGRADE:=0}"
[[ "$TREELAND_APT_UPGRADE" == 0 || "$TREELAND_APT_UPGRADE" == 1 ]] || {
  echo 'TREELAND_APT_UPGRADE must be 0 or 1' >&2; exit 2;
}
commit="$("${script_dir}/resolve_treeland_ref.sh")"
[[ "$commit" =~ ^[0-9a-f]{40,64}$ ]] || { echo 'Invalid resolved Treeland commit' >&2; exit 1; }
printf 'TREELAND_TARGET source=%s ref=%s commit=%s\n' \
  "$TREELAND_SOURCE_URL" "$TREELAND_REF" "$commit"
remote_command="TREELAND_SOURCE_URL=$(printf '%q' "$TREELAND_SOURCE_URL") "
remote_command+="TREELAND_REF=$(printf '%q' "$TREELAND_REF") TREELAND_COMMIT=$commit "
remote_command+="TREELAND_APT_UPGRADE=$TREELAND_APT_UPGRADE bash -se"
"${script_dir}/remote_sudo_exec.sh" "$remote_command" <<'REMOTE'
set -euo pipefail
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
sudo apt-get install -y build-essential cmake ninja-build pkg-config
# Use this checkout's debian/control, including dependencies introduced by a fork.
sudo apt-get build-dep -y "$src"
phase=TREELAND_BUILD
cmake -S "$src" -B "$src/build" -GNinja -DCMAKE_BUILD_TYPE=Debug \
  -DBUILD_TREELAND_DEBUG=ON -DTREELAND_DEBUG_SOURCE=ON \
  -DWITH_SUBMODULE_WAYLIB=ON -DCMAKE_INSTALL_PREFIX=/usr 2>&1 | tee "$src/configure.log"
cmake --build "$src/build" --parallel 2>&1 | tee "$src/build.log"
phase=TREELAND_INSTALL
sudo cmake --install "$src/build"
if systemctl is-active --quiet treeland.service; then
  sudo systemctl restart treeland.service
else
  systemctl --user restart treeland.service
fi
printf 'TREELAND_INSTALLED source=%s ref=%s commit=%s build_dir=%s\n' \
  "$TREELAND_SOURCE_URL" "$TREELAND_REF" "$TREELAND_COMMIT" "$src"
REMOTE
