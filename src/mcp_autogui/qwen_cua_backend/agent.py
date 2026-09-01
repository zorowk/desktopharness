"""Minimal embedded Qwen-CUA S2 agent.

The model proposes structured ``computer_use`` calls. This module converts those
calls to the same pyautogui-shaped strings consumed by the project's strict AST
allowlist; it never executes model output.
"""

from __future__ import annotations

import json
import re
import threading
import time
from dataclasses import dataclass
from typing import Any

from .image import prepare_screenshot
from .prompts import build_system_prompt


@dataclass(frozen=True)
class AgentPrediction:
    assistant_output: str
    action_text: str
    actions: list[str]
    processed_image: str
    original_size: tuple[int, int]
    processed_size: tuple[int, int]
    telemetry: dict[str, Any]


class QwenCUAAgent:
    def __init__(
        self,
        *,
        model: str,
        base_url: str,
        api_key: str,
        timeout: float = 120.0,
        verify_tls: bool = True,
        trust_env: bool = False,
        max_tokens: int = 1024,
        max_response_chars: int = 16384,
        top_p: float = 0.5,
        temperature: float = 0.1,
        max_history_turns: int = 4,
        coordinate_type: str = "relative",
        resize_factor: int = 32,
    ) -> None:
        if not base_url:
            raise ValueError("CUA_MODEL_BASE_URL is required in embedded mode")
        if not model:
            raise ValueError("CUA_MODEL is required in embedded mode")
        if coordinate_type not in {"relative", "absolute"}:
            raise ValueError("CUA_COORDINATE_TYPE must be relative or absolute")
        if max_response_chars < 1024:
            raise ValueError("CUA_MAX_RESPONSE_CHARS must be at least 1024")
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key or "not-required"
        self.timeout = timeout
        self.verify_tls = verify_tls
        self.trust_env = trust_env
        self.max_tokens = max_tokens
        self.max_response_chars = max_response_chars
        self.top_p = top_p
        self.temperature = temperature
        self.max_history_turns = max_history_turns
        self.coordinate_type = coordinate_type
        self.resize_factor = resize_factor
        self._client: Any = None
        self._http_client: Any = None
        self._client_lock = threading.Lock()

    def predict(
        self,
        instruction: str,
        screenshot: bytes,
        history: list[dict[str, Any]],
        *,
        accessibility_tree: str | None = None,
        previous_feedback: dict[str, Any] | None = None,
    ) -> AgentPrediction:
        started = time.perf_counter()
        processed_image, original_size, processed_size = prepare_screenshot(
            screenshot,
            factor=self.resize_factor,
        )
        messages = self.build_messages(
            instruction,
            processed_image,
            processed_size,
            history,
            accessibility_tree=accessibility_tree,
            previous_feedback=previous_feedback,
        )
        request_started = time.perf_counter()
        response = self._call_llm(messages)
        request_ms = (time.perf_counter() - request_started) * 1000
        action_text, actions = parse_s2_response(
            response,
            original_size=original_size,
            processed_size=processed_size,
            coordinate_type=self.coordinate_type,
        )
        return AgentPrediction(
            assistant_output=response,
            action_text=action_text,
            actions=actions,
            processed_image=processed_image,
            original_size=original_size,
            processed_size=processed_size,
            telemetry={
                "backend_mode": "embedded",
                "model": self.model,
                "original_width": original_size[0],
                "original_height": original_size[1],
                "processed_width": processed_size[0],
                "processed_height": processed_size[1],
                "history_turns": min(len(history), self.max_history_turns),
                "message_count": len(messages),
                "llm_request_ms": round(request_ms, 1),
                "predict_total_ms": round((time.perf_counter() - started) * 1000, 1),
            },
        )

    def build_messages(
        self,
        instruction: str,
        current_image: str,
        processed_size: tuple[int, int],
        history: list[dict[str, Any]],
        *,
        accessibility_tree: str | None = None,
        previous_feedback: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        system_prompt = build_system_prompt(self.coordinate_type, processed_size)
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": [{"type": "text", "text": system_prompt}]}
        ]
        selected_history = history[-self.max_history_turns :]
        for turn in selected_history:
            image = turn.get("processed_image")
            response = turn.get("assistant_output")
            if isinstance(image, str) and isinstance(response, str):
                messages.append(
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "image_url",
                                "image_url": {"url": f"data:image/jpeg;base64,{image}"},
                            }
                        ],
                    }
                )
                messages.append(
                    {"role": "assistant", "content": [{"type": "text", "text": response}]}
                )

        prompt_parts = [
            "Generate only the next move from the latest screenshot.",
            f"Instruction: {instruction}",
        ]
        execution_summaries = [
            turn.get("execution") for turn in selected_history if turn.get("execution")
        ]
        if execution_summaries:
            prompt_parts.append(
                "Confirmed previous execution results:\n"
                + json.dumps(execution_summaries, ensure_ascii=False, default=str)[:8000]
            )
        if previous_feedback:
            prompt_parts.append(
                "Latest local validation/execution feedback:\n"
                + json.dumps(previous_feedback, ensure_ascii=False, default=str)[:8000]
            )
        if accessibility_tree:
            prompt_parts.append(
                "Treeland desktop context (advisory; the local validator is authoritative):\n"
                + accessibility_tree[:12000]
            )
        messages.append(
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/jpeg;base64,{current_image}"},
                    },
                    {"type": "text", "text": "\n\n".join(prompt_parts)},
                ],
            }
        )
        return messages

    def _call_llm(self, messages: list[dict[str, Any]]) -> str:
        client = self._get_client()
        response = client.chat.completions.create(
            model=self.model,
            messages=messages,
            max_tokens=self.max_tokens,
            temperature=self.temperature,
            top_p=self.top_p,
        )
        content = response.choices[0].message.content
        if not isinstance(content, str) or not content.strip():
            raise RuntimeError("Qwen model returned an empty response")
        if len(content) > self.max_response_chars:
            raise RuntimeError(
                "Qwen model response exceeds CUA_MAX_RESPONSE_CHARS "
                f"({self.max_response_chars})"
            )
        return content

    def _get_client(self) -> Any:
        with self._client_lock:
            if self._client is not None:
                return self._client
            try:
                import httpx
                import openai
            except ImportError as exc:
                raise RuntimeError(
                    "Embedded Qwen-CUA requires the openai and httpx packages"
                ) from exc
            self._http_client = httpx.Client(
                verify=self.verify_tls,
                trust_env=self.trust_env,
                timeout=self.timeout,
            )
            self._client = openai.OpenAI(
                base_url=self.base_url,
                api_key=self.api_key,
                http_client=self._http_client,
            )
            return self._client

    def close(self) -> None:
        with self._client_lock:
            client, http_client = self._client, self._http_client
            self._client = None
            self._http_client = None
        if client is not None:
            client.close()
        if http_client is not None:
            http_client.close()


