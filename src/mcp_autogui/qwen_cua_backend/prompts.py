"""Qwen-CUA S2 prompt definitions migrated from gui-mcp."""

from __future__ import annotations

import json


ACTION_DESCRIPTION = """
* `key`: Press one key, or a shortcut when multiple keys are provided.
* `key_down`: Press and hold the specified keys.
* `key_up`: Release the specified keys in reverse order.
* `type`: Type a string of text.
* `mouse_move`: Move the cursor to a coordinate.
* `left_click`: Click the left mouse button at a coordinate.
* `left_click_drag`: Drag the cursor to a coordinate. To move a window, first
  use `mouse_move` in one step to place the pointer on an empty titlebar area;
  after that step is executed, use `left_click_drag` in the next step. Never
  drag an editor tab to move its window. To resize a window, first move to its
  visible outer border or corner, then drag in the next step.
* `right_click`: Click the right mouse button at a coordinate.
* `middle_click`: Click the middle mouse button at a coordinate.
* `double_click`: Double-click the left mouse button at a coordinate.
* `triple_click`: Triple-click the left mouse button at a coordinate.
* `scroll`: Scroll vertically.
* `hscroll`: Scroll horizontally.
* `wait`: Wait for the interface to change.
* `terminate`: Finish the task with success or failure.
"""

DESCRIPTION_TEMPLATE = """Use a mouse and keyboard to interact with a desktop GUI.
* Applications are opened by interacting with visible desktop UI.
* Applications may need time to start or update; use wait when appropriate.
{resolution_info}
* Consult the latest screenshot before choosing a coordinate.
* Aim at the visible center of a control unless the task explicitly requires an edge.
* Return only the next action, not an entire speculative action sequence.
* Emit exactly one `<tool_call>` block, then stop. Never repeat a tool call."""

SYSTEM_TEMPLATE = """# Tools

You may call one function for the next GUI step. The function signature is inside
<tools></tools> XML tags:
<tools>
{tools_xml}
</tools>

# Response format

Return exactly:
Action: a short imperative describing the next UI action.
<tool_call>
{{"name": "computer_use", "arguments": {{...}}}}
</tool_call>

Do not output executable Python. To finish, call `terminate` with a status."""


def build_system_prompt(coordinate_type: str, processed_size: tuple[int, int]) -> str:
    width, height = processed_size
    if coordinate_type == "absolute":
        resolution_info = f"* The provided image resolution is {width}x{height}."
    else:
        resolution_info = "* Coordinates use a normalized 0..999 by 0..999 grid."
    description = DESCRIPTION_TEMPLATE.format(resolution_info=resolution_info)
    tool = {
        "type": "function",
        "function": {
            "name_for_human": "computer_use",
            "name": "computer_use",
            "description": description,
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "description": ACTION_DESCRIPTION,
                        "enum": [
                            "key",
                            "key_down",
                            "key_up",
                            "type",
                            "mouse_move",
                            "left_click",
                            "left_click_drag",
                            "right_click",
                            "middle_click",
                            "double_click",
                            "triple_click",
                            "scroll",
                            "hscroll",
                            "wait",
                            "terminate",
                        ],
                    },
                    "keys": {"type": "array", "items": {"type": "string"}},
                    "text": {"type": "string"},
                    "coordinate": {
                        "type": "array",
                        "items": {"type": "number"},
                        "minItems": 2,
                        "maxItems": 2,
                    },
                    "pixels": {"type": "number"},
                    "time": {"type": "number"},
                    "duration": {"type": "number"},
                    "status": {
                        "type": "string",
                        "enum": ["success", "failure"],
                    },
                },
                "required": ["action"],
            },
            "args_format": "Format arguments as a JSON object.",
        },
    }
    return SYSTEM_TEMPLATE.format(tools_xml=json.dumps(tool, ensure_ascii=False))
