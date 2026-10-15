#!/usr/bin/env bash
# Sole public entry point for DesktopHarness deployment.
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
prelogin=0
while (($#)); do
  case "$1" in
    --host) SSH_HOST="$2"; shift 2 ;;
    --user) SSH_USER="$2"; shift 2 ;;
    --treeland-source) TREELAND_SOURCE_URL="$2"; shift 2 ;;
    --treeland-ref) TREELAND_REF="$2"; shift 2 ;;
    --sudo-password) DESKTOPHARNESS_SUDO_PASSWORD=1; shift ;;
    --apt-upgrade) TREELAND_APT_UPGRADE=1; shift ;;
    --source-deps) TREELAND_SOURCE_DEPS=1; shift ;;
    --prelogin) prelogin=1; shift ;;
    *)
      printf 'usage: %s --host HOST --user USER [--prelogin] ' "$0" >&2
      printf '%s\n' '--treeland-source URL --treeland-ref REF [--apt-upgrade] [--sudo-password]' >&2
      printf '%s\n' '  [--source-deps]' >&2
      exit 2 ;;
  esac
done
: "${SSH_HOST:?--host is required}"
: "${SSH_USER:?--user is required}"
: "${TREELAND_SOURCE_URL:?--treeland-source is required (the repository under test)}"
: "${TREELAND_REF:?--treeland-ref is required (the branch, tag, or commit under test)}"
: "${TREELAND_APT_UPGRADE:=0}"
: "${TREELAND_SOURCE_DEPS:=0}"
: "${CUA_MODEL_API_KEY:?CUA_MODEL_API_KEY must be injected by the controller}"
export DESKTOPHARNESS_SUDO_PASSWORD
export SSH_HOST SSH_USER TREELAND_SOURCE_URL TREELAND_REF TREELAND_APT_UPGRADE CUA_MODEL_API_KEY
export TREELAND_SOURCE_DEPS

"${root}/provision_treeland_debug.sh"
if ((prelogin)); then
  exec "${root}/provision_prelogin.sh"
fi
exec "${root}/provision_remote.sh"
