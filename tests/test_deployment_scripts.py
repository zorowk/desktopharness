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
