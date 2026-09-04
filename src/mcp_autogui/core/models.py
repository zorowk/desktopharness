"""Canonical protocol objects for AutoUI v2.

This module deliberately has no dependency on a compositor, model, input
library, desktop environment, or MCP transport.  Adapters translate their raw
data into these finite structures before it reaches the core.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, is_dataclass
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any, Mapping
from uuid import uuid4


SCHEMA_VERSION = "1"


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid4().hex}"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def to_primitive(value: Any) -> Any:
    """Serialize protocol objects without leaking adapter-specific objects."""
    if isinstance(value, StrEnum):
        return value.value
    if is_dataclass(value):
        return {key: to_primitive(item) for key, item in asdict(value).items()}
    if isinstance(value, Mapping):
        return {str(key): to_primitive(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_primitive(item) for item in value]
    return value


class WindowRole(StrEnum):
    NORMAL = "normal"
    DESKTOP = "desktop"
    PANEL = "panel"
    OVERLAY = "overlay"
    LOCKSCREEN = "lockscreen"
    DIALOG = "dialog"
    UNKNOWN = "unknown"


class StackingModel(StrEnum):
    TOTAL_ORDER = "total-order"
    PARTIAL_ORDER = "partial-order"
    HIT_TEST = "hit-test"
    TOPMOST_ONLY = "topmost-only"
    UNAVAILABLE = "unavailable"


class ActionType(StrEnum):
    POINTER_MOVE = "pointer.move"
    POINTER_CLICK = "pointer.click"
    POINTER_DOUBLE_CLICK = "pointer.double_click"
    POINTER_DRAG = "pointer.drag"
    POINTER_SCROLL = "pointer.scroll"
    KEYBOARD_KEY = "keyboard.key"
    KEYBOARD_SHORTCUT = "keyboard.shortcut"
    KEYBOARD_TEXT = "keyboard.text"
    PLATFORM_INVOKE = "platform.invoke"
    APPLICATION_LAUNCH = "application.launch"
    DONE = "done"


class PolicyStatus(StrEnum):
    ALLOW = "allow"
    DENY = "deny"
    CONFIRM = "confirm"
    INVALID = "invalid"
    STALE = "stale"


class ExecutionStatus(StrEnum):
    DELIVERED = "delivered"
    REJECTED = "rejected"
    FAILED = "failed"
    UNKNOWN = "unknown"


class AssertionStatus(StrEnum):
    PASSED = "passed"
    FAILED = "failed"
    UNKNOWN = "unknown"
    CONFLICT = "conflict"


class TaskStatus(StrEnum):
    CONTINUE = "continue"
    RETRY = "retry"
    NEEDS_EVIDENCE = "needs-evidence"
    COMPLETED = "completed"
    FAILED = "failed"


class EvidenceConfidence(StrEnum):
    DETERMINISTIC = "deterministic"
    DERIVED = "derived"
    PROBABILISTIC = "probabilistic"
    MODEL_CLAIM = "model-claim"
    HUMAN_ANNOTATION = "human-annotation"


class AttributionEventKind(StrEnum):
    ERROR = "error"
    SAFE_REFUSAL = "safe-refusal"
    POLICY_DECISION = "policy-decision"
    EXTERNAL_CHANGE = "external-change"
    INCOMPLETE = "incomplete"
    INSUFFICIENT_EVIDENCE = "insufficient-evidence"


class AttributionEvidenceStatus(StrEnum):
    CONFIRMED = "confirmed"
    INFERRED = "inferred"
    INSUFFICIENT = "insufficient"


@dataclass(frozen=True, slots=True)
class Attribution:
    attribution_id: str
    event_kind: AttributionEventKind
    stage: str
    owner: str
    code: str
    evidence_status: AttributionEvidenceStatus
    primary: bool
    summary: str
    evidence_refs: tuple[str, ...] = ()
    schema_version: str = SCHEMA_VERSION


@dataclass(frozen=True, slots=True)
class Point:
    x: float
    y: float


@dataclass(frozen=True, slots=True)
class Rect:
    x: float
    y: float
    width: float
    height: float

    def __post_init__(self) -> None:
        if self.width < 0 or self.height < 0:
            raise ValueError("rectangle dimensions cannot be negative")

    def contains(self, point: Point) -> bool:
        return (
            self.x <= point.x < self.x + self.width
            and self.y <= point.y < self.y + self.height
        )


@dataclass(frozen=True, slots=True)
class CoordinateSpace:
    id: str
    bounds: Rect
    version: str | None = None


@dataclass(frozen=True, slots=True)
class OutputFact:
    output_id: str
    geometry: Rect
    scale: float | None = None


@dataclass(frozen=True, slots=True)
class CanonicalWindowFact:
    window_id: str
    geometry: Rect
    app_id: str | None = None
    title: str | None = None
    visible: bool | None = None
    active: bool | None = None
    z_index: float | None = None
    workspace_id: str | None = None
    output_id: str | None = None
    role: WindowRole = WindowRole.UNKNOWN


@dataclass(frozen=True, slots=True)
class StackingCapabilities:
    model: StackingModel
    z_index: bool = False
    is_above: bool = False
    occlusion: bool = False
    hit_test: bool = False


@dataclass(frozen=True, slots=True)
class AdapterCapabilities:
    window_tree: bool
    cursor_position: bool
    desktop_geometry: bool
    stacking: StackingCapabilities
    active_window: bool = False
    workspace: bool = False
    window_identity: str = "unavailable"
    child_controls: bool = False
    window_text: bool = False


@dataclass(frozen=True, slots=True)
class AdapterDescriptor:
    adapter_id: str
    capabilities: AdapterCapabilities
    schema_version: str = SCHEMA_VERSION


@dataclass(frozen=True, slots=True)
class CanonicalSnapshot:
    snapshot_id: str
    captured_at: str
    environment_version: str
    coordinate_space: CoordinateSpace
    outputs: tuple[OutputFact, ...]
    cursor: Point | None
    windows: tuple[CanonicalWindowFact, ...]
    raw_artifact_ref: str | None = None
    schema_version: str = SCHEMA_VERSION

    def window(self, window_id: str) -> CanonicalWindowFact | None:
        return next((item for item in self.windows if item.window_id == window_id), None)

    def active_window(self) -> CanonicalWindowFact | None:
        return next((item for item in self.windows if item.active is True), None)


@dataclass(frozen=True, slots=True)
class FrameReference:
    frame_id: str
    captured_at: str
    image_ref: str
    pixel_size: tuple[int, int]
    from_space: str = "frame-pixel"
    to_space: str = "desktop-logical"
    transform_ref: str | None = None
    schema_version: str = SCHEMA_VERSION


@dataclass(frozen=True, slots=True)
class TaskPermissions:
    actions: frozenset[ActionType]
    semantic_intents: frozenset[str] = frozenset()


@dataclass(frozen=True, slots=True)
class AssertionSpec:
    assertion_id: str
    path: str
    operator: str
    expected: Any = None
    required: bool = True
    recoverable: bool = True
    subject: Mapping[str, Any] = field(default_factory=dict)
    providers: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class TaskLimits:
    max_steps: int = 10
    max_retries: int = 1

    def __post_init__(self) -> None:
        if self.max_steps < 1 or self.max_retries < 0:
            raise ValueError("invalid task limits")


@dataclass(frozen=True, slots=True)
class TaskContract:
    task_id: str
    goal: str
    permissions: TaskPermissions
    assertions: tuple[AssertionSpec, ...] = ()
    limits: TaskLimits = field(default_factory=TaskLimits)
    policy_profile: str = "desktop-safe-default"
    verification_profile: str = "default"
    policy_overrides: Mapping[str, str] = field(default_factory=dict)
    schema_version: str = SCHEMA_VERSION


@dataclass(frozen=True, slots=True)
class Action:
    type: ActionType
    coordinate: Point | None = None
    coordinate_space: str | None = None
    parameters: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        coordinate_actions = {
            ActionType.POINTER_MOVE,
            ActionType.POINTER_CLICK,
            ActionType.POINTER_DOUBLE_CLICK,
            ActionType.POINTER_DRAG,
        }
        if self.type in coordinate_actions and self.coordinate is None:
            raise ValueError(f"{self.type.value} requires a coordinate")
        if self.coordinate is not None and not self.coordinate_space:
            raise ValueError("coordinate actions must declare coordinate_space")


@dataclass(frozen=True, slots=True)
class ActionProposal:
    proposal_id: str
    source: str
    based_on_snapshot: str
    action: Action
    semantic_intent: str | None = None
    expected_effect: Mapping[str, Any] = field(default_factory=dict)
    debug_ref: str | None = None
    schema_version: str = SCHEMA_VERSION


@dataclass(frozen=True, slots=True)
class SemanticTag:
    tag: str
    source: str
    evidence_ref: str | None
    confidence: EvidenceConfidence


@dataclass(frozen=True, slots=True)
class SemanticResolution:
    semantic_resolution_id: str
    proposal_id: str
    status: str
    tags: tuple[SemanticTag, ...]
    schema_version: str = SCHEMA_VERSION


@dataclass(frozen=True, slots=True)
class ProposalGuard:
    guard_id: str
    proposal_id: str
    derived_from_snapshot: str
    coordinate_space_id: str | None = None
    coordinate_space_version: str | None = None
    target_window_id: str | None = None
    target_identity: Mapping[str, Any] = field(default_factory=dict)
    identity_required: bool = False
    required_visible: bool = False
    required_active: bool = False
    expected_geometry: Rect | None = None
    geometry_policy: str | None = None
    hit_test_point: Point | None = None
    required_hit_window_id: str | None = None
    cursor_origin: Point | None = None
    schema_version: str = SCHEMA_VERSION


@dataclass(frozen=True, slots=True)
class PolicyDecision:
    proposal_id: str
    status: PolicyStatus
    reason_code: str
    resolved_target: Mapping[str, Any] = field(default_factory=dict)
    guard_ref: str | None = None
    semantic_resolution_ref: str | None = None
    debug_ref: str | None = None
    schema_version: str = SCHEMA_VERSION


@dataclass(frozen=True, slots=True)
class ExecutionReceipt:
    execution_id: str
    proposal_id: str
    status: ExecutionStatus
    executed_action: Action | None
    started_at: str
    finished_at: str
    error_code: str | None = None
    debug_ref: str | None = None
    schema_version: str = SCHEMA_VERSION


@dataclass(frozen=True, slots=True)
class EvidenceRecord:
    evidence_id: str
    provider: str
    collected_at: str
    subject: Mapping[str, Any]
    facts: Mapping[str, Any]
    confidence: EvidenceConfidence
    method: str
    valid_at_collection: bool | None
    expires_on_environment_change: bool | None
    operation_id: str
    frame_id: str | None = None
    raw_artifact_ref: str | None = None
    schema_version: str = SCHEMA_VERSION


@dataclass(frozen=True, slots=True)
class ExcludedEvidence:
    evidence_id: str
    reason: str


@dataclass(frozen=True, slots=True)
class AssertionResult:
    assertion_id: str
    expression: AssertionSpec
    status: AssertionStatus
    evidence_refs: tuple[str, ...]
    evaluated_at: str
    reason: str
    excluded_evidence: tuple[ExcludedEvidence, ...] = ()
    schema_version: str = SCHEMA_VERSION


@dataclass(frozen=True, slots=True)
class TaskState:
    task_id: str
    status: TaskStatus = TaskStatus.CONTINUE
    step: int = 0
    retries: int = 0
    completed_assertions: tuple[str, ...] = ()
    failed_assertions: tuple[str, ...] = ()
    verified_facts: tuple[str, ...] = ()
    schema_version: str = SCHEMA_VERSION


@dataclass(frozen=True, slots=True)
class LedgerEvent:
    event_id: str
    task_id: str
    sequence: int
    occurred_at: str
    event_type: str
    epistemic_type: str
    object_ref: str
    caused_by: tuple[str, ...] = ()
    snapshot_id: str | None = None
    artifact_refs: tuple[str, ...] = ()
    debug_ref: str | None = None
    schema_version: str = SCHEMA_VERSION


@dataclass(frozen=True, slots=True)
class ModelContext:
    model_context_id: str
    task_id: str
    based_on_snapshot: str
    frame: FrameReference | None
    goal: str
    current_step: int
    pending_assertions: tuple[str, ...]
    verified_facts: tuple[Mapping[str, Any], ...]
    recent_execution_receipt: Mapping[str, Any] | None
    assertion_feedback: tuple[Mapping[str, Any], ...]
    constraints: Mapping[str, Any]
    ledger_event_refs: tuple[str, ...]
    spatial_projection: Mapping[str, Any] = field(default_factory=dict)
    strategy: str = "compact"
    schema_version: str = SCHEMA_VERSION


CORE_OBJECT_TYPES: tuple[type[Any], ...] = (
    AdapterDescriptor,
    CanonicalSnapshot,
    ActionProposal,
    PolicyDecision,
    ExecutionReceipt,
    AssertionResult,
)
