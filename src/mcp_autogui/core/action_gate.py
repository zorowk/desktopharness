"""Deterministic eligibility, semantic policy, and ProposalGuard checks."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence

from .models import (
    ActionProposal,
    ActionType,
    AdapterDescriptor,
    CanonicalSnapshot,
    EvidenceConfidence,
    Point,
    PolicyDecision,
    PolicyStatus,
    ProposalGuard,
    SemanticResolution,
    SemanticTag,
    TaskContract,
    new_id,
)


DEFAULT_SEMANTIC_POLICY = {
    "navigation": "allow",
    "open_application": "allow",
    "content_edit": "confirm",
    "settings_change": "confirm",
    "external_side_effect": "confirm",
    "destructive": "deny",
    "authentication": "deny",
    "unknown": "confirm",
}
DEFAULT_POLICY_PROFILES = {"desktop-safe-default": DEFAULT_SEMANTIC_POLICY}
_POLICY_PRIORITY = {"allow": 0, "confirm": 1, "deny": 2}
_POINTER_TARGET_ACTIONS = {
    ActionType.POINTER_MOVE,
    ActionType.POINTER_CLICK,
    ActionType.POINTER_DOUBLE_CLICK,
    ActionType.POINTER_DRAG,
}
_KEYBOARD_ACTIONS = {
    ActionType.KEYBOARD_KEY,
    ActionType.KEYBOARD_SHORTCUT,
    ActionType.KEYBOARD_TEXT,
}


class ActionGate:
    def __init__(
        self,
        descriptor: AdapterDescriptor,
        hit_test: Callable[[Point, CanonicalSnapshot | None], str | None],
        policy_profiles: Mapping[str, Mapping[str, str]] | None = None,
    ) -> None:
        self._descriptor = descriptor
        self._hit_test = hit_test
        self.policy_profiles = {
            name: dict(policy) for name, policy in (policy_profiles or DEFAULT_POLICY_PROFILES).items()
        }
        if not self.policy_profiles or any(
            value not in _POLICY_PRIORITY
            for policy in self.policy_profiles.values()
            for value in policy.values()
        ):
            raise ValueError("policy profiles must contain only allow, confirm, or deny")

    def decide(
        self,
        proposal: ActionProposal,
        contract: TaskContract,
        snapshot: CanonicalSnapshot,
        independent_tags: Sequence[SemanticTag] = (),
    ) -> tuple[PolicyDecision, ProposalGuard | None, SemanticResolution]:
        resolution = self.resolve_semantics(proposal, independent_tags)
        invalid = self._mechanical_check(proposal, contract, snapshot)
        if invalid is not None:
            return self._decision(proposal, invalid[0], invalid[1], resolution), None, resolution

        guard, guard_error = self._derive_guard(proposal, snapshot)
        if guard_error is not None:
            return self._decision(proposal, guard_error[0], guard_error[1], resolution), None, resolution

        policy_status, reason = self._evaluate_policy(resolution, contract)
        decision = PolicyDecision(
            proposal_id=proposal.proposal_id,
            status=policy_status,
            reason_code=reason,
            resolved_target=(
                {"window_id": guard.target_window_id}
                if guard is not None and guard.target_window_id is not None
                else {}
            ),
            guard_ref=guard.guard_id if guard is not None else None,
            semantic_resolution_ref=resolution.semantic_resolution_id,
        )
        return decision, guard, resolution

    @staticmethod
    def resolve_semantics(
        proposal: ActionProposal, independent_tags: Sequence[SemanticTag]
    ) -> SemanticResolution:
        tags = list(independent_tags)
        if proposal.action.type == ActionType.APPLICATION_LAUNCH:
            tags.append(SemanticTag("open_application", "action-schema", None, EvidenceConfidence.DETERMINISTIC))
        elif proposal.action.type == ActionType.KEYBOARD_TEXT:
            tags.append(SemanticTag("content_edit", "action-schema", None, EvidenceConfidence.DERIVED))
        elif proposal.action.type in {ActionType.POINTER_MOVE, ActionType.POINTER_SCROLL}:
            tags.append(SemanticTag("navigation", "action-schema", None, EvidenceConfidence.DETERMINISTIC))
        elif proposal.action.type == ActionType.DONE:
            tags.append(SemanticTag("navigation", "action-schema", None, EvidenceConfidence.DETERMINISTIC))

        if proposal.semantic_intent:
            tags.append(
                SemanticTag(
                    proposal.semantic_intent,
                    "proposal-claim",
                    proposal.debug_ref,
                    EvidenceConfidence.MODEL_CLAIM,
                )
            )
        independent = [tag for tag in tags if tag.confidence != EvidenceConfidence.MODEL_CLAIM]
        if not independent:
            tags.append(SemanticTag("unknown", "action-gate", None, EvidenceConfidence.DERIVED))
        return SemanticResolution(
            semantic_resolution_id=new_id("semantic-resolution"),
            proposal_id=proposal.proposal_id,
            status="resolved" if independent else "unknown",
            tags=tuple(tags),
        )

    def _mechanical_check(
        self, proposal: ActionProposal, contract: TaskContract, snapshot: CanonicalSnapshot
    ) -> tuple[PolicyStatus, str] | None:
        action = proposal.action
        if action.type not in contract.permissions.actions:
            return PolicyStatus.DENY, "MECHANICAL_PERMISSION_DENIED"
        if action.coordinate is not None:
            if action.coordinate_space != snapshot.coordinate_space.id:
                return PolicyStatus.INVALID, "INVALID_COORDINATE_SPACE"
            if not snapshot.coordinate_space.bounds.contains(action.coordinate):
                return PolicyStatus.INVALID, "OUTSIDE_DESKTOP"
        return None

    def _derive_guard(
        self, proposal: ActionProposal, snapshot: CanonicalSnapshot
    ) -> tuple[ProposalGuard | None, tuple[PolicyStatus, str] | None]:
        action = proposal.action
        target_id: str | None = None
        point: Point | None = None
        require_hit = False
        cursor_origin = None
        if action.type in _POINTER_TARGET_ACTIONS:
            point = action.coordinate
            if action.parameters.get("relative"):
                if snapshot.cursor is None:
                    return None, (PolicyStatus.INVALID, "CAPABILITY_UNAVAILABLE")
                cursor_origin = snapshot.cursor
            if action.type == ActionType.POINTER_DRAG:
                if snapshot.cursor is None:
                    return None, (PolicyStatus.INVALID, "CAPABILITY_UNAVAILABLE")
                # dragTo-style actions begin at the current cursor. The
                # destination was already checked against desktop bounds, but
                # target identity and occlusion belong to the source point.
                point = snapshot.cursor
                cursor_origin = snapshot.cursor
            if not self._descriptor.capabilities.stacking.hit_test:
                return None, (PolicyStatus.INVALID, "CAPABILITY_UNAVAILABLE")
            target_id = self._hit_test(point, snapshot) if point is not None else None
            if target_id is None:
                return None, (PolicyStatus.INVALID, "TARGET_NOT_FOUND")
            require_hit = True
        elif action.type in _KEYBOARD_ACTIONS:
            active = snapshot.active_window()
            target_id = active.window_id if active is not None else None

        target = snapshot.window(target_id) if target_id else None
        if action.type in _KEYBOARD_ACTIONS and target is None:
            reason = "TARGET_NOT_FOUND" if self._descriptor.capabilities.active_window else "CAPABILITY_UNAVAILABLE"
            return None, (PolicyStatus.INVALID, reason)
        if target is not None and target.visible is not True:
            reason = "TARGET_OCCLUDED" if target.visible is False else "CAPABILITY_UNAVAILABLE"
            return None, (PolicyStatus.INVALID, reason)
        identity = {}
        if target is not None:
            identity = {
                key: value
                for key, value in {"app_id": target.app_id, "title": target.title}.items()
                if value is not None
            }
        if target_id is None and point is None and action.type not in _KEYBOARD_ACTIONS:
            return None, None
        return (
            ProposalGuard(
                guard_id=new_id("proposal-guard"),
                proposal_id=proposal.proposal_id,
                derived_from_snapshot=snapshot.snapshot_id,
                coordinate_space_id=snapshot.coordinate_space.id if point is not None else None,
                coordinate_space_version=snapshot.coordinate_space.version if point is not None else None,
                target_window_id=target_id,
                target_identity=identity,
                identity_required=self._descriptor.capabilities.window_identity != "unavailable",
                required_visible=target is not None,
                required_active=action.type in _KEYBOARD_ACTIONS,
                expected_geometry=target.geometry if target is not None else None,
                geometry_policy="point-must-remain-inside" if point is not None else None,
                hit_test_point=point,
                required_hit_window_id=target_id if require_hit else None,
                cursor_origin=cursor_origin,
            ),
            None,
        )

    def _evaluate_policy(
        self, resolution: SemanticResolution, contract: TaskContract
    ) -> tuple[PolicyStatus, str]:
        independent = [
            tag for tag in resolution.tags if tag.confidence != EvidenceConfidence.MODEL_CLAIM
        ]
        effective = independent or [tag for tag in resolution.tags if tag.tag == "unknown"]
        profile = self.policy_profiles.get(contract.policy_profile)
        if profile is None:
            return PolicyStatus.INVALID, "SEMANTIC_POLICY_DENIED"
        policies = []
        for tag in effective:
            if (
                tag.tag != "unknown"
                and contract.permissions.semantic_intents
                and tag.tag not in contract.permissions.semantic_intents
            ):
                policies.append("deny")
            else:
                policies.append(
                    contract.policy_overrides.get(tag.tag, profile.get(tag.tag, "confirm"))
                )
        # Claims may conservatively raise the required policy level, but they
        # can never lower a decision supported by independent semantics.
        for tag in resolution.tags:
            if tag.confidence != EvidenceConfidence.MODEL_CLAIM:
                continue
            if contract.permissions.semantic_intents and tag.tag not in contract.permissions.semantic_intents:
                policies.append("deny")
            else:
                policies.append(
                    contract.policy_overrides.get(tag.tag, profile.get(tag.tag, "confirm"))
                )
        result = max(policies or ["confirm"], key=lambda item: _POLICY_PRIORITY.get(item, 1))
        if result == "deny":
            return PolicyStatus.DENY, "SEMANTIC_POLICY_DENIED"
        if result == "confirm":
            return PolicyStatus.CONFIRM, "CONFIRMATION_REQUIRED"
        return PolicyStatus.ALLOW, "OK"

    @staticmethod
    def _decision(
        proposal: ActionProposal,
        status: PolicyStatus,
        reason: str,
        resolution: SemanticResolution,
    ) -> PolicyDecision:
        return PolicyDecision(
            proposal_id=proposal.proposal_id,
            status=status,
            reason_code=reason,
            semantic_resolution_ref=resolution.semantic_resolution_id,
        )

    def recheck(self, guard: ProposalGuard, snapshot: CanonicalSnapshot) -> str | None:
        if guard.coordinate_space_id is not None:
            if snapshot.coordinate_space.id != guard.coordinate_space_id:
                return "COORDINATE_SPACE_CHANGED"
            if (
                guard.coordinate_space_version is not None
                and snapshot.coordinate_space.version is not None
                and snapshot.coordinate_space.version != guard.coordinate_space_version
            ):
                return "COORDINATE_SPACE_CHANGED"
        target = snapshot.window(guard.target_window_id) if guard.target_window_id else None
        if guard.target_window_id and target is None:
            return "TARGET_DISAPPEARED"
        if target is not None:
            if guard.required_visible and target.visible is not True:
                return "TARGET_OCCLUDED"
            if guard.required_active and target.active is not True:
                return "TARGET_IDENTITY_CHANGED"
            if guard.identity_required:
                current = {key: getattr(target, key) for key in guard.target_identity}
                if current != dict(guard.target_identity):
                    return "TARGET_IDENTITY_CHANGED"
            if guard.geometry_policy == "point-must-remain-inside":
                if guard.hit_test_point is None or not target.geometry.contains(guard.hit_test_point):
                    return "TARGET_GEOMETRY_INVALIDATED"
        if guard.required_hit_window_id is not None and guard.hit_test_point is not None:
            if self._hit_test(guard.hit_test_point, snapshot) != guard.required_hit_window_id:
                return "HIT_TEST_CHANGED"
        if guard.cursor_origin is not None and snapshot.cursor != guard.cursor_origin:
            return "CURSOR_ORIGIN_CHANGED"
        return None
