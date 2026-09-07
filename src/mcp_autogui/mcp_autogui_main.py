#coding: utf-8

import atexit
import os
import sys
import time
import asyncio
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from functools import partial
import json
import subprocess
from .qwen_backend import QwenBackendClient
from .adapters.evidence import AtSpiEvidenceProvider, CompositorWindowEvidenceProvider, OmniParserEvidenceProvider
from .adapters.proposal import QwenCUAProposalProvider
from .core.models import (
    Action,
    ActionProposal,
    ActionType,
    AssertionSpec,
    Point,
    TaskContract,
    TaskLimits,
    TaskPermissions,
    new_id,
)
from .core.orchestrator import CoreOrchestrator
from .core.audit import audit_components_from_config
from .desktop_backend import DEFAULT_DESKTOP_BACKEND, create_desktop_backend
from .facade import GuiRunFacade
from .spatial_fusion import flatten_treeland_windows

INPUT_IMAGE_SIZE = 960
DEFAULT_APPLICATION_WAIT_TIMEOUT_S = 3.0
MAX_APPLICATION_WAIT_TIMEOUT_S = 30.0
APPLICATION_WAIT_POLL_INTERVAL_S = 0.2


def _expected_active_app_id(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("expected_active_app_id must be a string")
    return value.strip()


def _application_wait_timeout(value: float) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ValueError("application_wait_timeout_s must be numeric")
    timeout = float(value)
    if not 0 <= timeout <= MAX_APPLICATION_WAIT_TIMEOUT_S:
        raise ValueError(
            "application_wait_timeout_s must be between 0 and "
            f"{MAX_APPLICATION_WAIT_TIMEOUT_S:g}"
        )
    return timeout


def _active_window_summary(tree: dict[str, object]) -> dict[str, object] | None:
    """Return a compact identity summary of the active Treeland window."""
    for window in flatten_treeland_windows(tree):
        if window.get("active") is True:
            return {
                "appId": window.get("appId"),
                "title": window.get("title"),
                "container": window.get("container"),
                "workspace": window.get("workspace"),
            }
    return None


def _capture_post_action_frame(
    capture_frame,
    read_tree,
    expected_app_id: str,
    timeout_s: float,
) -> tuple[bytes | None, tuple[int, int] | None, dict | None, dict]:
    """Poll the lightweight tree, then capture one final post-action frame."""
    started = time.monotonic()
    deadline = started + timeout_s
    attempts = 0
    poll_error = None
    observed_tree = None

    if expected_app_id:
        while True:
            attempts += 1
            try:
                observed_tree = read_tree()
                poll_error = None
            except Exception as exc:
                poll_error = f"{type(exc).__name__}: {exc}"

            active_window = (
                _active_window_summary(observed_tree)
                if observed_tree is not None
                else None
            )
            actual_app_id = (active_window or {}).get("appId")
            if actual_app_id == expected_app_id:
                break

            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            time.sleep(min(APPLICATION_WAIT_POLL_INTERVAL_S, remaining))

    observation_error = None
    try:
        latest_frame = capture_frame()
    except Exception as exc:
        observation_error = f"{type(exc).__name__}: {exc}"
        latest_frame = (None, None, observed_tree)
    if attempts == 0:
        attempts = 1

    waited_ms = round((time.monotonic() - started) * 1000, 2)
    tree = latest_frame[2]
    active_window = _active_window_summary(tree) if tree is not None else None
    actual_app_id = (active_window or {}).get("appId")
    if not expected_app_id:
        status = "not-requested"
    elif actual_app_id == expected_app_id:
        status = "matched"
    elif tree is None:
        status = "observation-unavailable"
    else:
        status = "timeout"
    return (
        latest_frame[0],
        latest_frame[1],
        latest_frame[2],
        {
            "status": status,
            "expected_active_app_id": expected_app_id or None,
            "actual_active_app_id": actual_app_id,
            "attempts": attempts,
            "waited_ms": waited_ms,
            "poll_error": poll_error,
            "observation_error": observation_error,
        },
    )


def _active_app_task_validation(
    expected_app_id: str,
    active_before: dict | None,
    active_after: dict | None,
    application_wait: dict,
) -> dict | None:
    """Evaluate the lightweight active-app postcondition for one session."""
    if not expected_app_id:
        return None
    before_app_id = (active_before or {}).get("appId")
    actual_app_id = (active_after or {}).get("appId")
    if actual_app_id == expected_app_id:
        status = "passed"
        reason = None
    elif active_after is None:
        status = "unknown"
        reason = "active_window_unavailable"
    elif actual_app_id and actual_app_id != before_app_id:
        status = "failed"
        reason = "wrong_application_active"
    else:
        status = "failed"
        reason = "expected_application_not_observed"
    return {
        "assertion": "active_window.appId == expected_active_app_id",
        "status": status,
        "reason": reason,
        "expected_active_app_id": expected_app_id,
        "actual_active_app_id": actual_app_id,
        "active_window_before": deepcopy(active_before),
        "active_window_after": deepcopy(active_after),
        "attempts": application_wait.get("attempts"),
        "waited_ms": application_wait.get("waited_ms"),
    }


def get_treeland_layout_tree(timeout=35):
    """Read Treeland's window tree from its built-in debug client."""
    result = subprocess.run(
        ["treeland-debug", "--tree"],
        check=True,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    output = result.stdout.strip()
    if not output:
        raise RuntimeError("treeland-debug --tree returned no window-tree data")
    try:
        return json.loads(output)
    except json.JSONDecodeError as exc:
        raise RuntimeError("treeland-debug --tree returned invalid JSON") from exc


def _env_enabled(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def mcp_autogui_main(
    mcp,
    *,
    desktop_backend_kind: str = DEFAULT_DESKTOP_BACKEND,
    proposal_provider_config: dict[str, object] | None = None,
    evidence_provider_config: dict[str, object] | None = None,
    audit_config: dict[str, object] | None = None,
):
    qwen_backend = QwenBackendClient(proposal_provider_config)
    backend_close = getattr(qwen_backend, "close", None)
    worker_pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="autoui-mcp")

    def close_runtime() -> None:
        worker_pool.shutdown(wait=True, cancel_futures=True)
        if callable(backend_close):
            backend_close()

    atexit.register(close_runtime)

    async def run_blocking(function, /, *args, **kwargs):
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(worker_pool, partial(function, *args, **kwargs))

    store, ledger = audit_components_from_config(audit_config)
    desktop_backend = create_desktop_backend(
        desktop_backend_kind,
        tree_reader=lambda: get_treeland_layout_tree(),
        artifact_store=store,
    )
    compositor = desktop_backend.compositor

    configured_evidence = evidence_provider_config or {}
    compositor_config = configured_evidence.get("compositor_window", {})
    compositor_enabled = (
        bool(compositor_config.get("enabled", True))
        if isinstance(compositor_config, dict)
        else True
    )
    evidence_providers = [CompositorWindowEvidenceProvider()] if compositor_enabled else []
    atspi_config = configured_evidence.get("atspi", {})
    atspi_enabled = bool(atspi_config.get("enabled", False)) if isinstance(atspi_config, dict) else False
    if atspi_enabled and AtSpiEvidenceProvider.available():
        evidence_providers.append(AtSpiEvidenceProvider())
    omni_config = configured_evidence.get("omniparser", {})
    omni_enabled = (
        bool(omni_config.get("enabled", False))
        if isinstance(omni_config, dict)
        else _env_enabled("GUI_OMNIPARSER_ENABLED")
    )
    if evidence_provider_config is None:
        omni_enabled = _env_enabled("GUI_OMNIPARSER_ENABLED")
    if omni_enabled:
        endpoint = (
            str(omni_config.get("endpoint") or "").strip()
            if isinstance(omni_config, dict)
            else ""
        )
        if evidence_provider_config is None:
            endpoint = os.environ.get("OMNI_PARSER_SERVER", "").strip()
        if not endpoint:
            raise RuntimeError(
                "OMNI_PARSER_SERVER is required when GUI_OMNIPARSER_ENABLED is enabled."
            )

        def capture_omniparser_frame() -> bytes:
            image, _, _ = desktop_backend.capture_observation()
            return image

        evidence_providers.append(
            OmniParserEvidenceProvider(endpoint, capture_omniparser_frame, store)
        )

    runtime = CoreOrchestrator(
        compositor,
        desktop_backend.executor,
        proposal_provider=QwenCUAProposalProvider(qwen_backend, store),
        frame_provider=desktop_backend.frame_provider,
        application_launcher=desktop_backend.application_launcher,
        evidence_providers=tuple(evidence_providers),
        policy_providers=desktop_backend.policy_providers,
        store=store,
        ledger=ledger,
    )
    facade = GuiRunFacade(runtime)
    capture_frame = desktop_backend.capture_observation



    @mcp.tool()
    async def gui_run(
        operation: str,
        task_id: str = '',
        task_contract: dict | None = None,
        proposal: dict | None = None,
        proposal_id: str = '',
        confirmed: bool = False,
        strategy: str = 'compact',
        object_ref: str = '',
        diagnostic: bool = False,
        max_iterations: int | None = None,
    ) -> dict:
        """运行统一 AutoUI 协议操作。

        支持 ``describe``、``observe``、``propose``、``decide``、
        ``execute``、``evaluate``/``verify``、``run``、``status``、``reset``、
        ``trace``。默认返回对象引用；传 ``diagnostic=true`` 或使用 ``trace``
        查看详细对象。
        """
        return await run_blocking(
            facade.handle,
            operation,
            task_id=task_id,
            task_contract=task_contract,
            proposal=proposal,
            proposal_id=proposal_id,
            confirmed=confirmed,
            strategy=strategy,
            object_ref=object_ref,
            diagnostic=diagnostic,
            max_iterations=max_iterations,
        )

    @mcp.tool()
    async def desktop_capabilities_list(category: str = '') -> list[dict]:
        """List controller-owned Deepin desktop shortcuts and their policies.

        This reads the packaged keybinding schema.  Results marked
        ``source=default-schema`` are defaults, not proof that a user has not
        changed the shortcut at runtime.  ``auto_invokable`` is the controller
        policy decision; it is deliberately narrower than ``enabled``.
        """
        requested_category = category.strip().lower()
        items = desktop_backend.list_capabilities()
        if requested_category:
            items = [
                item for item in items
                if item["category"].lower() == requested_category
            ]
        return items

    @mcp.tool()
    async def desktop_shortcut_invoke(capability_id: str) -> dict:
        """Invoke one low-risk, controller-approved Deepin shortcut.

        The capability must be returned by ``desktop_capabilities_list`` and
        marked ``auto_invokable``.  This API never executes the schema's raw
        command/DBus trigger value.
        """
        capability = desktop_backend.find_capability(capability_id.strip())
        if capability is None:
            raise ValueError("unknown desktop capability_id")
        if not capability["enabled"]:
            raise ValueError("desktop capability is disabled in the default schema")
        if not capability["auto_invokable"]:
            raise PermissionError(
                "controller policy does not allow automatic invocation of this capability"
            )
        hotkeys = capability["normalized_hotkeys"]
        if not hotkeys:
            raise ValueError("desktop capability has no keyboard shortcut")
        keys = hotkeys[0]
        task_id = new_id("shortcut-task")
        runtime.register_task(
            TaskContract(
                task_id=task_id,
                goal=f"Invoke platform capability {capability_id}",
                permissions=TaskPermissions(
                    frozenset({ActionType.PLATFORM_INVOKE}),
                    frozenset({"navigation"}),
                ),
                limits=TaskLimits(max_steps=1, max_retries=0),
            )
        )
        observed = runtime.observe(task_id)
        raw_before = store.require(observed.raw_artifact_ref)
        before = _active_window_summary(raw_before)
        proposal = ActionProposal(
            proposal_id=new_id("proposal"),
            source="desktop-shortcut",
            based_on_snapshot=observed.snapshot_id,
            action=Action(
                ActionType.PLATFORM_INVOKE,
                parameters={"capability_id": capability_id.strip()},
            ),
        )
        runtime.submit_proposal(task_id, proposal)
        decision = runtime.decide(proposal.proposal_id)
        if decision.status.value != "allow":
            raise PermissionError(f"policy refused shortcut: {decision.reason_code}")
        receipt = runtime.execute(proposal.proposal_id)
        if receipt.status.value != "delivered":
            return {
                "status": "failed",
                "capability": capability,
                "executed_keys": [],
                "reason": receipt.error_code,
            }
        _, _, post_tree, evidence = _capture_post_action_frame(
            capture_frame,
            desktop_backend.read_raw_tree,
            "",
            0,
        )
        return {
            "status": "success",
            "capability": capability,
            "executed_keys": keys,
            "evidence": {
                "active_window_before": before,
                "active_window_after": _active_window_summary(post_tree),
                "observation": evidence,
            },
        }

    @mcp.tool()
    async def desktop_applications_list(query: str = '', limit: int = 30) -> list[dict]:
        """Resolve installed desktop applications to safe dde-am application IDs.

        The catalogue contains discovery metadata only; it intentionally omits
        desktop-entry Exec commands.  Use the returned ``app_id`` with
        ``desktop_application_launch``.
        """
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 100:
            raise ValueError("limit must be an integer between 1 and 100")
        needle = query.strip().casefold()
        applications = desktop_backend.list_applications()
        if needle:
            applications = [
                item for item in applications
                if needle in item["app_id"].casefold()
                or needle in item["display_name"].casefold()
                or needle in (item["display_name_zh_cn"] or "").casefold()
            ]
        return applications[:limit]

    @mcp.tool()
    async def desktop_application_launch(
        app_id: str,
        expected_active_app_id: str = '',
        application_wait_timeout_s: float = DEFAULT_APPLICATION_WAIT_TIMEOUT_S,
    ) -> dict:
        """Launch a Deepin application by ID through dde-am and collect evidence.

        Only a plain application ID is accepted.  Paths, URIs, command mode,
        and arbitrary arguments are not part of this capability.  Supplying an
        expected Treeland app ID enables deterministic post-launch validation.
        """
        resolved_app_id = desktop_backend.validate_application_id(app_id)
        expected_app_id = _expected_active_app_id(expected_active_app_id)
        timeout_s = _application_wait_timeout(application_wait_timeout_s)
        task_id = new_id("application-task")
        assertions = (
            AssertionSpec(
                "application-active",
                "active_window.app_id",
                "equals",
                expected_app_id,
            ),
        ) if expected_app_id else ()
        runtime.register_task(
            TaskContract(
                task_id=task_id,
                goal=f"Launch application {resolved_app_id}",
                permissions=TaskPermissions(
                    frozenset({ActionType.APPLICATION_LAUNCH}),
                    frozenset({"open_application"}),
                ),
                assertions=assertions,
                limits=TaskLimits(max_steps=1, max_retries=0),
                verification_profile="application-open",
            )
        )
        observed = await run_blocking(runtime.observe, task_id)
        active_before = _active_window_summary(store.require(observed.raw_artifact_ref))
        proposal = ActionProposal(
            proposal_id=new_id("proposal"),
            source="desktop-application-launch",
            based_on_snapshot=observed.snapshot_id,
            action=Action(
                ActionType.APPLICATION_LAUNCH,
                parameters={"app_id": resolved_app_id},
            ),
            semantic_intent="open_application",
            expected_effect={"active_app_id": expected_app_id} if expected_app_id else {},
        )
        runtime.submit_proposal(task_id, proposal)
        decision = runtime.decide(proposal.proposal_id)
        if decision.status.value != "allow":
            raise PermissionError(f"policy refused application launch: {decision.reason_code}")
        receipt = await run_blocking(runtime.execute, proposal.proposal_id)
        result = launcher.result_for(proposal.proposal_id)
        if receipt.status.value != "delivered":
            return {
                "status": "failed",
                "app_id": resolved_app_id,
                "returncode": getattr(result, "returncode", None),
                "stdout": getattr(result, "stdout", None),
                "stderr": getattr(result, "stderr", None),
                "reason": receipt.error_code,
            }

        _, _, post_tree, application_wait = _capture_post_action_frame(
            capture_frame,
            desktop_backend.read_raw_tree,
            expected_app_id,
            timeout_s,
        )
        active_after = _active_window_summary(post_tree)
        task_validation = _active_app_task_validation(
            expected_app_id,
            active_before,
            active_after,
            application_wait,
        )
        _, assertion_results, task_state = await run_blocking(runtime.evaluate, task_id)
        return {
            "status": (
                "success"
                if task_validation is None or task_validation["status"] == "passed"
                else "partial"
            ),
            "app_id": resolved_app_id,
            "returncode": getattr(result, "returncode", 0),
            "stdout": getattr(result, "stdout", ""),
            "stderr": getattr(result, "stderr", ""),
            "evidence": {
                "active_window_before": active_before,
                "active_window_after": active_after,
                "application_wait": application_wait,
            },
            "task_validation": task_validation,
        }
