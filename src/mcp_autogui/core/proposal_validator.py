"""Technical proposal validation immediately before action injection."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
import math

from .desktop import AdapterDescriptor, CanonicalSnapshot, Point, Rect
from .protocol import ReasonCode
from .transaction import Action, ActionProposal, ActionType


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


@dataclass(frozen=True, slots=True)
class ValidationFailure:
    reason_code: ReasonCode
    retryable: bool


@dataclass(frozen=True, slots=True)
class ActionDependency:
    action_index: int
    coordinate_space_id: str | None = None
    coordinate_space_version: str | None = None
    target_window_id: str | None = None
    target_identity: tuple[tuple[str, str], ...] = ()
    identity_required: bool = False
    required_visible: bool = False
    required_active: bool = False
    expected_geometry: Rect | None = None
    hit_test_point: Point | None = None
    required_hit_window_id: str | None = None
    cursor_origin: Point | None = None


@dataclass(frozen=True, slots=True)
class PreparedProposal:
    proposal: ActionProposal
    source_snapshot_id: str
    dependencies: tuple[ActionDependency, ...]


class ProposalValidator:
    """Validate canonical actions without interpreting user intent."""

    def __init__(
        self,
        descriptor: AdapterDescriptor,
        hit_test: Callable[[Point, CanonicalSnapshot | None], str | None],
        *,
        denied_actions: frozenset[ActionType] = frozenset(),
        action_validator: Callable[[Action], ReasonCode | None] | None = None,
    ) -> None:
        self._descriptor = descriptor
        self._hit_test = hit_test
        self.denied_actions = denied_actions
        self._action_validator = action_validator

    def prepare(
        self, snapshot: CanonicalSnapshot, proposal: ActionProposal
    ) -> PreparedProposal | ValidationFailure:
        if proposal.based_on_snapshot != snapshot.snapshot_id:
            return ValidationFailure(ReasonCode.SNAPSHOT_UNAVAILABLE, False)
        done_indexes = [
            index for index, action in enumerate(proposal.actions)
            if action.type == ActionType.DONE
        ]
        if done_indexes and done_indexes != [len(proposal.actions) - 1]:
            return ValidationFailure(ReasonCode.MODEL_PROTOCOL_INVALID, False)

        dependencies: list[ActionDependency] = []
        working = snapshot
        cursor_determined_by_sequence = False
        for index, action in enumerate(proposal.actions):
            failure = self._validate_action(action, working)
            if failure is not None:
                return failure
            dependency = self._dependency(
                index,
                action,
                working,
                require_cursor_origin=not cursor_determined_by_sequence,
            )
            if isinstance(dependency, ValidationFailure):
                return dependency
            if dependency is not None:
                dependencies.append(dependency)
            if action.type in _POINTER_TARGET_ACTIONS and action.coordinate is not None:
                working = replace(working, cursor=action.coordinate)
                cursor_determined_by_sequence = True
        return PreparedProposal(proposal, snapshot.snapshot_id, tuple(dependencies))

    def recheck(
        self, prepared: PreparedProposal, latest_snapshot: CanonicalSnapshot
    ) -> PreparedProposal | ValidationFailure:
        for dependency in prepared.dependencies:
            failure = self._recheck_dependency(dependency, latest_snapshot)
            if failure is not None:
                return failure
        return prepared

    def _validate_action(
        self, action: Action, snapshot: CanonicalSnapshot
    ) -> ValidationFailure | None:
        if not isinstance(action, Action) or not isinstance(action.type, ActionType):
            return ValidationFailure(ReasonCode.MODEL_PROTOCOL_INVALID, False)
        if not isinstance(action.parameters, Mapping):
            return ValidationFailure(ReasonCode.INVALID_ACTION_PARAMETERS, False)
        if action.coordinate is None:
            if action.type in _POINTER_TARGET_ACTIONS or action.coordinate_space is not None:
                return ValidationFailure(ReasonCode.INVALID_ACTION_PARAMETERS, False)
        elif (
            action.type not in _POINTER_TARGET_ACTIONS
            or not isinstance(action.coordinate, Point)
            or not isinstance(action.coordinate_space, str)
            or not action.coordinate_space
        ):
            return ValidationFailure(ReasonCode.INVALID_ACTION_PARAMETERS, False)
        if action.type in self.denied_actions:
            return ValidationFailure(ReasonCode.ACTION_RESTRICTED, False)
        if action.coordinate is not None:
            if not math.isfinite(action.coordinate.x) or not math.isfinite(action.coordinate.y):
                return ValidationFailure(ReasonCode.INVALID_ACTION_PARAMETERS, False)
            if action.coordinate_space != snapshot.coordinate_space.id:
                return ValidationFailure(ReasonCode.INVALID_COORDINATE_SPACE, False)
            if not snapshot.coordinate_space.bounds.contains(action.coordinate):
                return ValidationFailure(ReasonCode.OUTSIDE_DESKTOP, False)
        if not self._valid_parameters(action):
            return ValidationFailure(ReasonCode.INVALID_ACTION_PARAMETERS, False)
        if self._action_validator is not None:
            reason = self._action_validator(action)
            if reason is not None:
                return ValidationFailure(reason, False)
        return None

    @staticmethod
    def _valid_parameters(action: Action) -> bool:
        params = action.parameters
        number = lambda value: isinstance(value, (int, float)) and not isinstance(value, bool)
        nonnegative = lambda value: number(value) and math.isfinite(float(value)) and value >= 0
        positive_int = lambda value: isinstance(value, int) and not isinstance(value, bool) and value > 0
        if "duration" in params and not nonnegative(params["duration"]):
            return False
        if "interval" in params and not nonnegative(params["interval"]):
            return False
        if "button" in params and params["button"] not in {"left", "middle", "right"}:
            return False
        if action.type == ActionType.POINTER_CLICK:
            return (
                params.get("event") in {None, "down", "up"}
                and ("clicks" not in params or positive_int(params["clicks"]))
            )
        if action.type == ActionType.POINTER_SCROLL:
            return number(params.get("clicks")) and params.get("axis") in {None, "vertical", "horizontal"}
        if action.type == ActionType.KEYBOARD_KEY:
            return (
                isinstance(params.get("key"), str) and bool(params["key"].strip())
                and params.get("event") in {None, "down", "up"}
                and ("presses" not in params or positive_int(params["presses"]))
            )
        if action.type == ActionType.KEYBOARD_SHORTCUT:
            keys = params.get("keys")
            return isinstance(keys, (list, tuple)) and bool(keys) and all(
                isinstance(key, str) and bool(key.strip()) for key in keys
            )
        if action.type == ActionType.KEYBOARD_TEXT:
            return isinstance(params.get("text"), str)
        if action.type == ActionType.PLATFORM_INVOKE:
            return isinstance(params.get("capability_id"), str) and bool(params["capability_id"].strip())
        if action.type == ActionType.APPLICATION_LAUNCH:
            return isinstance(params.get("app_id"), str) and bool(params["app_id"].strip())
        return True

    def _dependency(
        self,
        action_index: int,
        action: Action,
        snapshot: CanonicalSnapshot,
        *,
        require_cursor_origin: bool,
    ) -> ActionDependency | ValidationFailure | None:
        target_id: str | None = None
        point: Point | None = None
        require_hit = False
        cursor_origin = None
        if action.type in _POINTER_TARGET_ACTIONS:
            point = action.coordinate
            if action.parameters.get("relative"):
                if snapshot.cursor is None:
                    return ValidationFailure(ReasonCode.CAPABILITY_UNAVAILABLE, False)
                cursor_origin = snapshot.cursor if require_cursor_origin else None
            if action.type == ActionType.POINTER_DRAG:
                if snapshot.cursor is None:
                    return ValidationFailure(ReasonCode.CAPABILITY_UNAVAILABLE, False)
                point = snapshot.cursor
                cursor_origin = snapshot.cursor if require_cursor_origin else None
            if not self._descriptor.capabilities.stacking.hit_test:
                return ValidationFailure(ReasonCode.CAPABILITY_UNAVAILABLE, False)
            target_id = self._hit_test(point, snapshot) if point is not None else None
            if target_id is None:
                return ValidationFailure(ReasonCode.TARGET_NOT_FOUND, False)
            require_hit = True
        elif action.type in _KEYBOARD_ACTIONS:
            active = snapshot.active_window()
            target_id = active.window_id if active is not None else None

        target = snapshot.window(target_id) if target_id else None
        if action.type in _KEYBOARD_ACTIONS and target is None:
            # Wayland shell surfaces such as Deepin's launcher and its search
            # field need keyboard focus but are not advertised in Treeland's
            # normal window tree.  Do not reject their visible GUI input merely
            # because there is no compositor active window to bind it to.
            return None
        if target is not None and target.visible is not True:
            reason = ReasonCode.TARGET_OCCLUDED if target.visible is False else ReasonCode.CAPABILITY_UNAVAILABLE
            return ValidationFailure(reason, False)
        if target_id is None and point is None and action.type not in _KEYBOARD_ACTIONS:
            return None
        identity = tuple(
            (key, value) for key, value in (("app_id", target.app_id), ("title", target.title))
            if target is not None and value is not None
        )
        return ActionDependency(
            action_index=action_index,
            coordinate_space_id=snapshot.coordinate_space.id if point is not None else None,
            coordinate_space_version=snapshot.coordinate_space.version if point is not None else None,
            target_window_id=target_id,
            target_identity=identity,
            identity_required=self._descriptor.capabilities.window_identity != "unavailable",
            required_visible=target is not None,
            required_active=action.type in _KEYBOARD_ACTIONS,
            expected_geometry=target.geometry if target is not None else None,
            hit_test_point=point,
            required_hit_window_id=target_id if require_hit else None,
            cursor_origin=cursor_origin,
        )


    def _recheck_dependency(
        self, dependency: ActionDependency, snapshot: CanonicalSnapshot
    ) -> ValidationFailure | None:
        if dependency.coordinate_space_id is not None:
            if snapshot.coordinate_space.id != dependency.coordinate_space_id:
                return ValidationFailure(ReasonCode.COORDINATE_SPACE_CHANGED, True)
            if snapshot.coordinate_space.version != dependency.coordinate_space_version:
                return ValidationFailure(ReasonCode.COORDINATE_SPACE_CHANGED, True)
        target = snapshot.window(dependency.target_window_id) if dependency.target_window_id else None
        if dependency.target_window_id and target is None:
            return ValidationFailure(ReasonCode.TARGET_DISAPPEARED, True)
        if target is not None:
            if dependency.required_visible and target.visible is not True:
                return ValidationFailure(ReasonCode.TARGET_OCCLUDED, True)
            if dependency.required_active and target.active is not True:
                return ValidationFailure(ReasonCode.TARGET_IDENTITY_CHANGED, True)
            if dependency.identity_required:
                current = tuple((key, getattr(target, key)) for key, _ in dependency.target_identity)
                if current != dependency.target_identity:
                    return ValidationFailure(ReasonCode.TARGET_IDENTITY_CHANGED, True)
            if dependency.hit_test_point is not None and not target.geometry.contains(dependency.hit_test_point):
                return ValidationFailure(ReasonCode.TARGET_GEOMETRY_INVALIDATED, True)
        if dependency.required_hit_window_id is not None and dependency.hit_test_point is not None:
            if self._hit_test(dependency.hit_test_point, snapshot) != dependency.required_hit_window_id:
                return ValidationFailure(ReasonCode.HIT_TEST_CHANGED, True)
        if dependency.cursor_origin is not None and snapshot.cursor != dependency.cursor_origin:
            return ValidationFailure(ReasonCode.CURSOR_ORIGIN_CHANGED, True)
        return None
