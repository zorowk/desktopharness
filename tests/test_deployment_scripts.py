import os
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class ClientSystemPreparationTests(unittest.TestCase):
    def prepare(self, failed_command=""):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            (base / "client_env.sh").write_text((ROOT / "client_env.sh").read_text())
            (base / "udev").mkdir()
            (base / "udev/99-ydotoold-mouse.rules").write_text("test rule\n")
            (base / ".Xauthority").touch()
            command = r'''
set -euo pipefail
sudo() {
    printf '%s\n' "$*" >> "$HOME/commands"
    [[ "$1" != "$FAILED_COMMAND" ]]
}
dpkg-query() { return 1; }
ydotool() { :; }
udevadm() { :; }
cmp() { return 1; }
uv() { printf 'unexpected uv\n' >> "$HOME/commands"; return 1; }
# As in provisioning, sourcing happens in a guarded subshell. The sudo
# function deliberately is not exported to child Bash processes.
(set -- --prepare-system; source "$HOME/client_env.sh") || exit 19
'''
            result = subprocess.run(
                ["bash", "-c", command],
                env={**os.environ, "HOME": directory, "CLIENT_ENV_FILE": str(base / "absent"),
                     "FAILED_COMMAND": failed_command},
                capture_output=True, text=True, timeout=10,
            )
            return result, (base / "commands").read_text().splitlines()

    def test_preparation_inherits_sudo_and_stops_before_server_start(self):
        result, commands = self.prepare()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(commands[0], "apt-get update")
        self.assertTrue(commands[1].startswith("apt-get install -y "))
        self.assertTrue(commands[2].startswith("install -m 0644 "))
        self.assertEqual(commands[3:], ["udevadm control --reload-rules"])

    def test_failed_package_installation_stops_preparation(self):
        result, commands = self.prepare("apt-get")
        self.assertEqual(result.returncode, 19, result.stderr)
        self.assertEqual(commands, ["apt-get update"])

    def test_failed_rule_installation_stops_before_reload(self):
        result, commands = self.prepare("install")
        self.assertEqual(result.returncode, 19, result.stderr)
        self.assertTrue(commands[-1].startswith("install -m 0644 "))
        self.assertFalse(any("reload-rules" in command for command in commands))


