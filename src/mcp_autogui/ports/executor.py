from __future__ import annotations

from typing import Protocol

from ..core.models import ActionProposal, ExecutionReceipt


class InputExecutor(Protocol):
    def execute(self, proposal: ActionProposal) -> ExecutionReceipt: ...

