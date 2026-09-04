from __future__ import annotations

from typing import Protocol, Sequence

from ..core.models import ActionProposal, SemanticTag, TaskContract


class PolicyProvider(Protocol):
    def independent_tags(
        self, proposal: ActionProposal, contract: TaskContract
    ) -> Sequence[SemanticTag]: ...