class TreelandSourceDependencyTests(unittest.TestCase):
    helper = ROOT / "skills/deploy-desktop-harness/scripts/treeland_source_deps.sh"

    def test_remaining_dependencies_preserve_constraints_and_source_control(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            (base / "debian").mkdir()
            control = """Source: treeland
Build-Depends: cmake (>= 3.25),
 libddm-dev (>= 0.4.0),
 treeland-protocols (>= 0.6.0),
 libsystemd-dev [linux-any],
 pkg-config:any | pkgconf
Build-Depends-Arch: libddm, libwayland-dev (>= 1.23)

Package: treeland
Architecture: any
Depends: libddm, ${shlibs:Depends}
"""
            (base / "debian/control").write_text(control)
            command = r'''
set -euo pipefail
source "$1"
src="$2"
sudo() { cp "${@: -1}/debian/control" "$src/resolved-control"; }
install_remaining_build_deps "$src"
'''
            subprocess.run(
                ["bash", "-c", command, "bash", str(self.helper), directory],
                check=True, capture_output=True, text=True, timeout=10,
            )
            resolved = (base / "resolved-control").read_text()
            source_fields = resolved.split("\n\n")[0]
            self.assertNotIn("libddm", source_fields)
            self.assertNotIn("treeland-protocols", source_fields)
            self.assertIn("cmake (>= 3.25)", source_fields)
            self.assertIn("libsystemd-dev [linux-any]", source_fields)
            self.assertIn("pkg-config:any | pkgconf", source_fields)
            self.assertIn("libwayland-dev (>= 1.23)", source_fields)
            self.assertIn("Depends: libddm, ${shlibs:Depends}", resolved)
            self.assertEqual((base / "debian/control").read_text(), control)

    def test_protocols_are_installed_before_ddm_and_linker_cache_is_refreshed(self):
        command = r'''
set -euo pipefail
source "$1"
TREELAND_PROTOCOLS_REF=protocol-tag
DDM_REF=ddm-tag
build_source_dependency() { printf '%s\n' "$*"; }
sudo() { printf '%s\n' "$*"; }
source_dependencies_built=0
build_treeland_source_dependencies
[[ "$source_dependencies_built" == 1 ]]
'''
        result = subprocess.run(
            ["bash", "-c", command, "bash", str(self.helper)],
            check=True, capture_output=True, text=True, timeout=10,
        )
        self.assertEqual(result.stdout.splitlines(), [
            "treeland-protocols https://github.com/linuxdeepin/treeland-protocols.git protocol-tag",
            "ddm https://github.com/linuxdeepin/ddm.git ddm-tag",
            "ldconfig",
        ])

    def test_failed_protocol_build_stops_before_ddm(self):
        command = r'''
set -euo pipefail
source "$1"
TREELAND_PROTOCOLS_REF=HEAD
DDM_REF=HEAD
build_source_dependency() { printf '%s\n' "$1"; return 23; }
sudo() { printf 'unexpected sudo\n'; }
build_treeland_source_dependencies
'''
        result = subprocess.run(
            ["bash", "-c", command, "bash", str(self.helper)],
            capture_output=True, text=True, timeout=10,
        )
        self.assertEqual(result.returncode, 23)
        self.assertEqual(result.stdout.splitlines(), ["treeland-protocols"])

    def test_source_build_uses_usr_as_install_prefix(self):
        with tempfile.TemporaryDirectory() as directory:
            command = r'''
set -euo pipefail
source "$1"
src="$2"
mktemp() { mkdir -p "$src/checkout"; printf '%s\n' "$src/checkout"; }
timeout() { shift; "$@"; }
git() {
    if [[ "$*" == *'rev-parse HEAD' ]]; then printf '%040d\n' 1; fi
}
cmake() { printf '%s\n' "cmake $*" >> "$src/commands"; }
sudo() { "$@"; }
build_source_dependency ddm https://github.com/linuxdeepin/ddm.git test-ref
'''
            result = subprocess.run(
                ["bash", "-c", command, "bash", str(self.helper), directory],
                check=True, capture_output=True, text=True, timeout=10,
            )
            commands = (Path(directory) / "commands").read_text().splitlines()
            self.assertIn("-DCMAKE_INSTALL_PREFIX=/usr", commands[0])
            self.assertIn("--parallel 2", commands[1])
            self.assertTrue(commands[2].startswith("cmake --install "))
            self.assertIn("source=https://github.com/linuxdeepin/ddm.git", result.stdout)
            self.assertIn("ref=test-ref commit=", result.stdout)


class TreelandDependencyFallbackTests(unittest.TestCase):
    def run_payload(self, missing="", source_deps="1", config_error=""):
        scripts = ROOT / "skills/deploy-desktop-harness/scripts"
        payload = (scripts / "provision_treeland_debug.sh").read_text()
        payload = payload.split("cat <<'REMOTE'\n", 1)[1].split("\nREMOTE\n", 1)[0]
        stubs = r'''
git() {
    if [[ "$*" == *'rev-parse HEAD' ]]; then printf '%s\n' "$TREELAND_COMMIT"; fi
}
mktemp() { command mktemp -d "$TEST_ROOT/source.XXXXXXXX"; }
sudo() {
    if [[ "$1" == apt-get && "${2:-}" == build-dep && -n "$MISSING" ]]; then
        printf ' Depends: %s (>= 9.0) but 1.0 is to be installed\n' "$MISSING"
        return 100
    fi
}
cmake() {
    if [[ "$1" == --fresh && -n "$CONFIG_ERROR" && "$source_dependencies_built" == 0 ]]; then
        printf 'CMake Error: Could not find a compatible version of %s\n' "$CONFIG_ERROR"
        return 1
    fi
}
systemctl() { :; }
install_remaining_build_deps() { printf 'REMAINING_APT_DEPS\n'; }
build_treeland_source_dependencies() {
    printf 'SOURCE_FALLBACK\n'
    source_dependencies_built=1
}
'''
        with tempfile.TemporaryDirectory() as directory:
            return subprocess.run(
                ["bash", "-c", stubs + payload],
                env={**os.environ, "TEST_ROOT": directory, "MISSING": missing,
                     "CONFIG_ERROR": config_error, "TREELAND_SOURCE_DEPS": source_deps,
                     "TREELAND_COMMIT": "1" * 40, "TREELAND_SOURCE_URL": "/test/source",
                     "TREELAND_REF": "test", "TREELAND_APT_UPGRADE": "0",
                     "TREELAND_BUILD_JOBS": "2"},
                capture_output=True, text=True, timeout=10,
            )

    def test_apt_ddm_version_failure_uses_authorized_source_fallback(self):
        result = self.run_payload(missing="libddm-dev")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("REMAINING_APT_DEPS", result.stdout)
        self.assertIn("SOURCE_FALLBACK", result.stdout)
        self.assertIn("TREELAND_RESTARTED", result.stdout)

    def test_source_fallback_is_opt_in(self):
        result = self.run_payload(missing="libddm-dev", source_deps="0")
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("SOURCE_FALLBACK", result.stdout)
        self.assertIn("phase=DEPENDENCY", result.stderr)

    def test_unrelated_apt_failure_does_not_build_source_components(self):
        result = self.run_payload(missing="qt6-base-dev")
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("SOURCE_FALLBACK", result.stdout)

    def test_cmake_ddm_version_failure_uses_source_fallback(self):
        result = self.run_payload(config_error="DDM")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("SOURCE_FALLBACK", result.stdout)

    def test_unrelated_cmake_error_stops_without_source_fallback(self):
        result = self.run_payload(config_error="Qt6")
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("SOURCE_FALLBACK", result.stdout)
        self.assertIn("phase=TREELAND_BUILD", result.stderr)
