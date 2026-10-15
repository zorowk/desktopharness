# Treeland build preparation

Before installing build dependencies, the helper always runs `apt-get update`. It installs the
build tools and runs `apt-get build-dep` against the selected checkout's `debian/control`, so a
fork's dependency changes are used. If that file is absent, report `DEPENDENCY`. Diagnose package
resolution errors before choosing the source dependency fallback below; do not substitute upstream
Treeland for the selected fork.

For test machines that need installed packages refreshed, pass `--apt-upgrade` (or
`TREELAND_APT_UPGRADE=1`). This runs `apt-get upgrade -y` after updating package indexes and before
installing dependencies. Include this machine-wide package upgrade in the authorized plan; never
escalate automatically to `dist-upgrade`, `full-upgrade`, or package removal. If upgrade
was not authorized, diagnose the apt error and propose the specific needed update. Do not retry
an unchanged failed dependency installation repeatedly. Missing repositories, unavailable package
versions, and dpkg errors need their own diagnosis; update/upgrade does not guarantee resolution.

## Source dependencies when apt cannot satisfy versions

When update and an authorized upgrade still cannot provide compatible DDM or protocol versions,
use `--source-deps`. This authorizes source installation of the two known Treeland dependencies:

- `https://github.com/linuxdeepin/treeland-protocols.git`, built first.
- `https://github.com/linuxdeepin/ddm.git`, built after the protocols.

The helper activates this fallback for an apt version failure involving these packages, or a
Treeland CMake configuration failure mentioning DDM or TreelandProtocols. Unrelated dependency
errors remain failures. Each repository ref defaults to `HEAD`; set `TREELAND_PROTOCOLS_REF` and
`DDM_REF` to compatible tags or commits when the selected Treeland revision requires them. Each
fetch is detached at the resolved commit and its source, ref, SHA, and directory are logged.

All three projects use `-DCMAKE_INSTALL_PREFIX=/usr`: `/usr` is the installation prefix, not the
source or build directory. Sources and configure/build/install logs stay in fresh `/var/tmp`
directories. Compilation defaults to two jobs; set `TREELAND_BUILD_JOBS` for the target's capacity.
Deployment disables Waylib examples and protocol test targets while keeping Treeland and its
debug interface enabled; application testing is a separate step after deployment.
The source installations do not update dpkg's recorded versions. To install remaining apt build
dependencies, the helper generates a temporary control file omitting only DDM/protocol entries;
it never edits the selected checkout's `debian/control` or creates fake version packages. CMake
then validates the actual installed development files and versions. Do not rerun the unchanged,
unfiltered `apt-get build-dep` after source installation: it would still see old dpkg versions.

## Activate installed binaries

After installation, run `ldconfig` and `systemctl daemon-reload`, then restart **both**
`ddm.service` and `treeland.service` (or the discovered user Treeland service). Restarting only
Treeland can leave DDM using the previous library or executable. This ends the graphical session.
A machine reboot is preferred when replacing the whole stack because it clears all old processes
and loaded libraries. Include the restart or reboot in the deployment plan. For a controller
running on the same desktop, save logs and pending verification before reboot and resume after
reconnecting; a scheduled reboot or successful install is not `READY`. Verify services, MCP,
observe/screenshot, and approved input again after the machine or services return. Never change
DDM automatic-login settings to recover the desktop; use the prelogin path when appropriate.

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
