from __future__ import annotations

from typing import Protocol

from ..core.models import ActionProposal, ModelContext


class ProposalProvider(Protocol):
    def propose(self, context: ModelContext) -> ActionProposal: ...

    def record_execution(self, task_id: str, receipt: object) -> object: ...

    def reset(self, task_id: str) -> None: ...
