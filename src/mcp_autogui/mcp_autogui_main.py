import atexit
import asyncio
from concurrent.futures import ThreadPoolExecutor
from functools import partial

from .core.audit import recording_components_from_config
from .core.context_builder import ContextBuilder
from .core.orchestrator import CoreOrchestrator
from .desktop_backend import DEFAULT_DESKTOP_BACKEND, create_desktop_backend
from .desktop_transactions import CoreDesktopTransactionRunner
from .facade import AutoUIFacade
from .provider_registry import (
    ProviderBuildContext,
    create_evidence_providers,
    create_proposal_provider,
)
from .runtime_description import RuntimeDescription


def mcp_autogui_main(
    mcp,
    *,
    desktop_backend_kind: str = DEFAULT_DESKTOP_BACKEND,
    proposal_provider_config: dict[str, object] | None = None,
    denied_actions=frozenset(),
    evidence_provider_config: dict[str, object] | None = None,
    recording_config: dict[str, object] | None = None,
    effective_config: dict[str, object] | None = None,
    run_blocking_override=None,
):
    worker_pool = (
        None if run_blocking_override is not None
        else ThreadPoolExecutor(max_workers=4, thread_name_prefix="autoui-mcp")
    )

    async def run_blocking(function, /, *args, **kwargs):
        if run_blocking_override is not None:
            return await run_blocking_override(function, *args, **kwargs)
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(worker_pool, partial(function, *args, **kwargs))

    recording = recording_components_from_config(recording_config)
    store = recording.runtime_store
    desktop_backend = create_desktop_backend(
        desktop_backend_kind,
        artifact_store=store,
    )
    compositor = desktop_backend.compositor
    proposal_runtime = create_proposal_provider(
        proposal_provider_config or {"kind": "qwen-cua"}, store
    )
    evidence_providers = create_evidence_providers(
        evidence_provider_config or {"compositor_window": {"enabled": True}},
        ProviderBuildContext(store, desktop_backend.capture_observation),
    )

    def close_runtime() -> None:
        if worker_pool is not None:
            worker_pool.shutdown(wait=True, cancel_futures=True)
        if callable(proposal_runtime.close):
            proposal_runtime.close()

    atexit.register(close_runtime)

    runtime = CoreOrchestrator(
        compositor,
        desktop_backend.executor,
        proposal_provider=proposal_runtime.provider,
        frame_provider=desktop_backend.frame_provider,
        evidence_providers=evidence_providers,
        denied_actions=denied_actions,
        store=store,
        ledger=recording.runtime_ledger,
        audit_store=recording.audit_store,
        audit_ledger=recording.audit_ledger,
        diagnostic_enabled=recording.diagnostic_enabled,
    )
    runtime_description = RuntimeDescription.from_components(
        compositor=compositor,
        executor=desktop_backend.executor,
        proposal_provider=proposal_runtime.provider,
        frame_provider=desktop_backend.frame_provider,
        evidence_providers=evidence_providers,
        denied_actions=denied_actions,
        context_strategies=ContextBuilder.STRATEGIES,
        effective_config=effective_config,
        transport=(effective_config or {}).get("transport"),
        recording={
            "audit": recording.audit_enabled,
            "diagnostic": recording.diagnostic_enabled,
        },
    )
    facade = AutoUIFacade(runtime, runtime_description)
    desktop_tools = desktop_backend.create_tools(
        CoreDesktopTransactionRunner(runtime, run_blocking),
        run_blocking,
    )

    @mcp.tool()
    async def gui_run(
        operation: str,
        task_id: str = '',
        task_contract: dict | None = None,
        proposal_id: str = '',
        strategy: str = 'compact',
        max_iterations: int | None = None,
        intent: str = '',
    ) -> dict:
        """运行紧凑任务生命周期；task_contract 可含由 MCP 依次推进的 steps。"""
        return await run_blocking(
            facade.handle,
            operation,
            task_id=task_id,
            task_contract=task_contract,
            proposal_id=proposal_id,
            strategy=strategy,
            max_iterations=max_iterations,
            intent=intent,
        )

    if recording.diagnostic_enabled:
        @mcp.tool()
        async def gui_diagnostic(
            operation: str,
            task_id: str = '',
            task_contract: dict | None = None,
            proposal: dict | None = None,
            proposal_id: str = '',
            strategy: str = 'compact',
            object_ref: str = '',
            max_iterations: int | None = None,
        ) -> dict:
            """诊断控制器阶段：observe、propose、prepare、execute、evaluate、trace。"""
            return await run_blocking(
                facade.handle_diagnostic,
                operation,
                task_id=task_id,
                task_contract=task_contract,
                proposal=proposal,
                proposal_id=proposal_id,
                strategy=strategy,
                object_ref=object_ref,
                max_iterations=max_iterations,
            )

    @mcp.tool()
    async def desktop_capabilities_list(category: str = '') -> list[dict]:
        """List shortcuts advertised by the selected desktop backend."""
        return desktop_tools.list_capabilities(category)

    @mcp.tool()
    async def desktop_shortcut_invoke(capability_id: str) -> dict:
        """Invoke one safe shortcut through the selected desktop backend."""
        return await desktop_tools.invoke_shortcut(capability_id)

    @mcp.tool()
    async def desktop_applications_list(query: str = '', limit: int = 30) -> list[dict]:
        """List applications advertised by the selected desktop backend."""
        return desktop_tools.list_applications(query, limit)

    @mcp.tool()
    async def desktop_application_launch(
        app_id: str,
        expected_active_app_id: str = '',
        application_wait_timeout_s: float = 3.0,
    ) -> dict:
        """Launch an application through the selected desktop backend."""
        return await desktop_tools.launch_application(
            app_id,
            expected_active_app_id,
            application_wait_timeout_s,
        )
