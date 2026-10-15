#!/usr/bin/env bash
# Resolve a user-selected Treeland source/ref to one immutable commit.
set -euo pipefail

: "${TREELAND_SOURCE_URL:?Specify the repository under test}"
: "${TREELAND_REF:?Specify the ref under test}"

work_dir="$(mktemp -d)"
trap 'rm -rf -- "$work_dir"' EXIT
git clone --quiet --no-checkout "$TREELAND_SOURCE_URL" "$work_dir/source"
git -C "$work_dir/source" fetch --quiet --tags origin "$TREELAND_REF"
commit="$(git -C "$work_dir/source" rev-parse --verify FETCH_HEAD^{commit})"
# stdout is a machine-readable SHA only; callers must not evaluate repository input.
printf '%s\n' "$commit"
