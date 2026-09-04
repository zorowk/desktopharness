#coding: utf-8

import atexit
import os
import sys
import time
import threading
import io
import asyncio
from copy import deepcopy
from contextlib import redirect_stdout
import base64
import json
import subprocess
import pyautogui
import pyperclip
from mcp.server.fastmcp import Image
import PIL
import requests
from .qwen_backend import QwenBackendClient
from .adapters.evidence import CompositorWindowEvidenceProvider, OmniParserEvidenceProvider
from .adapters.executor import PyAutoGUIExecutor
from .adapters.frame import PyAutoGUIFrameProvider
from .adapters.proposal import QwenCUAProposalProvider
from .core.models import (
    Action,
    ActionProposal,
    ActionType,
    AssertionSpec,
    Point,
    TaskContract,
    TaskLimits,
    TaskPermissions,
    new_id,
)
from .core.orchestrator import CoreOrchestrator
from .core.audit import audit_components_from_environment
from .desktop_backend import DEFAULT_DESKTOP_BACKEND, create_desktop_backend
from .facade import GuiRunFacade
from .desktop_capabilities import (
    find_capability,
    load_desktop_application_catalogue,
    load_keybinding_catalogue,
    validate_application_id,
)
from .spatial_fusion import (
    actionable_treeland_windows,
    build_action_targets,
    desktop_bounds_from_treeland,
    flatten_treeland_windows,
    fuse_omniparser_with_treeland,
    screenshot_to_desktop_point,
)

INPUT_IMAGE_SIZE = 960
DEFAULT_APPLICATION_WAIT_TIMEOUT_S = 3.0
MAX_APPLICATION_WAIT_TIMEOUT_S = 30.0
APPLICATION_WAIT_POLL_INTERVAL_S = 0.2
WINDOW_RESIZE_HANDLE_PX = 12.0


