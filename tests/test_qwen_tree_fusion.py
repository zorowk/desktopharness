import unittest

from mcp_autogui.spatial_fusion import (
    desktop_bounds_from_treeland,
    desktop_to_screenshot_point,
    flatten_treeland_windows,
    fuse_qwen_actions_with_treeland,
    qwen_normalized_to_screenshot,
    screenshot_to_qwen_normalized,
)


def sample_tree():
    return {
        "currentMode": "Normal",
        "layers": [
            {
                "name": "background",
                "layer": 0,
                "windows": [
                    {
                        "appId": "desktop",
                        "title": "Desktop",
                        "visible": True,
                        "active": False,
                        "z": 0,
                        "geometry": {"x": 0, "y": 0, "width": 1000, "height": 800},
                    }
                ],
                "workspaces": [],
            },
            {
                "name": "workspace",
                "layer": 1,
                "windows": [],
                "workspaces": [
                    {
                        "isActive": True,
                        "windows": [
                            {
                                "appId": "settings",
                                "title": "Settings",
                                "container": "workspace",
                                "workspace": 0,
                                "visible": True,
                                "active": True,
                                "z": 1,
                                "geometry": {
                                    "x": 100,
                                    "y": 100,
                                    "width": 800,
                                    "height": 600,
                                },
                            }
                        ],
                    }
                ],
            },
            {
                "name": "top",
                "layer": 2,
                "windows": [
                    {
                        "appId": "dialog",
                        "title": "Confirmation",
                        "visible": True,
                        "active": False,
                        "z": 0,
                        "geometry": {"x": 400, "y": 300, "width": 200, "height": 200},
                    }
                ],
                "workspaces": [],
            },
        ],
    }


