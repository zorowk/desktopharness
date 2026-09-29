"""Single source of truth for Qwen-CUA actions supported by AutoUI v2.

The embedded Qwen prompt, its ``computer_use`` decoder, the legacy action
parser, and the v2 proposal adapter must agree on these finite sets.  Keeping
them here prevents a model from being invited to emit an action the v2
transaction protocol cannot represent.
"""

from __future__ import annotations


# Actions exposed in the model-facing ``computer_use`` schema.  ``wait`` is a
# bounded controller action: it consumes the normal step budget and is followed
# by a fresh observation.
COMPUTER_USE_ACTIONS = (
    "wait",
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
    "terminate",
)


# Parsed legacy Qwen action types that have a canonical v2 Action mapping.
V2_PARSED_QWEN_ACTIONS = frozenset(
    {
        "moveTo",
        "click",
        "rightClick",
        "middleClick",
        "doubleClick",
        "tripleClick",
        "dragTo",
        "moveRel",
        "dragRel",
        "scroll",
        "hscroll",
        "press",
        "keyDown",
        "keyUp",
        "hotkey",
        "typewrite",
        "write",
        "mouseDown",
        "mouseUp",
        "sleep",
        "done",
    }
)
