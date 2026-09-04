from __future__ import annotations

from typing import Protocol

from ..core.models import ActionProposal, ExecutionReceipt


class ApplicationLauncher(Protocol):
    def launch(self, proposal: ActionProposal) -> ExecutionReceipt: ...