_TOOL_CALL_PATTERN = re.compile(r"<tool_call>\s*(\{.*?\})\s*</tool_call>", re.DOTALL)
_ACTION_PATTERN = re.compile(r"^\s*Action:\s*(.+?)\s*$", re.IGNORECASE | re.MULTILINE)


def parse_s2_response(
    response: str,
    *,
    original_size: tuple[int, int],
    processed_size: tuple[int, int],
    coordinate_type: str,
) -> tuple[str, list[str]]:
    if not isinstance(response, str) or not response.strip():
        raise ValueError("Qwen response must be non-empty text")
    action_match = _ACTION_PATTERN.search(response)
    action_text = action_match.group(1).strip() if action_match else ""
    payloads = _TOOL_CALL_PATTERN.findall(response)
    if not payloads:
        stripped_lines = [line.strip() for line in response.splitlines()]
        payloads = [
            line
            for line in stripped_lines
            if line.startswith("{") and line.endswith("}") and '"arguments"' in line
        ]
    if not payloads:
        raise ValueError("Qwen response does not contain a computer_use tool call")
    if len(payloads) != 1:
        raise ValueError(
            "Qwen response must contain exactly one computer_use tool call for the next step"
        )

    try:
        tool_call = json.loads(payloads[0])
    except json.JSONDecodeError as exc:
        raise ValueError("Qwen tool call is not valid JSON") from exc
    if tool_call.get("name") != "computer_use":
        raise ValueError("Qwen tool call must use computer_use")
    arguments = tool_call.get("arguments")
    if not isinstance(arguments, dict):
        raise ValueError("Qwen computer_use arguments must be an object")
    actions = _computer_use_to_actions(
        arguments,
        original_size=original_size,
        processed_size=processed_size,
        coordinate_type=coordinate_type,
    )
    if not actions:
        raise ValueError("Qwen tool call did not produce an action")
    return action_text or "Perform the proposed GUI action", actions


