# Treeland build preparation

Before installing build dependencies, the helper always runs `apt-get update`. It installs the
build tools and runs `apt-get build-dep` against the selected checkout's `debian/control`, so a
fork's dependency changes are used. If that file is absent or package resolution fails, report
`DEPENDENCY` with the apt error; do not silently fall back to upstream dependencies.

For test machines that need installed packages refreshed, pass `--apt-upgrade` (or
`TREELAND_APT_UPGRADE=1`). This runs `apt-get upgrade -y` after updating package indexes and before
installing dependencies. Include this machine-wide package upgrade in the authorized plan; never
escalate automatically to `dist-upgrade`, `full-upgrade`, package removal, or reboot. If upgrade
was not authorized, diagnose the apt error and propose the specific needed update. Do not retry
an unchanged failed dependency installation repeatedly. Missing repositories, unavailable package
versions, and dpkg errors need their own diagnosis; update/upgrade does not guarantee resolution.

Every deployment uses a fresh `/var/tmp/treeland-debug-build.*` directory and preserves it with
configure/build logs for diagnosis. Record the source URL (without credentials), requested ref,
resolved commit, build directory, and whether apt upgrade ran in the deployment report. A build or
install failure must stop before DesktopHarness provisioning and must not report `READY`.


## Privilege preflight

Check `sudo -n true` during inventory. If password authentication is needed, pass
`--sudo-password`; the controller enters the supplied password at each provisioning helper's
hidden terminal prompt. The SSH password can be reused when the remote account uses it for sudo.
The option applies to both Treeland build and DesktopHarness provisioning, including `--prelogin`.
Before starting MCP as the desktop user, provisioning sources `client_env.sh --prepare-system`
in a subshell that inherits the authenticated sudo function, then starts ydotoold for the desktop
user's runtime directory. Package installation, ydotool installation, and udev setup therefore run
before the detached server starts; no password is exported to that server. The target checkout
must support `--prepare-system`; preparation errors stop at `DEPENDENCY`.

The internal `remote_sudo_exec.sh` keeps the password in unexported shell memory and sends it over
SSH stdin, separately from command arguments and the script. It validates sudo before provisioning
and refreshes authentication before each sudo command without consuming that command's stdin.
Do not enable shell tracing. No password file or password environment variable is created.
If authentication fails or sudo requires an actual terminal, stop and report `DEPENDENCY`; do not
change sudoers or authentication policy. Without this option, existing noninteractive sudo behavior
is preserved.
