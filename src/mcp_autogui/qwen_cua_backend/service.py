"""In-process Qwen-CUA service with explicit proposal/feedback state."""

from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass, field
from typing import Any

from .agent import AgentPrediction, QwenCUAAgent


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


@dataclass(frozen=True)
class QwenCUAConfig:
    model: str
    base_url: str
    api_key: str
    timeout: float
    verify_tls: bool
    trust_env: bool
    max_tokens: int
    top_p: float
    temperature: float
    max_history_turns: int
    coordinate_type: str
    resize_factor: int

    @classmethod
    def from_env(cls) -> "QwenCUAConfig":
        return cls(
            model=os.getenv("CUA_MODEL", "qwen3_rl").strip() or "qwen3_rl",
            base_url=os.getenv("CUA_MODEL_BASE_URL", "").strip().rstrip("/"),
            api_key=os.getenv("CUA_MODEL_API_KEY", "").strip(),
            timeout=_env_float("CUA_MODEL_TIMEOUT", 120.0, 1.0),
            verify_tls=_env_bool("CUA_MODEL_TLS_VERIFY", True),
            trust_env=_env_bool("CUA_MODEL_TRUST_ENV", False),
            max_tokens=_env_int("CUA_MAX_TOKENS", 4096),
            top_p=min(1.0, _env_float("CUA_TOP_P", 0.5)),
            temperature=min(1.0, _env_float("CUA_TEMPERATURE", 0.1)),
            max_history_turns=_env_int("CUA_MAX_HISTORY_TURNS", 4),
            coordinate_type=os.getenv("CUA_COORDINATE_TYPE", "relative").strip()
            or "relative",
            resize_factor=_env_int("CUA_RESIZE_FACTOR", 32),
        )


@dataclass
class _PendingProposal:
    prediction: AgentPrediction
    instruction: str
    client_step: int | None
    created_at: float = field(default_factory=time.time)


@dataclass
class _Session:
    instruction: str = ""
    history: list[dict[str, Any]] = field(default_factory=list)
    pending: _PendingProposal | None = None
    previous_feedback: dict[str, Any] | None = None
    lock: threading.RLock = field(default_factory=threading.RLock)


class QwenCUAService:
    """Own Qwen conversation state inside treeland-autoui-mcp.

    Prediction creates a pending proposal. The proposal enters committed model
    history only after ``record_execution(..., status="success")``.
    """

    def __init__(
        self,
        config: QwenCUAConfig | None = None,
        *,
        agent: QwenCUAAgent | None = None,
    ) -> None:
        self.config = config or QwenCUAConfig.from_env()
        self._agent = agent
        self._sessions: dict[str, _Session] = {}
        self._sessions_lock = threading.RLock()

    def init(self, session_id: str) -> dict[str, Any]:
        self._validate_session_id(session_id)
        self._get_or_create_session(session_id)
        return {"ok": True, "session_id": session_id, "backend_mode": "embedded"}

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
    ) -> dict[str, Any]:
        del image_mime, image_quality
        self._validate_session_id(session_id)
        normalized_instruction = instruction.strip()
        if not normalized_instruction:
            raise ValueError("instruction must not be empty")
        session = self._get_or_create_session(session_id)
        with session.lock:
            if session.pending is not None:
                raise RuntimeError(
                    "Qwen-CUA session already has a pending proposal; record its execution or reset"
                )
            if session.instruction and session.instruction != normalized_instruction:
                session.history.clear()
                session.previous_feedback = None
            session.instruction = normalized_instruction
            prediction = self._get_agent().predict(
                normalized_instruction,
                screenshot,
                session.history,
                accessibility_tree=accessibility_tree,
                previous_feedback=session.previous_feedback,
            )
            session.pending = _PendingProposal(
                prediction=prediction,
                instruction=normalized_instruction,
                client_step=client_step,
            )
            return {
                "agent_type": "cua",
                "observation_text": prediction.assistant_output,
                "action_text": prediction.action_text,
                "actions": prediction.actions,
                "assistant_output": prediction.assistant_output,
                "telemetry": prediction.telemetry,
                "proposal_pending": True,
            }

    def record_execution(
        self,
        session_id: str,
        *,
        status: str,
        execution: Any = None,
        reason: str | None = None,
    ) -> dict[str, Any]:
        session = self._get_session(session_id)
        normalized_status = status.strip().lower()
        if normalized_status not in {"success", "rejected", "partial", "error", "cancelled"}:
            raise ValueError("Unsupported Qwen-CUA execution status")
        with session.lock:
            pending = session.pending
            if pending is None:
                raise RuntimeError("Qwen-CUA session has no pending proposal")
            feedback = {
                "status": normalized_status,
                "proposed_actions": pending.prediction.actions,
                "actual_execution": execution,
                "reason": reason,
                "client_step": pending.client_step,
            }
            if normalized_status == "success":
                session.history.append(
                    {
                        "processed_image": pending.prediction.processed_image,
                        "assistant_output": pending.prediction.assistant_output,
                        "action_text": pending.prediction.action_text,
                        "actions": pending.prediction.actions,
                        "execution": feedback,
                    }
                )
                overflow = len(session.history) - self.config.max_history_turns
                if overflow > 0:
                    del session.history[:overflow]
            session.previous_feedback = feedback
            session.pending = None
            return {
                "ok": True,
                "session_id": session_id,
                "status": normalized_status,
                "committed": normalized_status == "success",
                "history_turns": len(session.history),
            }

    def reset(self, session_id: str) -> None:
        self._validate_session_id(session_id)
        with self._sessions_lock:
            self._sessions.pop(session_id, None)

    def health(self) -> dict[str, Any]:
        with self._sessions_lock:
            session_count = len(self._sessions)
            pending_count = sum(
                1 for session in self._sessions.values() if session.pending is not None
            )
        return {
            "ok": bool(self.config.base_url),
            "backend_mode": "embedded",
            "configured": bool(self.config.base_url),
            "model": self.config.model,
            "sessions": session_count,
            "pending_proposals": pending_count,
            "message": (
                "ready"
                if self.config.base_url
                else "CUA_MODEL_BASE_URL is not configured"
            ),
        }

    def close(self) -> None:
        with self._sessions_lock:
            self._sessions.clear()
        if self._agent is not None:
            self._agent.close()
            self._agent = None

    def _get_agent(self) -> QwenCUAAgent:
        with self._sessions_lock:
            if self._agent is None:
                self._agent = QwenCUAAgent(
                    model=self.config.model,
                    base_url=self.config.base_url,
                    api_key=self.config.api_key,
                    timeout=self.config.timeout,
                    verify_tls=self.config.verify_tls,
                    trust_env=self.config.trust_env,
                    max_tokens=self.config.max_tokens,
                    top_p=self.config.top_p,
                    temperature=self.config.temperature,
                    max_history_turns=self.config.max_history_turns,
                    coordinate_type=self.config.coordinate_type,
                    resize_factor=self.config.resize_factor,
                )
            return self._agent

    def _get_or_create_session(self, session_id: str) -> _Session:
        with self._sessions_lock:
            return self._sessions.setdefault(session_id, _Session())

    def _get_session(self, session_id: str) -> _Session:
        self._validate_session_id(session_id)
        with self._sessions_lock:
            session = self._sessions.get(session_id)
        if session is None:
            raise ValueError("Unknown Qwen-CUA session_id")
        return session

    @staticmethod
    def _validate_session_id(session_id: str) -> None:
        if not isinstance(session_id, str) or not session_id.strip():
            raise ValueError("session_id must not be empty")