def _computer_use_to_actions(
    arguments: dict[str, Any],
    *,
    original_size: tuple[int, int],
    processed_size: tuple[int, int],
    coordinate_type: str,
) -> list[str]:
    action = str(arguments.get("action") or "").strip()
    coordinate_actions = {
        "mouse_move": "moveTo",
        "left_click": "click",
        "click": "click",
        "left_click_drag": "dragTo",
        "right_click": "rightClick",
        "middle_click": "middleClick",
        "double_click": "doubleClick",
        "triple_click": "tripleClick",
    }
    if action in coordinate_actions:
        coordinate = arguments.get("coordinate")
        if not (
            isinstance(coordinate, list)
            and len(coordinate) == 2
            and all(isinstance(value, (int, float)) and not isinstance(value, bool) for value in coordinate)
        ):
            raise ValueError(f"Qwen {action} action requires a numeric coordinate pair")
        x, y = _project_coordinate(
            float(coordinate[0]),
            float(coordinate[1]),
            original_size=original_size,
            processed_size=processed_size,
            coordinate_type=coordinate_type,
        )
        function = coordinate_actions[action]
        if action == "left_click_drag":
            duration = _bounded_number(arguments.get("duration", 0.5), 0.0, 30.0, "duration")
            return [f"pyautogui.{function}({x}, {y}, duration={duration})"]
        return [f"pyautogui.{function}({x}, {y})"]
    if action == "type":
        text = arguments.get("text")
        if not isinstance(text, str):
            raise ValueError("Qwen type action requires text")
        return [f"pyautogui.write({text!r})"]
    if action in {"key", "key_down", "key_up"}:
        raw_keys = arguments.get("keys")
        if not isinstance(raw_keys, list) or not raw_keys or not all(
            isinstance(key, str) and key.strip() for key in raw_keys
        ):
            raise ValueError(f"Qwen {action} action requires non-empty string keys")
        keys = [key.strip() for key in raw_keys]
        if action == "key":
            joined = ", ".join(repr(key) for key in keys)
            function = "hotkey" if len(keys) > 1 else "press"
            return [f"pyautogui.{function}({joined})"]
        function = "keyDown" if action == "key_down" else "keyUp"
        ordered = keys if action == "key_down" else list(reversed(keys))
        return [f"pyautogui.{function}({key!r})" for key in ordered]
    if action in {"scroll", "hscroll"}:
        pixels = _bounded_number(arguments.get("pixels", 0), -100000, 100000, "pixels")
        return [f"pyautogui.{action}({pixels})"]
    if action == "wait":
        seconds = _bounded_number(arguments.get("time", 1.0), 0.0, 30.0, "time")
        return [f"time.sleep({seconds})"]
    if action == "terminate":
        return ["FAIL" if str(arguments.get("status", "success")).lower() == "failure" else "DONE"]
    raise ValueError(f"Unsupported Qwen computer_use action: {action or '<empty>'}")


def _project_coordinate(
    x: float,
    y: float,
    *,
    original_size: tuple[int, int],
    processed_size: tuple[int, int],
    coordinate_type: str,
) -> tuple[int, int]:
    original_width, original_height = original_size
    if coordinate_type == "absolute":
        processed_width, processed_height = processed_size
        projected_x = round(x * original_width / processed_width)
        projected_y = round(y * original_height / processed_height)
    else:
        projected_x = round(x * (original_width - 1) / 999)
        projected_y = round(y * (original_height - 1) / 999)
    return (
        max(0, min(original_width - 1, projected_x)),
        max(0, min(original_height - 1, projected_y)),
    )


def _bounded_number(value: Any, minimum: float, maximum: float, name: str) -> int | float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ValueError(f"Qwen {name} must be numeric")
    if value < minimum or value > maximum:
        raise ValueError(f"Qwen {name} must be between {minimum} and {maximum}")
    return value