def _expected_active_app_id(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("expected_active_app_id must be a string")
    return value.strip()


def _application_wait_timeout(value: float) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ValueError("application_wait_timeout_s must be numeric")
    timeout = float(value)
    if not 0 <= timeout <= MAX_APPLICATION_WAIT_TIMEOUT_S:
        raise ValueError(
            "application_wait_timeout_s must be between 0 and "
            f"{MAX_APPLICATION_WAIT_TIMEOUT_S:g}"
        )
    return timeout


def _capture_post_action_frame(
    capture_frame,
    read_tree,
    expected_app_id: str,
    timeout_s: float,
) -> tuple[bytes | None, tuple[int, int] | None, dict | None, dict]:
    """Poll the lightweight tree, then capture one final post-action frame."""
    started = time.monotonic()
    deadline = started + timeout_s
    attempts = 0
    poll_error = None
    observed_tree = None

    if expected_app_id:
        while True:
            attempts += 1
            try:
                observed_tree = read_tree()
                poll_error = None
            except Exception as exc:
                poll_error = f"{type(exc).__name__}: {exc}"

            active_window = (
                _active_window_summary(observed_tree)
                if observed_tree is not None
                else None
            )
            actual_app_id = (active_window or {}).get("appId")
            if actual_app_id == expected_app_id:
                break

            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            time.sleep(min(APPLICATION_WAIT_POLL_INTERVAL_S, remaining))

    observation_error = None
    try:
        latest_frame = capture_frame()
    except Exception as exc:
        observation_error = f"{type(exc).__name__}: {exc}"
        latest_frame = (None, None, observed_tree)
    if attempts == 0:
        attempts = 1

    waited_ms = round((time.monotonic() - started) * 1000, 2)
    tree = latest_frame[2]
    active_window = _active_window_summary(tree) if tree is not None else None
    actual_app_id = (active_window or {}).get("appId")
    if not expected_app_id:
        status = "not-requested"
    elif actual_app_id == expected_app_id:
        status = "matched"
    elif tree is None:
        status = "observation-unavailable"
    else:
        status = "timeout"
    return (
        latest_frame[0],
        latest_frame[1],
        latest_frame[2],
        {
            "status": status,
            "expected_active_app_id": expected_app_id or None,
            "actual_active_app_id": actual_app_id,
            "attempts": attempts,
            "waited_ms": waited_ms,
            "poll_error": poll_error,
            "observation_error": observation_error,
        },
    )


def _active_app_task_validation(
    expected_app_id: str,
    active_before: dict | None,
    active_after: dict | None,
    application_wait: dict,
) -> dict | None:
    """Evaluate the lightweight active-app postcondition for one session."""
    if not expected_app_id:
        return None
    before_app_id = (active_before or {}).get("appId")
    actual_app_id = (active_after or {}).get("appId")
    if actual_app_id == expected_app_id:
        status = "passed"
        reason = None
    elif active_after is None:
        status = "unknown"
        reason = "active_window_unavailable"
    elif actual_app_id and actual_app_id != before_app_id:
        status = "failed"
        reason = "wrong_application_active"
    else:
        status = "failed"
        reason = "expected_application_not_observed"
    return {
        "assertion": "active_window.appId == expected_active_app_id",
        "status": status,
        "reason": reason,
        "expected_active_app_id": expected_app_id,
        "actual_active_app_id": actual_app_id,
        "active_window_before": deepcopy(active_before),
        "active_window_after": deepcopy(active_after),
        "attempts": application_wait.get("attempts"),
        "waited_ms": application_wait.get("waited_ms"),
    }


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


def _active_window_summary(tree):
    """Return a compact identity summary of the active window in a tree."""
    for window in actionable_treeland_windows(tree):
        if window.get("active") is True:
            return {
                "appId": window.get("appId"),
                "title": window.get("title"),
                "container": window.get("container"),
                "workspace": window.get("workspace"),
            }
    return None


def _matching_windows_by_identity(tree, identity):
    """Return all windows matching a non-unique controller identity."""
    return [
        window
        for window in flatten_treeland_windows(tree)
        if all(window.get(key) == value for key, value in identity.items())
    ]


def _point_in_window_titlebar(point, window):
    """Whether a desktop point is in Treeland's titlebar rectangle.

    Titlebar coordinates are relative to the toplevel window.  We require
    explicit non-empty metadata: guessing a titlebar height risks treating an
    editor tab as a safe drag source.
    """
    geometry = window.get("geometry") or {}
    titlebar = window.get("titlebarGeometry") or {}
    width = float(titlebar.get("width") or 0)
    height = float(titlebar.get("height") or 0)
    if width <= 0 or height <= 0:
        return False
    left = float(geometry.get("x") or 0) + float(titlebar.get("x") or 0)
    top = float(geometry.get("y") or 0) + float(titlebar.get("y") or 0)
    return left <= point["x"] < left + width and top <= point["y"] < top + height


def _point_on_window_resize_handle(point, window):
    """Return whether a point is on a toplevel's outer resize border."""
    geometry = window.get("geometry") or {}
    left = float(geometry.get("x") or 0)
    top = float(geometry.get("y") or 0)
    width = float(geometry.get("width") or 0)
    height = float(geometry.get("height") or 0)
    if width <= 0 or height <= 0:
        return False
    right = left + width
    bottom = top + height
    x, y = point["x"], point["y"]
    within_handle_area = (
        left - WINDOW_RESIZE_HANDLE_PX <= x <= right + WINDOW_RESIZE_HANDLE_PX
        and top - WINDOW_RESIZE_HANDLE_PX <= y <= bottom + WINDOW_RESIZE_HANDLE_PX
    )
    return within_handle_area and (
        abs(x - left) <= WINDOW_RESIZE_HANDLE_PX
        or abs(x - right) <= WINDOW_RESIZE_HANDLE_PX
        or abs(y - top) <= WINDOW_RESIZE_HANDLE_PX
        or abs(y - bottom) <= WINDOW_RESIZE_HANDLE_PX
    )


def _window_manager_drag_to(action, pyautogui_module, screenshot_size):
    """Safely interpret dragTo as a window move or edge resize.

    A titlebar source uses WM move mode.  A source on an outer border keeps
    the native dragTo down/move/up sequence, which the compositor interprets
    as resize.  Content and tab-strip sources are rejected.
    """
    target = action.get("coordinate")
    if not isinstance(target, dict):
        raise ValueError("dragTo requires an absolute destination")
    source_x, source_y = pyautogui_module.position()
    tree = get_treeland_layout_tree()
    bounds = desktop_bounds_from_treeland(tree)
    source_desktop = screenshot_to_desktop_point(
        {"x": float(source_x), "y": float(source_y)},
        screenshot_size[0], screenshot_size[1], bounds,
    )
    active_windows = [
        window for window in flatten_treeland_windows(tree) if window.get("active") is True
    ]
    if len(active_windows) != 1:
        raise ValueError("drag_active_window_ambiguous")
    window = active_windows[0]
    identity = {
        key: window.get(key)
        for key in ("appId", "title", "container", "workspace")
    }
    matches = _matching_windows_by_identity(tree, identity)
    if len(matches) != 1:
        raise ValueError("drag_source_window_ambiguous")

    destination_x = float(target["x"])
    destination_y = float(target["y"])
    screen_width, screen_height = pyautogui_module.size()
    if not (0 <= destination_x < screen_width and 0 <= destination_y < screen_height):
        raise ValueError("drag_destination_outside_screen")

    before_geometry = deepcopy(window.get("geometry") or {})
    if _point_in_window_titlebar(source_desktop, window):
        kind = "wm_window_move"
        pyautogui_module.hotkey("alt", "f7")
        pyautogui_module.moveTo(destination_x, destination_y)
        # In WM move mode this confirms the new anchor without delivering the
        # click to an application tab or content area.
        pyautogui_module.click(destination_x, destination_y)
    elif _point_on_window_resize_handle(source_desktop, window):
        kind = "native_window_resize"
        pyautogui_module.dragTo(*action.get("args", []), **action.get("kwargs", {}))
    else:
        raise ValueError("drag_source_not_on_titlebar_or_resize_border")
    return {
        "kind": kind,
        "source_window_identity": identity,
        "source_geometry_before": before_geometry,
        "source_screenshot_coordinate": {"x": float(source_x), "y": float(source_y)},
        "destination_screenshot_coordinate": {
            "x": destination_x,
            "y": destination_y,
        },
    }

def _env_enabled(name, default=False):
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def register_omniparser_tools(mcp):
    raise RuntimeError(
        "legacy OmniParser direct execution tools are removed; "
        "use OmniParserEvidenceProvider through gui_run evaluation instead"
    )

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


def mcp_autogui_main(mcp, *, desktop_backend_kind: str = DEFAULT_DESKTOP_BACKEND):
    qwen_backend = QwenBackendClient()
    backend_close = getattr(qwen_backend, "close", None)
    if callable(backend_close):
        atexit.register(backend_close)
    store, ledger = audit_components_from_environment()
    desktop_backend = create_desktop_backend(
        desktop_backend_kind,
        tree_reader=lambda: get_treeland_layout_tree(),
        cursor_reader=pyautogui.position,
        artifact_store=store,
        capability_loader=lambda: load_keybinding_catalogue(),
        capability_resolver=lambda capability_id: find_capability(capability_id),
    )
    compositor = desktop_backend.compositor

    def coordinate_mapper(point, coordinate_space, proposal):
        if coordinate_space != "desktop-logical":
            raise ValueError("unsupported executor coordinate space")
        current = store.require(proposal.based_on_snapshot)
        width, height = pyautogui.size()
        bounds = current.coordinate_space.bounds
        return Point(
            (point.x - bounds.x) * float(width) / bounds.width,
            (point.y - bounds.y) * float(height) / bounds.height,
        )

    def drag_handler(proposal, point):
        width, height = pyautogui.size()
        result = _window_manager_drag_to(
            {
                "coordinate": {"x": point.x, "y": point.y},
                "args": [point.x, point.y],
                "kwargs": {
                    "duration": proposal.action.parameters.get("duration", 0.5),
                    "button": proposal.action.parameters.get("button", "left"),
                },
            },
            pyautogui,
            (int(width), int(height)),
        )
        store.put(result, prefix="window-gesture")
        return True

    evidence_providers = [CompositorWindowEvidenceProvider()]
    if _env_enabled("GUI_OMNIPARSER_ENABLED"):
        endpoint = os.environ.get("OMNI_PARSER_SERVER", "").strip()
        if not endpoint:
            raise RuntimeError(
                "OMNI_PARSER_SERVER is required when GUI_OMNIPARSER_ENABLED is enabled."
            )

        def capture_omniparser_frame() -> bytes:
            screenshot = pyautogui.screenshot().convert("RGB")
            buffer = io.BytesIO()
            screenshot.save(buffer, format="PNG")
            return buffer.getvalue()

        evidence_providers.append(
            OmniParserEvidenceProvider(endpoint, capture_omniparser_frame, store)
        )

    runtime = CoreOrchestrator(
        compositor,
        PyAutoGUIExecutor(
            pyautogui,
            coordinate_mapper=coordinate_mapper,
            platform_resolver=desktop_backend.platform_resolver,
            drag_handler=drag_handler,
        ),
        proposal_provider=QwenCUAProposalProvider(qwen_backend, store),
        frame_provider=PyAutoGUIFrameProvider(pyautogui, store),
        application_launcher=desktop_backend.application_launcher,
        evidence_providers=tuple(evidence_providers),
        policy_providers=desktop_backend.policy_providers,
        store=store,
        ledger=ledger,
    )
    facade = GuiRunFacade(runtime)
    def capture_frame():
        screenshot = pyautogui.screenshot().convert("RGB")
        screenshot_buffer = io.BytesIO()
        screenshot.save(screenshot_buffer, format="PNG")
        tree = get_treeland_layout_tree()
        return screenshot_buffer.getvalue(), screenshot.size, tree



    @mcp.tool()
    async def gui_run(
        operation: str,
        task_id: str = '',
        task_contract: dict | None = None,
        proposal: dict | None = None,
        proposal_id: str = '',
        confirmed: bool = False,
        strategy: str = 'compact',
        object_ref: str = '',
        diagnostic: bool = False,
        max_iterations: int | None = None,
    ) -> dict:
        """运行统一 AutoUI 协议操作。

        支持 ``describe``、``observe``、``propose``、``decide``、
        ``execute``、``evaluate``/``verify``、``run``、``status``、``reset``、
        ``trace``。默认返回对象引用；传 ``diagnostic=true`` 或使用 ``trace``
        查看详细对象。
        """
        return await asyncio.to_thread(
            facade.handle,
            operation,
            task_id=task_id,
            task_contract=task_contract,
            proposal=proposal,
            proposal_id=proposal_id,
            confirmed=confirmed,
            strategy=strategy,
            object_ref=object_ref,
            diagnostic=diagnostic,
            max_iterations=max_iterations,
        )

    @mcp.tool()
    async def desktop_capabilities_list(category: str = '') -> list[dict]:
        """List controller-owned Deepin desktop shortcuts and their policies.

        This reads the packaged keybinding schema.  Results marked
        ``source=default-schema`` are defaults, not proof that a user has not
        changed the shortcut at runtime.  ``auto_invokable`` is the controller
        policy decision; it is deliberately narrower than ``enabled``.
        """
        requested_category = category.strip().lower()
        items = load_keybinding_catalogue()
        if requested_category:
            items = [
                item for item in items
                if item["category"].lower() == requested_category
            ]
        return items

    @mcp.tool()
    async def desktop_shortcut_invoke(capability_id: str) -> dict:
        """Invoke one low-risk, controller-approved Deepin shortcut.

        The capability must be returned by ``desktop_capabilities_list`` and
        marked ``auto_invokable``.  This API never executes the schema's raw
        command/DBus trigger value.
        """
        capability = find_capability(capability_id.strip())
        if capability is None:
            raise ValueError("unknown desktop capability_id")
        if not capability["enabled"]:
            raise ValueError("desktop capability is disabled in the default schema")
        if not capability["auto_invokable"]:
            raise PermissionError(
                "controller policy does not allow automatic invocation of this capability"
            )
        hotkeys = capability["normalized_hotkeys"]
        if not hotkeys:
            raise ValueError("desktop capability has no keyboard shortcut")
        keys = hotkeys[0]
        task_id = new_id("shortcut-task")
        runtime.register_task(
            TaskContract(
                task_id=task_id,
                goal=f"Invoke platform capability {capability_id}",
                permissions=TaskPermissions(
                    frozenset({ActionType.PLATFORM_INVOKE}),
                    frozenset({"navigation"}),
                ),
                limits=TaskLimits(max_steps=1, max_retries=0),
            )
        )
        observed = runtime.observe(task_id)
        raw_before = store.require(observed.raw_artifact_ref)
        before = _active_window_summary(raw_before)
        proposal = ActionProposal(
            proposal_id=new_id("proposal"),
            source="desktop-shortcut",
            based_on_snapshot=observed.snapshot_id,
            action=Action(
                ActionType.PLATFORM_INVOKE,
                parameters={"capability_id": capability_id.strip()},
            ),
        )
        runtime.submit_proposal(task_id, proposal)
        decision = runtime.decide(proposal.proposal_id)
        if decision.status.value != "allow":
            raise PermissionError(f"policy refused shortcut: {decision.reason_code}")
        receipt = runtime.execute(proposal.proposal_id)
        if receipt.status.value != "delivered":
            return {
                "status": "failed",
                "capability": capability,
                "executed_keys": [],
                "reason": receipt.error_code,
            }
        _, _, post_tree, evidence = _capture_post_action_frame(
            capture_frame,
            get_treeland_layout_tree,
            "",
            0,
        )
        return {
            "status": "success",
            "capability": capability,
            "executed_keys": keys,
            "evidence": {
                "active_window_before": before,
                "active_window_after": _active_window_summary(post_tree),
                "observation": evidence,
            },
        }

    @mcp.tool()
    async def desktop_applications_list(query: str = '', limit: int = 30) -> list[dict]:
        """Resolve installed desktop applications to safe dde-am application IDs.

        The catalogue contains discovery metadata only; it intentionally omits
        desktop-entry Exec commands.  Use the returned ``app_id`` with
        ``desktop_application_launch``.
        """
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 100:
            raise ValueError("limit must be an integer between 1 and 100")
        needle = query.strip().casefold()
        applications = load_desktop_application_catalogue()
        if needle:
            applications = [
                item for item in applications
                if needle in item["app_id"].casefold()
                or needle in item["display_name"].casefold()
                or needle in (item["display_name_zh_cn"] or "").casefold()
            ]
        return applications[:limit]

    @mcp.tool()
    async def desktop_application_launch(
        app_id: str,
        expected_active_app_id: str = '',
        application_wait_timeout_s: float = DEFAULT_APPLICATION_WAIT_TIMEOUT_S,
    ) -> dict:
        """Launch a Deepin application by ID through dde-am and collect evidence.

        Only a plain application ID is accepted.  Paths, URIs, command mode,
        and arbitrary arguments are not part of this capability.  Supplying an
        expected Treeland app ID enables deterministic post-launch validation.
        """
        resolved_app_id = validate_application_id(app_id)
        expected_app_id = _expected_active_app_id(expected_active_app_id)
        timeout_s = _application_wait_timeout(application_wait_timeout_s)
        task_id = new_id("application-task")
        assertions = (
            AssertionSpec(
                "application-active",
                "active_window.app_id",
                "equals",
                expected_app_id,
            ),
        ) if expected_app_id else ()
        runtime.register_task(
            TaskContract(
                task_id=task_id,
                goal=f"Launch application {resolved_app_id}",
                permissions=TaskPermissions(
                    frozenset({ActionType.APPLICATION_LAUNCH}),
                    frozenset({"open_application"}),
                ),
                assertions=assertions,
                limits=TaskLimits(max_steps=1, max_retries=0),
                verification_profile="application-open",
            )
        )
        observed = await asyncio.to_thread(runtime.observe, task_id)
        active_before = _active_window_summary(store.require(observed.raw_artifact_ref))
        proposal = ActionProposal(
            proposal_id=new_id("proposal"),
            source="desktop-application-launch",
            based_on_snapshot=observed.snapshot_id,
            action=Action(
                ActionType.APPLICATION_LAUNCH,
                parameters={"app_id": resolved_app_id},
            ),
            semantic_intent="open_application",
            expected_effect={"active_app_id": expected_app_id} if expected_app_id else {},
        )
        runtime.submit_proposal(task_id, proposal)
        decision = runtime.decide(proposal.proposal_id)
        if decision.status.value != "allow":
            raise PermissionError(f"policy refused application launch: {decision.reason_code}")
        receipt = await asyncio.to_thread(runtime.execute, proposal.proposal_id)
        result = launcher.result_for(proposal.proposal_id)
        if receipt.status.value != "delivered":
            return {
                "status": "failed",
                "app_id": resolved_app_id,
                "returncode": getattr(result, "returncode", None),
                "stdout": getattr(result, "stdout", None),
                "stderr": getattr(result, "stderr", None),
                "reason": receipt.error_code,
            }

        _, _, post_tree, application_wait = _capture_post_action_frame(
            capture_frame,
            get_treeland_layout_tree,
            expected_app_id,
            timeout_s,
        )
        active_after = _active_window_summary(post_tree)
        task_validation = _active_app_task_validation(
            expected_app_id,
            active_before,
            active_after,
            application_wait,
        )
        _, assertion_results, task_state = await asyncio.to_thread(runtime.evaluate, task_id)
        return {
            "status": (
                "success"
                if task_validation is None or task_validation["status"] == "passed"
                else "partial"
            ),
            "app_id": resolved_app_id,
            "returncode": getattr(result, "returncode", 0),
            "stdout": getattr(result, "stdout", ""),
            "stderr": getattr(result, "stderr", ""),
            "evidence": {
                "active_window_before": active_before,
                "active_window_after": active_after,
                "application_wait": application_wait,
            },
            "task_validation": task_validation,
        }
