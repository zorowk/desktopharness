#coding: utf-8

import atexit
import os
import sys
import threading
import io
import asyncio
from copy import deepcopy
from contextlib import redirect_stdout
import base64
import json
import subprocess
import uuid
import pyautogui
import pyperclip
from mcp.server.fastmcp import Image
import PIL
import requests
from .qwen_actions import execute_parsed_actions, parse_qwen_actions, set_absolute_coordinate
from .qwen_backend import QwenBackendClient
from .spatial_fusion import (
    actionable_treeland_windows,
    build_action_targets,
    desktop_bounds_from_treeland,
    desktop_to_screenshot_point,
    fuse_omniparser_with_treeland,
    fuse_qwen_actions_with_treeland,
)

INPUT_IMAGE_SIZE = 960


def _qwen_precision_constraint(
    expected_action: str,
    expected_screenshot_coordinate: list[float] | None,
    coordinate_tolerance_px: float,
    screenshot_size: tuple[int, int],
) -> dict | None:
    """Build a controller-owned constraint for one Qwen mouse-move proposal."""
    action = expected_action.strip()
    if not action and expected_screenshot_coordinate is None:
        return None
    if action != "mouse_move":
        raise ValueError("expected_action must be 'mouse_move' when using a coordinate constraint")
    if not (
        isinstance(expected_screenshot_coordinate, list)
        and len(expected_screenshot_coordinate) == 2
        and all(
            isinstance(value, (int, float)) and not isinstance(value, bool)
            for value in expected_screenshot_coordinate
        )
    ):
        raise ValueError("expected_screenshot_coordinate must be a numeric [x, y] pair")
    if not isinstance(coordinate_tolerance_px, (int, float)) or isinstance(
        coordinate_tolerance_px, bool
    ):
        raise ValueError("coordinate_tolerance_px must be numeric")
    if not 0 <= coordinate_tolerance_px <= 20:
        raise ValueError("coordinate_tolerance_px must be between 0 and 20")

    width, height = screenshot_size
    x, y = (float(value) for value in expected_screenshot_coordinate)
    if not (0 <= x < width and 0 <= y < height):
        raise ValueError("expected_screenshot_coordinate is outside the current screenshot")
    normalized = {
        "x": round(x * 999 / (width - 1)),
        "y": round(y * 999 / (height - 1)),
    }
    return {
        "expected_action": action,
        "expected_screenshot_coordinate": {"x": x, "y": y},
        "qwen_normalized_coordinate": normalized,
        "coordinate_tolerance_px": float(coordinate_tolerance_px),
    }


def _qwen_constraint_prompt(constraint: dict) -> str:
    normalized = constraint["qwen_normalized_coordinate"]
    target = constraint["expected_screenshot_coordinate"]
    return (
        "Controller precision constraint (authoritative for this next step):\n"
        "- Return exactly action `mouse_move`.\n"
        "- Qwen coordinates use the normalized 0..999 space.\n"
        f"- Return coordinate [{normalized['x']}, {normalized['y']}] exactly.\n"
        f"- This is checked against screenshot pixel ({target['x']}, {target['y']})."
    )


def _qwen_constraint_violation(actions: list[dict], constraint: dict | None) -> str | None:
    if constraint is None:
        return None
    if len(actions) != 1 or actions[0].get("type") != "moveTo":
        return "expected exactly one Qwen mouse_move action"
    coordinate = actions[0].get("coordinate")
    if not isinstance(coordinate, dict):
        return "Qwen mouse_move action has no coordinate"
    target = constraint["expected_screenshot_coordinate"]
    tolerance = constraint["coordinate_tolerance_px"]
    if (
        abs(float(coordinate["x"]) - target["x"]) > tolerance
        or abs(float(coordinate["y"]) - target["y"]) > tolerance
    ):
        return (
            "Qwen coordinate does not satisfy the controller constraint: "
            f"expected screenshot ({target['x']}, {target['y']}) ± {tolerance}px, "
            f"got ({coordinate['x']}, {coordinate['y']})"
        )
    return None