class QwenTreeFusionTests(unittest.TestCase):
    def test_coordinate_is_attached_to_topmost_window(self):
        actions = [{"type": "click", "coordinate": {"x": 500, "y": 400}}]

        fused = fuse_qwen_actions_with_treeland(actions, sample_tree(), (1000, 800))
        action = fused["actions"][0]

        self.assertEqual(action["target_window"]["appId"], "dialog")
        self.assertEqual(
            action["target_window"]["window_relative_coordinate"],
            {"x": 100.0, "y": 100.0},
        )
        self.assertEqual(action["validation"]["matching_window_count"], 3)

    def test_action_without_coordinate_uses_active_window_context(self):
        actions = [{"type": "hotkey", "coordinate": None}]

        fused = fuse_qwen_actions_with_treeland(actions, sample_tree(), (1000, 800))

        self.assertEqual(fused["actions"][0]["target_window"]["appId"], "settings")
        self.assertEqual(
            fused["actions"][0]["validation"]["target_source"], "active_window"
        )

    def test_screenshot_coordinate_maps_to_negative_desktop_origin(self):
        tree = sample_tree()
        tree["layers"][0]["windows"][0]["geometry"] = {
            "x": -1000,
            "y": 0,
            "width": 2000,
            "height": 800,
        }
        actions = [{"type": "click", "coordinate": {"x": 100, "y": 100}}]

        fused = fuse_qwen_actions_with_treeland(actions, tree, (2000, 800))

        self.assertEqual(
            fused["actions"][0]["desktop_coordinate"],
            {"x": -900.0, "y": 100.0},
        )

    def test_desktop_coordinate_maps_back_to_screenshot_pixels(self):
        tree = sample_tree()
        tree["layers"][0]["windows"][0]["geometry"] = {
            "x": 0,
            "y": 0,
            "width": 1536,
            "height": 864,
        }

        screenshot = desktop_to_screenshot_point(
            {"x": 1252.8, "y": 80.0},
            1920,
            1080,
            desktop_bounds_from_treeland(tree),
        )

        self.assertEqual(screenshot, {"x": 1566.0, "y": 100.0})

    def test_hidden_workspace_window_is_not_a_fusion_target_with_container_names(self):
        tree = {
            "currentMode": "Normal",
            "layers": [
                {
                    "name": "BackgroundContainer",
                    "layer": -2,
                    "windows": [
                        {
                            "appId": "",
                            "title": "",
                            "visible": True,
                            "active": False,
                            "z": 0,
                            "container": "BackgroundContainer",
                            "workspace": -1,
                            "geometry": {"x": 0, "y": 0, "width": 1920, "height": 1080},
                        }
                    ],
                    "workspaces": [],
                },
                {
                    "name": "WorkspaceContainer",
                    "layer": 0,
                    "windows": [],
                    "workspaces": [
                        {
                            "isActive": True,
                            "windows": [
                                {
                                    "appId": "firefox",
                                    "title": "Firefox hidden",
                                    "visible": False,
                                    "active": False,
                                    "z": 3,
                                    "container": "WorkspaceContainer",
                                    "workspace": 0,
                                    "geometry": {
                                        "x": 0,
                                        "y": 0,
                                        "width": 1920,
                                        "height": 1080,
                                    },
                                },
                                {
                                    "appId": "deepin-editor",
                                    "title": "Editor",
                                    "visible": True,
                                    "active": True,
                                    "z": 2,
                                    "container": "WorkspaceContainer",
                                    "workspace": 0,
                                    "geometry": {
                                        "x": 280,
                                        "y": 96,
                                        "width": 1536,
                                        "height": 816,
                                    },
                                },
                            ],
                        }
                    ],
                },
            ],
        }

        flattened = flatten_treeland_windows(tree)
        self.assertNotIn("firefox", [window["appId"] for window in flattened])

        fused = fuse_qwen_actions_with_treeland(
            [{"type": "click", "coordinate": {"x": 960, "y": 540}}],
            tree,
            (1920, 1080),
        )

        target = fused["actions"][0]["target_window"]
        self.assertEqual(target["appId"], "deepin-editor")
        self.assertTrue(target["visible"])

    def test_desktop_bounds_include_top_dock_layer(self):
        tree = {
            "layers": [
                {
                    "name": "BackgroundContainer",
                    "layer": -2,
                    "windows": [
                        {"geometry": {"x": 0, "y": 0, "width": 1920, "height": 1032}}
                    ],
                    "workspaces": [],
                },
                {
                    "name": "TopContainer",
                    "layer": 2,
                    "windows": [
                        {"geometry": {"x": 0, "y": 1032, "width": 1920, "height": 48}}
                    ],
                    "workspaces": [],
                },
            ]
        }

        self.assertEqual(
            desktop_bounds_from_treeland(tree),
            {"x": 0.0, "y": 0.0, "width": 1920.0, "height": 1080.0},
        )

    def test_qwen_normalized_roundtrip_is_lossy_at_most_one_pixel(self):
        width, height = 1920, 1080
        samples = [
            (0, 0),
            (100, 100),
            (960, 540),
            (1234, 567),
            (1919, 1079),
            (1, 1078),
        ]
        for pixel_x, pixel_y in samples:
            with self.subTest(pixel=(pixel_x, pixel_y)):
                normalized = screenshot_to_qwen_normalized(
                    {"x": pixel_x, "y": pixel_y}, width, height
                )
                back = qwen_normalized_to_screenshot(normalized, width, height)
                self.assertLessEqual(abs(back["x"] - pixel_x), 1.0)
                self.assertLessEqual(abs(back["y"] - pixel_y), 1.0)

    def test_qwen_normalized_boundaries(self):
        width, height = 1920, 1080
        self.assertEqual(
            screenshot_to_qwen_normalized({"x": 0, "y": 0}, width, height),
            {"x": 0, "y": 0},
        )
        self.assertEqual(
            screenshot_to_qwen_normalized(
                {"x": width - 1, "y": height - 1}, width, height
            ),
            {"x": 999, "y": 999},
        )
        self.assertEqual(
            qwen_normalized_to_screenshot({"x": 999, "y": 999}, width, height),
            {"x": float(width - 1), "y": float(height - 1)},
        )

    def test_screenshot_desktop_roundtrip(self):
        bounds = {"x": 0.0, "y": 0.0, "width": 1920.0, "height": 1080.0}
        screenshot = desktop_to_screenshot_point(
            {"x": 1252.8, "y": 80.0},
            1920,
            1080,
            bounds,
        )
        back = {
            "x": bounds["x"] + screenshot["x"] * bounds["width"] / 1920,
            "y": bounds["y"] + screenshot["y"] * bounds["height"] / 1080,
        }
        self.assertAlmostEqual(back["x"], 1252.8, places=6)
        self.assertAlmostEqual(back["y"], 80.0, places=6)

    def test_desktop_screenshot_roundtrip_with_negative_origin(self):
        bounds = {"x": -1000.0, "y": -500.0, "width": 2000.0, "height": 1000.0}
        desktop = {"x": -900.0, "y": 100.0}
        pixel = desktop_to_screenshot_point(desktop, 1920, 960, bounds)
        back = {
            "x": bounds["x"] + pixel["x"] * bounds["width"] / 1920,
            "y": bounds["y"] + pixel["y"] * bounds["height"] / 960,
        }
        self.assertAlmostEqual(back["x"], -900.0, places=6)
        self.assertAlmostEqual(back["y"], 100.0, places=6)


if __name__ == "__main__":
    unittest.main()
