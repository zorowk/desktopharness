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
    --prelogin) prelogin=1; shift ;;
    *) echo "usage: $0 --host HOST --user USER [--prelogin] [--treeland-source URL] [--treeland-ref REF]" >&2; exit 2 ;;
  esac
done
: "${SSH_HOST:?--host is required}"
: "${SSH_USER:?--user is required}"
: "${CUA_MODEL_API_KEY:?CUA_MODEL_API_KEY must be injected by the controller}"
export SSH_HOST SSH_USER TREELAND_SOURCE_URL TREELAND_REF CUA_MODEL_API_KEY

"${root}/provision_treeland_debug.sh"
if ((prelogin)); then
  exec "${root}/provision_prelogin.sh"
fi
exec "${root}/provision_remote.sh"
