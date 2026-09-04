from __future__ import annotations

from ...desktop_capabilities import find_capability, load_keybinding_catalogue
from ...core.models import ActionType, EvidenceConfidence, SemanticTag


class DeepinKeybindingProvider:
    provider_id = "deepin-keybindings"

    def __init__(self, loader=load_keybinding_catalogue, resolver=find_capability):
        self._loader = loader
        self._resolver = resolver

    def list_capabilities(self):
        return self._loader()

    def resolve(self, capability_id: str):
        return self._resolver(capability_id)

    def independent_tags(self, proposal, contract):
        del contract
        if proposal.action.type != ActionType.PLATFORM_INVOKE:
            return ()
        capability_id = str(proposal.action.parameters.get("capability_id") or "")
        capability = self.resolve(capability_id)
        if capability is None:
            return ()
        if capability.get("risk") == "high":
            tag = "destructive"
        elif capability.get("auto_invokable"):
            tag = "navigation"
        else:
            tag = "unknown"
        return (
            SemanticTag(
                tag,
                "platform-capability",
                f"deepin-capability:{capability_id}",
                EvidenceConfidence.DETERMINISTIC,
            ),
        )
