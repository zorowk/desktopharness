#!/usr/bin/env bash
# Publish one sanitized DesktopHarness report to its separate report repository.
set -euo pipefail

skill_dir="$(cd "$(dirname "$BASH_SOURCE")/.." && pwd)"
config_file="$skill_dir/reporting.local.env"
publish=""
report_file=""
machine=""

usage() {
  echo "usage: $0 --report /absolute/path/report.md --machine MACHINE [--publish|--dry-run]" >&2
}

while (($#)); do
  case "$1" in
    --report) report_file="$2"; shift 2 ;;
    --machine) machine="$2"; shift 2 ;;
    --publish) publish=true; shift ;;
    --dry-run) publish=false; shift ;;
    *) usage; exit 2 ;;
  esac
done

[[ -f "$config_file" ]] || {
  echo "missing local report configuration: $config_file" >&2
  exit 2
}
[[ -n "$report_file" && -f "$report_file" ]] || {
  echo "--report must name an existing regular file" >&2
  exit 2
}
[[ "$machine" =~ ^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$ ]] || {
  echo "--machine must contain only letters, digits, dot, underscore, or hyphen" >&2
  exit 2
}

# This is intentionally a user-local, ignored shell configuration.
set -a
# shellcheck disable=SC1090
source "$config_file"
set +a

[[ -n "$REPORT_REPOSITORY" ]] || {
  echo "Set REPORT_REPOSITORY in reporting.local.env" >&2
  exit 2
}
REPORT_BRANCH="${REPORT_BRANCH:-main}"
REPORT_AUTHOR_NAME="${REPORT_AUTHOR_NAME:-Desktop Harness Bot}"
REPORT_AUTHOR_EMAIL="${REPORT_AUTHOR_EMAIL:-desktop-harness-bot@localhost}"
REPORT_AUTO_PUBLISH="${REPORT_AUTO_PUBLISH:-true}"

if [[ -z "$publish" ]]; then
  publish="$REPORT_AUTO_PUBLISH"
fi
[[ "$publish" == true || "$publish" == false ]] || {
  echo "REPORT_AUTO_PUBLISH must be true or false" >&2
  exit 2
}

report_file="$(cd "$(dirname "$report_file")" && pwd)/$(basename "$report_file")"

if grep -Eqi '(authorization:[[:space:]]*bearer|private[ _-]?key|api[ _-]?key[[:space:]]*[:=]|password[[:space:]]*[:=]|token[[:space:]]*[:=])' "$report_file"; then
  echo "report appears to contain a credential marker; sanitize it before publishing" >&2
  exit 2
fi

timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
month="$(date -u +%Y-%m)"
destination="reports/$month/$machine-$timestamp.md"

if ! "$publish"; then
  cat <<EOF
dry run: no report-repository commit or push will be made
repository: $REPORT_REPOSITORY
branch: $REPORT_BRANCH
source: $report_file
destination: $destination
omit --dry-run to publish
EOF
  exit 0
fi

askpass_file=""
report_worktree=""
cleanup() {
  [[ -z "$askpass_file" ]] || rm -f -- "$askpass_file"
  [[ -z "$report_worktree" ]] || rm -rf -- "$report_worktree"
}
trap cleanup EXIT

if [[ -v REPORT_GITLAB_TOKEN && -n "$REPORT_GITLAB_TOKEN" ]]; then
  askpass_file="$(mktemp)"
  chmod 700 "$askpass_file"
  cat >"$askpass_file" <<'EOF'
#!/usr/bin/env bash
case "$1" in
  *Username*) printf '%s\n' 'oauth2' ;;
  *) printf '%s\n' "$REPORT_GITLAB_TOKEN" ;;
esac
EOF
  export GIT_ASKPASS="$askpass_file"
  export GIT_TERMINAL_PROMPT=0
fi

tmp_root="${TMPDIR:-/tmp}"
report_worktree="$(mktemp -d "$tmp_root/desktop-harness-report.XXXXXX")"
git clone --branch "$REPORT_BRANCH" --single-branch "$REPORT_REPOSITORY" "$report_worktree"

mkdir -p "$(dirname "$report_worktree/$destination")"
cp -- "$report_file" "$report_worktree/$destination"
git -C "$report_worktree" add -- "$destination"
git -C "$report_worktree" -c user.name="$REPORT_AUTHOR_NAME" \
  -c user.email="$REPORT_AUTHOR_EMAIL" \
  commit -m "report: add $machine deployment result"
git -C "$report_worktree" push origin "$REPORT_BRANCH"

echo "published $destination to $REPORT_REPOSITORY ($REPORT_BRANCH)"
