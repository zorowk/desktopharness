from __future__ import annotations

import base64
import json
import os
import struct
from typing import Any

import requests


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int, minimum: int = 1) -> int:
    try:
        return max(minimum, int(os.getenv(name, str(default))))
    except (TypeError, ValueError):
        return default


def _env_float(name: str, default: float, minimum: float = 0.0) -> float:
    try:
        return max(minimum, float(os.getenv(name, str(default))))
    except (TypeError, ValueError):
        return default


class HttpQwenBackendClient:
    """Optional compatibility client for a separately deployed Qwen-CUA backend."""

    def __init__(self) -> None:
        self.url = os.getenv("CUA_BACKEND_URL", "").strip().rstrip("/")
        self.api_key = os.getenv("CUA_BACKEND_API_KEY", "").strip()
        self.agent_type = os.getenv("CUA_AGENT_TYPE", "cua").strip() or "cua"
        self.rollout_nums = _env_int("CUA_ROLLOUT_NUMS", 1)
        self.temperature = min(1.0, _env_float("CUA_TEMPERATURE", 0.1))
        self.timeout = _env_float("CUA_BACKEND_TIMEOUT", 120.0, minimum=1.0)
        self.verify_tls = _env_bool("CUA_TLS_VERIFY", True)
        self.trust_env = _env_bool("CUA_HTTP_TRUST_ENV", False)

    def _require_url(self) -> None:
        if not self.url:
            raise RuntimeError("CUA_BACKEND_URL is required for Qwen-CUA tools")

    @property
    def headers(self) -> dict[str, str]:
        return {"X-API-Key": self.api_key} if self.api_key else {}

    def _request(self, method: str, path: str, **kwargs: Any) -> requests.Response:
        self._require_url()
        kwargs.setdefault("timeout", self.timeout)
        kwargs.setdefault("verify", self.verify_tls)
        with requests.Session() as session:
            session.trust_env = self.trust_env
            response = session.request(method, f"{self.url}{path}", **kwargs)
        if response.status_code == 401:
            raise RuntimeError("Qwen-CUA backend API key is missing")
        if response.status_code == 403:
            raise RuntimeError("Qwen-CUA backend API key is invalid")
        if response.status_code >= 400:
            raise RuntimeError(
                f"Qwen-CUA backend returned HTTP {response.status_code}: {response.text[:500]}"
            )
        return response

    def init(self, session_id: str) -> dict[str, Any]:
        response = self._request(
            "POST",
            "/init",
            json={"frontend_id": session_id, "agent_type": self.agent_type},
            headers=self.headers,
        )
        return self._json_object(response)

    def reset(self, session_id: str) -> None:
        self._request(
            "POST",
            "/reset",
            json={"frontend_id": session_id, "agent_type": self.agent_type},
            headers=self.headers,
        )

    def predict(
        self,
        instruction: str,
        screenshot: bytes,
        session_id: str,
        *,
        image_mime: str = "image/png",
        image_quality: int | None = None,
        client_step: int | None = None,
        accessibility_tree: str | None = None,
        session_instruction: str | None = None,
    ) -> dict[str, Any]:
        del session_instruction
        metadata: dict[str, Any] = {
            "frontend_id": session_id,
            "instruction": instruction,
            "agent_type": self.agent_type,
            "rollout_nums": self.rollout_nums,
            "image_mime": image_mime,
            "image_quality": image_quality,
        }
        if self.temperature > 0:
            metadata["temperature"] = self.temperature
        if client_step is not None:
            metadata["client_step"] = client_step
        if accessibility_tree:
            metadata["accessibility_tree"] = accessibility_tree

        try:
            return self._predict_binary(metadata, screenshot)
        except _BinaryEndpointUnavailable:
            return self._predict_json(metadata, screenshot)

    def health(self) -> dict[str, Any]:
        return self._json_object(self._request("GET", "/health", headers=self.headers))

    def _predict_binary(self, metadata: dict[str, Any], screenshot: bytes) -> dict[str, Any]:
        metadata_json = json.dumps(metadata, ensure_ascii=False).encode("utf-8")
        if len(metadata_json) > 65535:
            raise ValueError("Qwen-CUA request metadata is too large")
        body = struct.pack(">I", len(metadata_json)) + metadata_json + screenshot
        self._require_url()
        headers = {**self.headers, "Content-Type": "application/octet-stream"}
        with requests.Session() as session:
            session.trust_env = self.trust_env
            response = session.request(
                "POST",
                f"{self.url}/predict_binary",
                data=body,
                headers=headers,
                timeout=self.timeout,
                verify=self.verify_tls,
            )
        if response.status_code in {404, 405}:
            raise _BinaryEndpointUnavailable
        if response.status_code == 401:
            raise RuntimeError("Qwen-CUA backend API key is missing")
        if response.status_code == 403:
            raise RuntimeError("Qwen-CUA backend API key is invalid")
        if response.status_code >= 400:
            raise RuntimeError(
                f"Qwen-CUA backend returned HTTP {response.status_code}: {response.text[:500]}"
            )
        return self._json_object(response)

    def _predict_json(self, metadata: dict[str, Any], screenshot: bytes) -> dict[str, Any]:
        payload = dict(metadata)
        payload["screenshot_b64"] = base64.b64encode(screenshot).decode("ascii")
        response = self._request(
            "POST",
            "/predict",
            json=payload,
            headers={**self.headers, "Content-Type": "application/json"},
        )
        return self._json_object(response)

    @staticmethod
    def _json_object(response: requests.Response) -> dict[str, Any]:
        try:
            payload = response.json()
        except ValueError as exc:
            raise RuntimeError("Qwen-CUA backend returned invalid JSON") from exc
        if not isinstance(payload, dict):
            raise RuntimeError("Qwen-CUA backend response must be a JSON object")
        return payload


