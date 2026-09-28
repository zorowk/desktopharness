# Deployment report publishing

This skill publishes a prepared deployment report to a separate Git repository after deployment
verification by default. The report repository is not this source repository. Use `--dry-run`
only when publication should be suppressed for a local check.

The controlling model writes the report from
[`../assets/deployment-report-template.md`](../assets/deployment-report-template.md). Fill only
facts observed during the deployment and probes. Keep an unavailable value as `未采集`; do not
turn a PID, an open port, or a delivered input into a `READY` claim without the corresponding
acceptance evidence.

Name the local report `<machine>-<YYYYMMDDTHHMMSSZ>.md` with a UTC timestamp. The publisher
preserves that identity in its destination naming scheme:
`reports/YYYY-MM/<machine>-<YYYYMMDDTHHMMSSZ>.md`.

## Local configuration

Create `reporting.local.env` next to `SKILL.md`, with mode `0600`:

```sh
REPORT_REPOSITORY=https://gitlabwh.uniontech.com/ut000203/desktop-harness-report.git
REPORT_BRANCH=main
REPORT_AUTHOR_NAME='Desktop Harness Bot'
REPORT_AUTHOR_EMAIL='desktop-harness-bot@uniontech.com'
REPORT_AUTO_PUBLISH=true

# Optional. Prefer an environment variable or credential manager when possible.
# REPORT_GITLAB_TOKEN=...
```

The configuration file is ignored by this repository. At publication time, the script clones the
report repository to a fresh temporary directory and removes that directory on exit. Do not place a
password, SSH private key, raw desktop content, or model/API key in a report or its filename.

For GitLab HTTPS, a project or bot access token must be scoped only to this report repository and
must have `write_repository`. The script uses `REPORT_GITLAB_TOKEN` through a temporary
`GIT_ASKPASS` helper; it never puts the token in the remote URL, commit message, or report. When a
token is supplied, the publisher disables configured credential helpers so a stale local credential
cannot override it. If no token is supplied, Git uses the configured credential helper or
interactive authentication.

## Invocation

```sh
skills/deploy-desktop-harness/scripts/publish_report.sh \
  --report /absolute/path/to/sanitized-report.md \
  --machine desktop-test-01

# Override configured automatic publication for a local check:
skills/deploy-desktop-harness/scripts/publish_report.sh \
  --report /absolute/path/to/sanitized-report.md \
  --machine desktop-test-01 \
  --dry-run
```

The destination is `reports/YYYY-MM/<machine>-<YYYYMMDDTHHMMSSZ>.md`. The publisher refuses a
dirty report worktree, a non-regular report, or common secret markers. It fast-forwards before
commit and never force-pushes. A conflict, authentication error, protected branch, or rejected
push leaves the source report unchanged and reports the failure.
