"""Deepin application launcher adapter; the core only sees the port."""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from threading import RLock

from ..core.models import (
    ActionProposal,
    ActionType,
    ExecutionReceipt,
    ExecutionStatus,
    new_id,
    utc_now,
)
from ..desktop_capabilities import validate_application_id


class DdeApplicationLauncher:
    launcher_id = "dde-am"

    def __init__(self, runner: Callable[..., object] = subprocess.run) -> None:
        self._runner = runner
        self._results: dict[str, object] = {}
        self._lock = RLock()

    def launch(self, proposal: ActionProposal) -> ExecutionReceipt:
        started = utc_now()
        status = ExecutionStatus.FAILED
        error = None
        try:
            if proposal.action.type != ActionType.APPLICATION_LAUNCH:
                raise ValueError("launcher received a non-launch proposal")
            app_id = validate_application_id(str(proposal.action.parameters.get("app_id") or ""))
            result = self._runner(
                ["dde-am", app_id], capture_output=True, text=True, timeout=10, check=False
            )
            with self._lock:
                self._results[proposal.proposal_id] = result
                while len(self._results) > 100:
                    self._results.pop(next(iter(self._results)))
            if getattr(result, "returncode", 1) != 0:
                error = "APPLICATION_LAUNCH_FAILED"
            else:
                status = ExecutionStatus.DELIVERED
        except FileNotFoundError:
            error = "CAPABILITY_UNAVAILABLE"
        except Exception as exc:
            error = f"APPLICATION_LAUNCH_{type(exc).__name__.upper()}"
        return ExecutionReceipt(
            execution_id=new_id("execution"),
            proposal_id=proposal.proposal_id,
            status=status,
            executed_action=proposal.action if status == ExecutionStatus.DELIVERED else None,
            started_at=started,
            finished_at=utc_now(),
            error_code=error,
        )

    def result_for(self, proposal_id: str) -> object | None:
        with self._lock:
            return self._results.get(proposal_id)