class _BinaryEndpointUnavailable(Exception):
    pass


class QwenBackendClient:
    """Select the embedded Qwen-CUA service by default.

    ``CUA_BACKEND_MODE=http`` retains compatibility with the old gui-mcp HTTP
    deployment. The default ``embedded`` mode has no runtime dependency on that
    repository or service.
    """

    def __init__(self) -> None:
        self.mode = os.getenv("CUA_BACKEND_MODE", "embedded").strip().lower() or "embedded"
        if self.mode == "embedded":
            from .qwen_cua_backend import QwenCUAService

            self._delegate: Any = QwenCUAService()
        elif self.mode == "http":
            self._delegate = HttpQwenBackendClient()
        else:
            raise ValueError("CUA_BACKEND_MODE must be 'embedded' or 'http'")

    def init(self, session_id: str) -> dict[str, Any]:
        return self._delegate.init(session_id)

    def reset(self, session_id: str) -> None:
        self._delegate.reset(session_id)

    def predict(
        self,
        instruction: str,
        screenshot: bytes,
        session_id: str,
        *,
        image_mime: str = "image/png",
        image_quality: int | None = None,
        client_step: int | None = None,
        accessibility_tree: str | None = None,
        session_instruction: str | None = None,
    ) -> dict[str, Any]:
        return self._delegate.predict(
            instruction,
            screenshot,
            session_id,
            image_mime=image_mime,
            image_quality=image_quality,
            client_step=client_step,
            accessibility_tree=accessibility_tree,
            session_instruction=session_instruction,
        )

    def record_execution(
        self,
        session_id: str,
        *,
        status: str,
        execution: Any = None,
        reason: str | None = None,
    ) -> dict[str, Any]:
        recorder = getattr(self._delegate, "record_execution", None)
        if not callable(recorder):
            return {
                "ok": False,
                "backend_mode": self.mode,
                "committed": False,
                "message": "HTTP compatibility backend does not support execution feedback",
            }
        return recorder(
            session_id,
            status=status,
            execution=execution,
            reason=reason,
        )

    def health(self) -> dict[str, Any]:
        health = self._delegate.health()
        health.setdefault("backend_mode", self.mode)
        return health

    def close(self) -> None:
        closer = getattr(self._delegate, "close", None)
        if callable(closer):
            closer()
