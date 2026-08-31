import io
import subprocess
import unittest
from unittest.mock import patch

from PIL import Image

from mcp_autogui.wayland_screenshot import grab_with_grim, install_grim_backend


def png_bytes(size=(100, 80)):
    output = io.BytesIO()
    Image.new("RGB", size, "white").save(output, format="PNG")
    return output.getvalue()


class WaylandScreenshotTests(unittest.TestCase):
    @patch("mcp_autogui.wayland_screenshot.subprocess.run")
    def test_grim_capture_supports_pyscreenshot_bbox(self, run):
        run.return_value.stdout = png_bytes()

        image = grab_with_grim((10, 20, 70, 60))

        self.assertEqual(image.size, (60, 40))
        run.assert_called_once_with(
            ["grim", "-"], check=True, capture_output=True, timeout=15
        )

    @patch("mcp_autogui.wayland_screenshot.subprocess.run")
    def test_grim_failure_includes_stderr(self, run):
        run.side_effect = subprocess.CalledProcessError(
            1, ["grim", "-"], stderr=b"cannot connect to compositor"
        )

        with self.assertRaisesRegex(RuntimeError, "cannot connect to compositor"):
            grab_with_grim()

    @patch("mcp_autogui.wayland_screenshot.shutil.which", return_value="/usr/bin/grim")
    def test_installs_backend_for_wayland(self, _which):
        import pyscreenshot

        original = pyscreenshot.grab
        try:
            with patch.dict(
                "os.environ",
                {"XDG_SESSION_TYPE": "wayland", "WAYLAND_DISPLAY": "treeland.socket"},
                clear=True,
            ):
                self.assertTrue(install_grim_backend())
                self.assertIs(pyscreenshot.grab, grab_with_grim)
        finally:
            pyscreenshot.grab = original


if __name__ == "__main__":
    unittest.main()
