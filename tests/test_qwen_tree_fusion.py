import unittest

from mcp_autogui.spatial_fusion import fuse_qwen_actions_with_treeland


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


if __name__ == "__main__":
    unittest.main()