def get_treeland_layout_tree(timeout=35):
    """Read Treeland's window tree from its built-in debug client."""
    result = subprocess.run(
        ["treeland-debug", "--tree"],
        check=True,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    output = result.stdout.strip()
    if not output:
        raise RuntimeError("treeland-debug --tree returned no window-tree data")
    try:
        return json.loads(output)
    except json.JSONDecodeError as exc:
        raise RuntimeError("treeland-debug --tree returned invalid JSON") from exc


def omniparser_bbox_center(bbox, screen_width, screen_height):
    xmin, ymin, xmax, ymax = bbox
    x = int((xmin + xmax) * screen_width) // 2
    y = int((ymin + ymax) * screen_height) // 2
    return x, y


def get_fused_window_by_id(fused_tree, window_id):
    if fused_tree is None:
        return None
    windows = actionable_treeland_windows(fused_tree)
    if window_id < 0 or window_id >= len(windows):
        return None
    return windows[window_id]


def window_region_center(window, region):
    geometry = window.get("geometry") or {}
    win_x = float(geometry.get("x") or 0)
    win_y = float(geometry.get("y") or 0)
    win_width = float(geometry.get("width") or 0)
    win_height = float(geometry.get("height") or 0)
    if win_width <= 0 or win_height <= 0:
        return None

    titlebar = window.get("titlebarGeometry") or {}
    titlebar_width = float(titlebar.get("width") or 0)
    titlebar_height = float(titlebar.get("height") or 0)

    if region == "titlebar":
        if titlebar_width > 0 and titlebar_height > 0:
            titlebar_x = win_x + float(titlebar.get("x") or 0)
            titlebar_y = win_y + float(titlebar.get("y") or 0)
            return int(titlebar_x + titlebar_width / 2), int(titlebar_y + titlebar_height / 2)
        fallback_height = min(40.0, max(1.0, win_height * 0.1))
        return int(win_x + win_width / 2), int(win_y + fallback_height / 2)

    if region == "content":
        content_y = win_y
        content_height = win_height
        if titlebar_width > 0 and titlebar_height > 0:
            content_y += titlebar_height
            content_height = max(1.0, win_height - titlebar_height)
        return int(win_x + win_width / 2), int(content_y + content_height / 2)

    if region == "center":
        return int(win_x + win_width / 2), int(win_y + win_height / 2)

    return None

def _env_enabled(name, default=False):
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def register_omniparser_tools(mcp):
    omniparser_thread = None
    result_image = None
    detail = None
    fused_detail = None
    is_finished = False

    current_mouse_x, current_mouse_y = pyautogui.position()

    if 'OMNI_PARSER_SERVER' not in os.environ:
        raise RuntimeError(
            'OMNI_PARSER_SERVER is required when GUI_OMNIPARSER_ENABLED is enabled.'
        )

    @mcp.tool()
    async def omniparser_details_on_screen() -> list:
        """Get the screen and analyze its details.
        If a timeout occurs, you can continue by running it again.

    Return value:
        - Details such as the content of text.
        - Screen capture with ID number added.
        """
        nonlocal omniparser_thread, result_image, detail, fused_detail, is_finished

        detail_text = ''
        with redirect_stdout(sys.stderr):
            def omniparser_thread_func():
                nonlocal result_image, detail, fused_detail, is_finished
                with redirect_stdout(sys.stderr):
                    screenshot_image = pyautogui.screenshot()

                    buffered = io.BytesIO()
                    screenshot_image.save(buffered, format='png')
                    send_img = base64.b64encode(buffered.getvalue()).decode('ascii')
                    json_data = json.dumps({'base64_image': send_img})
                    response = requests.post(
                        f"http://{os.environ['OMNI_PARSER_SERVER']}/parse/",
                        data=json_data,
                        headers={"Content-Type": "application/json"}
                    )
                    response_json = response.json()
                    dino_labled_img = response_json['som_image_base64']
                    detail = response_json['parsed_content_list']
                    image_bytes = base64.b64decode(dino_labled_img)
                    result_image_local = PIL.Image.open(io.BytesIO(image_bytes))

                    width, height = result_image_local.size
                    if width > height:
                        result_image_local = result_image_local.resize((INPUT_IMAGE_SIZE, INPUT_IMAGE_SIZE * height // width))
                    else:
                        result_image_local = result_image_local.resize((INPUT_IMAGE_SIZE * width // height, INPUT_IMAGE_SIZE))

                    result_image = io.BytesIO()
                    result_image_local.save(result_image, format='png')

                    is_finished = True
            if omniparser_thread is None:
                result_image = None
                detail = None
                fused_detail = None
                is_finished = False
                omniparser_thread = threading.Thread(target=omniparser_thread_func)
                omniparser_thread.start()

            while not is_finished:
                await asyncio.sleep(0.1)

            omniparser_thread = None

            fusion_error = None
            try:
                treeland_tree = get_treeland_layout_tree()
            except Exception as exc:
                fusion_error = f"{type(exc).__name__}: {exc}"
                print(f"Treeland tree fetch failed: {fusion_error}", file=sys.stderr)
                treeland_tree = None

            if treeland_tree is not None:
                try:
                    fused_detail = fuse_omniparser_with_treeland(detail, treeland_tree)
                    stats = fused_detail.get("fusion_stats", {})
                    print(
                        "Treeland fusion: "
                        f"assigned {stats.get('assigned_elements', 0)} / {stats.get('total_elements', 0)} "
                        f"elements, unassigned {stats.get('unassigned_elements', 0)}, "
                        f"windows {stats.get('window_count', 0)}",
                        file=sys.stderr,
                    )
                    detail_text += json.dumps(build_action_targets(fused_detail), ensure_ascii=False, indent=2)
                except Exception as exc:
                    fusion_error = f"{type(exc).__name__}: {exc}"

            if fusion_error is not None:
                print(f"Treeland fusion failed: {fusion_error}", file=sys.stderr)
                detail_text += f'\nTreeland OmniParser fusion failed: {fusion_error}\n'

            # Save result image to /tmp only when debug mode is enabled
            if os.environ.get('OMNIPARSER_MCP_DEBUG') == '1':
                with open('/tmp/omniparser_mark.png', 'wb') as f:
                    f.write(result_image.getvalue())

                with open('/tmp/omniparser_mark.json', 'w', encoding='utf-8') as f:
                    json.dump(detail, f, ensure_ascii=False, indent=2)

                with open('/tmp/omniparser_detail_text.txt', 'w', encoding='utf-8') as f:
                    f.write(detail_text)

                if fused_detail is not None:
                    with open('/tmp/omniparser_fused_windowtree.json', 'w', encoding='utf-8') as f:
                        json.dump(fused_detail, f, ensure_ascii=False, indent=2)

            return [detail_text, Image(data=result_image.getvalue(), format="png")]

    @mcp.tool()
    async def omniparser_click(id: int, button: str = 'left', clicks: int = 1) -> bool:
        """Click on anything on the screen.

    Args:
        id: The element on the screen that it click. You can check it with "omniparser_details_on_screen".
        button: Button to click. 'left', 'middle', or 'right'.
        clicks: Number of clicks. 2 for double click.
    Return value:
        True is success. False is means "this is not found".
        """
        nonlocal current_mouse_x, current_mouse_y
        screen_width, screen_height = pyautogui.size()
        if len(detail) > id:
            compos = detail[id]['bbox']
            current_mouse_x, current_mouse_y = omniparser_bbox_center(compos, screen_width, screen_height)
            pyautogui.click(x=current_mouse_x, y=current_mouse_y, button=button, clicks=clicks)
            return True
        return False

    @mcp.tool()
    async def omniparser_drags(from_id: int, to_id: int, button: str = 'left', key: str = '') -> bool:
        """Drag and drop on the screen.

    Args:
        from_id: The element on the screen that it start to drag. You can check it with "omniparser_details_on_screen".
        to_id: The element on the screen that it end to drag. You can check it with "omniparser_details_on_screen".
        button: Button to click. 'left', 'middle', or 'right'.
        key: The name of the keyboard key if you hold down it while dragging. You can check key's name with "omniparser_get_keys_list".
    Return value:
        True is success. False is means "this is not found".
        """
        nonlocal current_mouse_x, current_mouse_y
        screen_width, screen_height = pyautogui.size()

        from_x = -1
        to_x = -1
        if len(detail) <= from_id or len(detail) <= to_id:
            return False
        compos = detail[from_id]['bbox']
        from_x, from_y = omniparser_bbox_center(compos, screen_width, screen_height)
        compos = detail[to_id]['bbox']
        to_x, to_y = omniparser_bbox_center(compos, screen_width, screen_height)

        if key is not None and key != '':
            pyautogui.keyDown(key)
        pyautogui.moveTo(from_x, from_y)
        pyautogui.dragTo(to_x, to_y, button=button)
        if key is not None and key != '':
            pyautogui.keyUp(key)
        current_mouse_x = to_x
        current_mouse_y = to_y
        return True

    @mcp.tool()
    async def omniparser_mouse_move(id: int) -> bool:
        """Moves the mouse cursor over the specified element.

    Args:
        id: The element on the screen that it move. You can check it with "omniparser_details_on_screen".
    Return value:
        True is success. False is means "this is not found".
        """
        nonlocal current_mouse_x, current_mouse_y
        screen_width, screen_height = pyautogui.size()
        if len(detail) <= id:
            return False
        compos = detail[id]['bbox']
        current_mouse_x, current_mouse_y = omniparser_bbox_center(compos, screen_width, screen_height)
        pyautogui.moveTo(current_mouse_x, current_mouse_y)
        return True

    @mcp.tool()
    async def omniparser_click_window_region(
        window_id: int,
        region: str = 'titlebar',
        button: str = 'left',
        clicks: int = 1,
    ) -> bool:
        """Click a named region of a Treeland window from omniparser_details_on_screen.

    Args:
        window_id: The window_id shown in "Treeland OmniParser action targets".
        region: 'titlebar', 'content', or 'center'.
        button: Button to click. 'left', 'middle', or 'right'.
        clicks: Number of clicks. 2 for double click.
    Return value:
        True is success. False means the window or region is not found.
        """
        nonlocal current_mouse_x, current_mouse_y
        window = get_fused_window_by_id(fused_detail, window_id)
        if window is None:
            return False
        center = window_region_center(window, region)
        if center is None:
            return False
        current_mouse_x, current_mouse_y = center
        pyautogui.click(x=current_mouse_x, y=current_mouse_y, button=button, clicks=clicks)
        return True

    @mcp.tool()
    async def omniparser_drag_window_region(
        window_id: int,
        delta_x: int,
        delta_y: int,
        region: str = 'titlebar',
        button: str = 'left',
    ) -> bool:
        """Drag a named region of a Treeland window by a relative pixel offset.

    Args:
        window_id: The window_id shown in "Treeland OmniParser action targets".
        delta_x: Horizontal drag offset in pixels. Positive moves right.
        delta_y: Vertical drag offset in pixels. Positive moves down.
        region: Usually 'titlebar' for moving a window.
        button: Button to hold while dragging. Usually 'left'.
    Return value:
        True is success. False means the window or region is not found.
        """
        nonlocal current_mouse_x, current_mouse_y
        window = get_fused_window_by_id(fused_detail, window_id)
        if window is None:
            return False
        center = window_region_center(window, region)
        if center is None:
            return False
        from_x, from_y = center
        to_x = from_x + delta_x
        to_y = from_y + delta_y
        pyautogui.moveTo(from_x, from_y)
        pyautogui.dragTo(to_x, to_y, button=button)
        current_mouse_x = to_x
        current_mouse_y = to_y
        return True

    @mcp.tool()
    async def omniparser_scroll(clicks: int) -> None:
        """The mouse scrolling wheel behavior.

    CRITICAL: Before scrolling, ensure the target window is focused.
        It is highly recommended to click the window title bar or the target area first.
    Args:
        clicks: Amount of scrolling. 1000 is scroll up 1000 "clicks" and -1000 is scroll down 1000 "clicks".
        """
        pyautogui.moveTo(current_mouse_x, current_mouse_y)
        pyautogui.scroll(clicks)

    @mcp.tool()
    async def omniparser_write(content: str, id: int = -1) -> None:
        """Type the characters in the string that is passed.

    IMPORTANT: A window must be active to receive text input.
        If 'id' is provided, this tool will click the element to focus it.
        If 'id' is -1, you MUST ensure the target window/input box is already focused
        (e.g., by clicking it in a previous step).

    Args:
        content: What to enter.
        id: Click on the target before typing. You can check it with "omniparser_details_on_screen".
        """
        if id >= 0:
            await omniparser_click(id)
        else:
            pyautogui.moveTo(current_mouse_x, current_mouse_y)
        if content.isascii():
            pyautogui.write(content)
        else:
            prev_clip = pyperclip.paste()
            pyperclip.copy(content)
            pyautogui.hotkey('ctrl', 'v')
            if prev_clip:
                pyperclip.copy(prev_clip)

    @mcp.tool()
    async def omniparser_get_keys_list() -> list[str]:
        """List of keyboard keys. Used in "omniparser_input_key" etc.

    Return value:
        List of keyboard keys.
        """
        return pyautogui.KEYBOARD_KEYS

    @mcp.tool()
    async def omniparser_input_key(key1: str, key2: str = '', key3: str = '') -> None:
        """Press of keyboard keys.

    CRITICAL: Shortcuts (like 'ctrl'+'c') only work if the target window is active.
        Ensure you have clicked the target window to focus it before calling this.

    Args:
        key1-3: Press of keyboard keys. You can check key's name with "omniparser_get_keys_list". If you specify multiple, keys will be pressed down in order, and then released in reverse order.
        """
        pyautogui.moveTo(current_mouse_x, current_mouse_y)
        if key2 is not None and key2 != '' and key3 is not None and key3 != '':
            pyautogui.hotkey(key1, key2, key3)
        elif key2 is not None and key2 != '':
            pyautogui.hotkey(key1, key2)
        else:
            pyautogui.hotkey(key1)

    @mcp.tool()
    async def omniparser_wait(time: float = 1.0) -> None:
        """Waits for the specified number of seconds.

    Args:
        time: Waiting time (seconds).
        """
        await asyncio.sleep(time)


def mcp_autogui_main(mcp):
    qwen_backend = QwenBackendClient()
    backend_close = getattr(qwen_backend, "close", None)
    if callable(backend_close):
        atexit.register(backend_close)
    qwen_sessions = {}
    qwen_sessions_lock = threading.RLock()

    def record_qwen_execution(session_id, status, execution=None, reason=None):
        recorder = getattr(qwen_backend, "record_execution", None)
        if not callable(recorder):
            return {"ok": False, "committed": False, "message": "feedback unsupported"}
        try:
            return recorder(
                session_id,
                status=status,
                execution=execution,
                reason=reason,
            )
        except Exception as exc:
            return {
                "ok": False,
                "committed": False,
                "message": f"{type(exc).__name__}: {exc}",
            }

    def capture_qwen_frame():
        screenshot = pyautogui.screenshot().convert("RGB")
        screenshot_buffer = io.BytesIO()
        screenshot.save(screenshot_buffer, format="PNG")
        tree = get_treeland_layout_tree()
        return screenshot_buffer.getvalue(), screenshot.size, tree

    @mcp.tool()
    async def qwen_cua_predict(
        instruction: str,
        session_id: str = '',
        reset: bool = False,
        expected_action: str = '',
        expected_screenshot_coordinate: list[float] | None = None,
        coordinate_tolerance_px: float = 2.0,
    ) -> list:
        """Ask Qwen-CUA for one GUI step and fuse its coordinates with Treeland.

    This tool does not execute the returned actions. Inspect the deterministic
    Treeland target and validation data, then call qwen_cua_execute.

    Args:
        instruction: The bounded GUI task for the current Qwen-CUA session.
        session_id: Reuse the returned ID for subsequent steps of the same task.
        reset: Reset the backend session before this prediction.
        expected_action: Optional controller constraint; currently supports
            ``mouse_move`` only.
        expected_screenshot_coordinate: Optional screenshot-pixel target for
            the constrained Qwen action. It is converted to Qwen's native
            0..999 coordinate space and checked after prediction.
        coordinate_tolerance_px: Allowed screenshot-pixel error for the
            constrained result (0 to 20).
        """
        normalized_instruction = instruction.strip()
        if not normalized_instruction:
            raise ValueError("instruction must not be empty")
        resolved_session_id = session_id.strip() or f"treeland-autoui-{uuid.uuid4().hex}"

        with qwen_sessions_lock:
            existing_state = qwen_sessions.get(resolved_session_id)
        if existing_state and not existing_state.get("execution_complete"):
            if not existing_state.get("requires_reset"):
                raise RuntimeError(
                    "The previous Qwen-CUA prediction is still pending; execute it or reset the session"
                )
        instruction_changed = bool(
            existing_state
            and existing_state.get("instruction") != normalized_instruction
        )
        state_requires_reset = bool(existing_state and existing_state.get("requires_reset"))
        if reset or instruction_changed or state_requires_reset:
            await asyncio.to_thread(qwen_backend.reset, resolved_session_id)
            with qwen_sessions_lock:
                qwen_sessions.pop(resolved_session_id, None)

        screenshot_bytes, screenshot_size, treeland_tree = await asyncio.to_thread(
            capture_qwen_frame
        )
        constraint = _qwen_precision_constraint(
            expected_action,
            expected_screenshot_coordinate,
            coordinate_tolerance_px,
            screenshot_size,
        )
        model_instruction = normalized_instruction
        if constraint is not None:
            model_instruction = f"{normalized_instruction}\n\n{_qwen_constraint_prompt(constraint)}"
        with qwen_sessions_lock:
            previous = qwen_sessions.get(resolved_session_id) or {}
            client_step = int(previous.get("step", 0)) + 1

        backend_result = await asyncio.to_thread(
            qwen_backend.predict,
            model_instruction,
            screenshot_bytes,
            resolved_session_id,
            image_mime="image/png",
            client_step=client_step,
            session_instruction=normalized_instruction,
        )
        try:
            parsed_actions = parse_qwen_actions(backend_result.get("actions", []))
            violation = _qwen_constraint_violation(parsed_actions, constraint)
            if violation is not None:
                raise ValueError(violation)
        except Exception as exc:
            await asyncio.to_thread(
                record_qwen_execution,
                resolved_session_id,
                "rejected",
                {
                    "constraint": constraint,
                    "proposed_actions": backend_result.get("actions", []),
                },
                f"Proposal validation rejected: {type(exc).__name__}: {exc}",
            )
            raise ValueError(f"Qwen proposal rejected by controller validation: {exc}") from exc
        fused = fuse_qwen_actions_with_treeland(
            parsed_actions,
            treeland_tree,
            screenshot_size,
        )
        frame_id = f"frame-{uuid.uuid4().hex}"
        with qwen_sessions_lock:
            qwen_sessions[resolved_session_id] = {
                "session_id": resolved_session_id,
                "instruction": normalized_instruction,
                "step": client_step,
                "frame_id": frame_id,
                "screenshot_size": screenshot_size,
                "parsed_actions": parsed_actions,
                "fused": fused,
                "execution_complete": False,
                "requires_reset": False,
            }

        response = {
            "session_id": resolved_session_id,
            "frame_id": frame_id,
            "step": client_step,
            "instruction": normalized_instruction,
            "coordinate_constraint": constraint,
            "agent_type": backend_result.get("agent_type"),
            "observation": backend_result.get("observation_text", ""),
            "action_text": backend_result.get("action_text", ""),
            "assistant_output": backend_result.get("assistant_output", ""),
            "fused_actions": fused,
            "telemetry": backend_result.get("telemetry", {}),
        }
        return [
            json.dumps(response, ensure_ascii=False, indent=2),
            Image(data=screenshot_bytes, format="png"),
        ]

    @mcp.tool()
    async def qwen_cua_execute(
        session_id: str,
        action_indexes: list[int] | None = None,
    ) -> dict:
        """Execute selected actions from the latest Qwen-CUA prediction.

    Actions are parsed through a strict allowlist. Coordinate actions are
    refused when the latest Treeland tree no longer resolves to the same target
    window as the prediction frame.

    Args:
        session_id: ID returned by qwen_cua_predict.
        action_indexes: Fused action indexes to execute; omit to execute all.
        """
        with qwen_sessions_lock:
            state = qwen_sessions.get(session_id)
        if state is None:
            raise ValueError("No pending Qwen-CUA prediction for this session_id")

        parsed_actions = state["parsed_actions"]
        if action_indexes is not None:
            if any(not isinstance(index, int) or isinstance(index, bool) for index in action_indexes):
                raise ValueError("action_indexes must contain integers")
            if any(index < 0 or index >= len(parsed_actions) for index in action_indexes):
                raise ValueError("action_indexes contains an out-of-range index")

        latest_tree = await asyncio.to_thread(get_treeland_layout_tree)
        latest_desktop_bounds = desktop_bounds_from_treeland(latest_tree)
        latest_fused = fuse_qwen_actions_with_treeland(
            parsed_actions,
            latest_tree,
            state["screenshot_size"],
        )
        selected = set(action_indexes) if action_indexes is not None else set(range(len(parsed_actions)))
        refusals = []
        original_fused_actions = state["fused"]["actions"]
        latest_fused_actions = latest_fused["actions"]
        for index in sorted(selected):
            action = parsed_actions[index]
            if action.get("coordinate") is None:
                continue
            original_target = original_fused_actions[index].get("target_window")
            latest_target = latest_fused_actions[index].get("target_window")
            if original_target is None or latest_target is None:
                refusals.append({"action_index": index, "reason": "target_window_missing"})
                continue
            identity_keys = ("appId", "title", "container", "workspace")
            if any(original_target.get(key) != latest_target.get(key) for key in identity_keys):
                refusals.append(
                    {
                        "action_index": index,
                        "reason": "target_window_changed",
                        "predicted_target": original_target,
                        "current_target": latest_target,
                    }
                )

        if refusals:
            backend_feedback = await asyncio.to_thread(
                record_qwen_execution,
                session_id,
                "rejected",
                {
                    "frame_id": state["frame_id"],
                    "selected_action_indexes": sorted(selected),
                    "refusals": refusals,
                },
                "Treeland target validation rejected the proposal",
            )
            can_continue = bool(backend_feedback.get("ok"))
            with qwen_sessions_lock:
                current_state = qwen_sessions.get(session_id)
                if current_state is state:
                    current_state["execution_complete"] = can_continue
                    current_state["requires_reset"] = not can_continue
            return {
                "session_id": session_id,
                "frame_id": state["frame_id"],
                "status": "refused",
                "refusals": refusals,
                "backend_feedback": backend_feedback,
                "session_continuable": can_continue,
                "guidance": (
                    "Call qwen_cua_predict again with this session; the embedded backend received the rejection."
                    if can_continue
                    else "Reset this session, or call qwen_cua_predict again to auto-reset it."
                ),
            }

        execution_actions = deepcopy(parsed_actions)
        for index in sorted(selected):
            original = original_fused_actions[index]
            latest = latest_fused_actions[index]
            if original.get("desktop_coordinate") is None:
                continue
            original_target = original.get("target_window") or {}
            latest_target = latest.get("target_window") or {}
            relative = original_target.get("window_relative_coordinate")
            latest_geometry = latest_target.get("geometry") or {}
            if isinstance(relative, dict) and latest_geometry:
                execution_coordinate = {
                    "x": float(latest_geometry.get("x") or 0) + float(relative.get("x") or 0),
                    "y": float(latest_geometry.get("y") or 0) + float(relative.get("y") or 0),
                }
            else:
                execution_coordinate = latest["desktop_coordinate"]
            input_coordinate = desktop_to_screenshot_point(
                execution_coordinate,
                state["screenshot_size"][0],
                state["screenshot_size"][1],
                latest_desktop_bounds,
            )
            set_absolute_coordinate(execution_actions[index], input_coordinate)

        execution_results = await asyncio.to_thread(
            execute_parsed_actions,
            execution_actions,
            pyautogui,
            action_indexes=action_indexes,
        )
        try:
            cursor_x, cursor_y = await asyncio.to_thread(pyautogui.position)
            post_action_cursor = {"x": float(cursor_x), "y": float(cursor_y)}
            post_action_cursor_error = None
        except Exception as exc:
            post_action_cursor = None
            post_action_cursor_error = f"{type(exc).__name__}: {exc}"
        post_tree = None
        post_tree_error = None
        try:
            post_tree = await asyncio.to_thread(get_treeland_layout_tree)
        except Exception as exc:
            post_tree_error = f"{type(exc).__name__}: {exc}"
        all_actions_selected = selected == set(range(len(parsed_actions)))
        all_actions_succeeded = bool(execution_results) and all(
            item.get("status") == "success" for item in execution_results
        )
        if all_actions_selected and all_actions_succeeded:
            feedback_status = "success"
        elif all_actions_succeeded:
            feedback_status = "partial"
        else:
            feedback_status = "error"
        backend_feedback = await asyncio.to_thread(
            record_qwen_execution,
            session_id,
            feedback_status,
            {
                "frame_id": state["frame_id"],
                "selected_action_indexes": sorted(selected),
                "actual_actions": [execution_actions[index] for index in sorted(selected)],
                "results": execution_results,
                "post_action_cursor": post_action_cursor,
                "post_action_cursor_error": post_action_cursor_error,
                "post_tree_error": post_tree_error,
            },
            None if all_actions_succeeded else "One or more actions failed",
        )
        feedback_recorded = bool(backend_feedback.get("ok"))
        with qwen_sessions_lock:
            current_state = qwen_sessions.get(session_id)
            if current_state is state:
                current_state["last_execution"] = execution_results
                current_state["execution_complete"] = (
                    feedback_recorded or (all_actions_selected and all_actions_succeeded)
                )
                current_state["requires_reset"] = not current_state["execution_complete"]

        execution_succeeded = bool(execution_results) and all(
            item.get("status") == "success" for item in execution_results
        )
        return {
            "session_id": session_id,
            "frame_id": state["frame_id"],
            "status": (
                "success"
                if all_actions_selected and execution_succeeded
                else "partial"
                if execution_succeeded
                else "error"
            ),
            "session_continuable": bool(
                feedback_recorded
                or (execution_succeeded and selected == set(range(len(parsed_actions))))
            ),
            "execution_results": execution_results,
            "executed_actions": [execution_actions[index] for index in sorted(selected)],
            "post_action_cursor": post_action_cursor,
            "post_action_cursor_error": post_action_cursor_error,
            "backend_feedback": backend_feedback,
            "post_action_windows": (
                len(actionable_treeland_windows(post_tree)) if post_tree is not None else None
            ),
            "post_tree_error": post_tree_error,
            "next": (
                "Call qwen_cua_predict with the same session_id for the next step."
                if feedback_recorded
                or (execution_succeeded and selected == set(range(len(parsed_actions))))
                else "The next prediction will reset this session because execution was partial or failed."
            ),
        }

    @mcp.tool()
    async def qwen_cua_reset(session_id: str) -> bool:
        """Reset a Qwen-CUA backend session and discard its pending local frame."""
        await asyncio.to_thread(qwen_backend.reset, session_id)
        with qwen_sessions_lock:
            qwen_sessions.pop(session_id, None)
        return True

    @mcp.tool()
    async def qwen_cua_status() -> dict:
        """Check Qwen-CUA backend connectivity and local pending sessions."""
        health = await asyncio.to_thread(qwen_backend.health)
        with qwen_sessions_lock:
            sessions = [
                {
                    "session_id": value["session_id"],
                    "instruction": value["instruction"],
                    "step": value["step"],
                    "frame_id": value["frame_id"],
                }
                for value in qwen_sessions.values()
            ]
        return {"backend": health, "pending_sessions": sessions}

    if _env_enabled("GUI_OMNIPARSER_ENABLED"):
        register_omniparser_tools(mcp)
