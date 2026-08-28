#coding: utf-8

import os
import sys
import threading
import io
import asyncio
from contextlib import redirect_stdout
import base64
import json
import subprocess
import pyautogui
import pyperclip
from mcp.server.fastmcp import Image
import PIL
import requests
from .spatial_fusion import actionable_treeland_windows, build_action_targets, fuse_omniparser_with_treeland

INPUT_IMAGE_SIZE = 960


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

def mcp_autogui_main(mcp):
    omniparser_thread = None
    result_image = None
    detail = None
    fused_detail = None
    is_finished = False

    current_mouse_x, current_mouse_y = pyautogui.position()

    if 'OMNI_PARSER_SERVER' not in os.environ:
        raise RuntimeError('OMNI_PARSER_SERVER environment variable is required.')

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
