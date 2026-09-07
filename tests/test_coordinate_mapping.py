import unittest
from subprocess import CompletedProcess
from unittest.mock import patch

from mcp_autogui.adapters.compositor.treeland import (
    desktop_bounds_from_treeland,
    flatten_treeland_windows,
    read_treeland_tree,
)
from mcp_autogui.coordinate_mapping import (
    desktop_to_screenshot_point,
    screenshot_to_desktop_point,
)


class CoordinateMappingTests(unittest.TestCase):
    def test_desktop_coordinate_maps_back_to_screenshot_pixels(self):
        bounds = {"x": 0.0, "y": 0.0, "width": 1536.0, "height": 864.0}
        self.assertEqual(
            desktop_to_screenshot_point({"x": 1252.8, "y": 80.0}, 1920, 1080, bounds),
            {"x": 1566.0, "y": 100.0},
        )

    def test_screenshot_desktop_roundtrip_with_negative_origin(self):
        bounds = {"x": -1000.0, "y": -500.0, "width": 2000.0, "height": 1000.0}
        point = {"x": 100, "y": 576}
        desktop = screenshot_to_desktop_point(point, 2000, 960, bounds)
        self.assertEqual(desktop, {"x": -900.0, "y": 100.0})
        self.assertEqual(desktop_to_screenshot_point(desktop, 2000, 960, bounds), point)

class TreelandTreeParserTests(unittest.TestCase):
    def test_tree_reader_requests_machine_readable_json(self):
        tree = {"layers": []}
        with patch(
            "mcp_autogui.adapters.compositor.treeland.subprocess.run",
            return_value=CompletedProcess(
                args=["treeland-debug", "--json", "tree"], returncode=0, stdout='{"layers": []}', stderr=""
            ),
        ) as run:
            self.assertEqual(read_treeland_tree(), tree)

        run.assert_called_once_with(
            ["treeland-debug", "--json", "tree"],
            check=True,
            capture_output=True,
            text=True,
            timeout=35,
        )

    def test_desktop_bounds_include_background_bounding_rect_and_dock(self):
        tree = {
            "layers": [
                {"name": "BackgroundContainer", "windows": [{"boundingRect": {"x": 0, "y": 0, "width": 1920, "height": 1032}}], "workspaces": []},
                {"name": "TopContainer", "windows": [{"geometry": {"x": 0, "y": 1032, "width": 1920, "height": 48}}], "workspaces": []},
            ]
        }
        self.assertEqual(desktop_bounds_from_treeland(tree), {"x": 0.0, "y": 0.0, "width": 1920.0, "height": 1080.0})

    def test_hidden_workspace_window_is_excluded(self):
        tree = {"layers": [{"name": "WorkspaceContainer", "layer": 0, "windows": [], "workspaces": [{"isActive": True, "windows": [{"appId": "hidden", "visible": False, "geometry": {"x": 0, "y": 0, "width": 1, "height": 1}}, {"appId": "shown", "visible": True, "geometry": {"x": 0, "y": 0, "width": 1, "height": 1}}]}]}]}
        self.assertEqual([window["appId"] for window in flatten_treeland_windows(tree)], ["shown"])


if __name__ == "__main__":
    unittest.main()
