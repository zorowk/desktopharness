import json
import tempfile
import unittest
from pathlib import Path

from mcp_autogui.desktop_capabilities import (
    find_capability,
    load_desktop_application_catalogue,
    load_keybinding_catalogue,
    validate_application_id,
)


def write_shortcut(root, slug, *, hotkeys, enabled=True, name="Test"):
    directory = root / f"org.deepin.dde.keybinding.shortcut.{slug}"
    directory.mkdir()
    (directory / "org.deepin.shortcut.json").write_text(
        json.dumps(
            {
                "contents": {
                    "displayName": {"value": name},
                    "hotkeys": {"value": hotkeys},
                    "enabled": {"value": enabled},
                    "category": {"value": "System"},
                    "triggerType": {"value": 1},
                    "triggerValue": {"value": ["unsafe command must not leak"]},
                }
            }
        ),
        encoding="utf-8",
    )


class DesktopCapabilitiesTests(unittest.TestCase):
    def test_launcher_is_a_safe_meta_capability(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_shortcut(root, "app.launcher", hotkeys=["Meta"], name="Launcher")

            capability = load_keybinding_catalogue(root)[0]

        self.assertEqual(capability["capability_id"], "desktop.launcher.toggle")
        self.assertEqual(capability["normalized_hotkeys"], [["winleft"]])
        self.assertTrue(capability["auto_invokable"])
        self.assertNotIn("triggerValue", capability)

    def test_high_risk_capability_is_catalogued_but_not_auto_invokable(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_shortcut(root, "app.lockscreen", hotkeys=["Meta+L"])

            capability = load_keybinding_catalogue(root)[0]

        self.assertEqual(capability["policy"], "confirm")
        self.assertFalse(capability["auto_invokable"])

    def test_application_id_rejects_command_like_values(self):
        self.assertEqual(validate_application_id("deepin-editor"), "deepin-editor")
        for invalid in ("/usr/bin/editor", "dde-am -c id", "https://example.test", "--help"):
            with self.assertRaises(ValueError):
                validate_application_id(invalid)

    def test_application_catalogue_tolerates_duplicate_desktop_entry_fields(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "example.desktop").write_text(
                "[Desktop Entry]\nName=Example\nName=Example Updated\n",
                encoding="utf-8",
            )

            applications = load_desktop_application_catalogue((root,))

        self.assertEqual(applications[0]["app_id"], "example")
        self.assertEqual(applications[0]["display_name"], "Example Updated")

    def test_unknown_capability_is_not_found(self):
        with tempfile.TemporaryDirectory() as directory:
            self.assertIsNone(find_capability("unknown", Path(directory)))
