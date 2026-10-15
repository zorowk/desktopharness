#!/usr/bin/env bash
# Internal provisioning transport; password travels only in memory and SSH stdin.
set +x
set -euo pipefail
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [[ "${DESKTOPHARNESS_SUDO_PASSWORD:-0}" != 1 ]]; then
  exec "$script_dir/remote_exec.sh" "$@"
fi
[[ $# == 1 && "$1" == *'bash -se' ]] || {
  echo 'Password sudo transport requires a bash -se provisioning payload' >&2; exit 2;
}
# /dev/tty keeps password entry separate from the provisioning script on stdin.
IFS= read -r -s -p 'Remote sudo password (may be the SSH password): ' sudo_secret </dev/tty
printf '\n' >/dev/tty
[[ -n "$sudo_secret" ]] || { echo 'Empty sudo password' >&2; exit 2; }
trap 'unset sudo_secret' EXIT
bootstrap=$(cat <<'BOOTSTRAP'
set +x
set -euo pipefail
IFS= read -r sudo_secret
trap 'unset sudo_secret' EXIT
sudo() {
  # Authenticate separately so the actual command retains its original stdin.
  if ! printf '%s\n' "$sudo_secret" | command sudo -S -p '' -v; then
    printf 'PROVISION_FAILURE phase=DEPENDENCY reason=sudo-authentication-failed\n' >&2
    return 1
  fi
  command sudo -n "$@"
}
# Fail before executing any provisioning commands if the credential is invalid.
sudo true
source /dev/stdin
BOOTSTRAP
)
# SSH's login shell parses this argument before Bash starts; use POSIX quoting.
quoted_bootstrap="'${bootstrap//\'/\'\\\'\'}'"
remote_command="${1%bash -se}bash -c $quoted_bootstrap"
{ printf '%s\n' "$sudo_secret"; cat; } | "$script_dir/remote_exec.sh" "$remote_command"
