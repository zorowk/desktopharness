import ast
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]
CORE = ROOT / "src" / "mcp_autogui" / "core"
FACADE = ROOT / "src" / "mcp_autogui" / "facade.py"
DESKTOP_BACKEND = ROOT / "src" / "mcp_autogui" / "desktop_backend.py"
DESKTOP_TOOLS = (
    ROOT
    / "src"
    / "mcp_autogui"
    / "adapters"
    / "backends"
    / "treeland_deepin_tools.py"
)


class CoreBoundaryTests(unittest.TestCase):
    def test_core_implementation_modules_do_not_import_the_compatibility_aggregate(self):
        for path in CORE.glob("*.py"):
            if path.name in {"__init__.py", "models.py"}:
                continue
            self.assertNotIn("from .models import", path.read_text(encoding="utf-8"), path.name)

    def test_default_server_entrypoint_does_not_import_optional_langchain_clients(self):
        entrypoint = (ROOT / "src" / "mcp_autogui" / "__init__.py").read_text(encoding="utf-8")
        self.assertNotIn("langchain", entrypoint)

    def test_core_does_not_import_adapters(self):
        for path in CORE.glob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            modules = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom):
                    modules.add(node.module or "")
                elif isinstance(node, ast.Import):
                    modules.update(alias.name for alias in node.names)
            self.assertFalse(
                any(
                    module == "adapters"
                    or module.startswith("adapters.")
                    or module == "mcp_autogui.adapters"
                    or module.startswith("mcp_autogui.adapters.")
                    for module in modules
                ),
                path.name,
            )

    def test_desktop_tools_do_not_depend_on_the_full_core_runtime(self):
        tools = DESKTOP_TOOLS.read_text(encoding="utf-8")
        backend = DESKTOP_BACKEND.read_text(encoding="utf-8")

        self.assertNotIn("CoreOrchestrator", tools)
        self.assertNotIn("core.orchestrator", tools)
        self.assertNotIn("Callable[[Any, RunBlocking]", backend)
        self.assertIn(
            "Callable[[DesktopTransactionRunner, RunBlocking]", backend
        )

    def test_public_and_diagnostic_dispatch_remain_separate(self):
        tree = ast.parse(FACADE.read_text(encoding="utf-8"), filename=str(FACADE))
        facade = next(
            node
            for node in tree.body
            if isinstance(node, ast.ClassDef) and node.name == "AutoUIFacade"
        )
        methods = {
            node.name: node
            for node in facade.body
            if isinstance(node, ast.FunctionDef)
        }

        self.assertIn("_handle_public", methods)
        self.assertIn("_handle_diagnostic", methods)
        self.assertNotIn("_handle", methods)
        for name in ("_handle_public", "_handle_diagnostic"):
            arguments = methods[name].args
            parameter_names = {
                argument.arg
                for argument in (*arguments.args, *arguments.kwonlyargs)
            }
            self.assertNotIn("diagnostic", parameter_names)

    def test_facade_describe_does_not_inspect_runtime_components(self):
        facade = FACADE.read_text(encoding="utf-8")

        for component in (
            "compositor",
            "executor",
            "proposal_provider",
            "frame_provider",
            "evidence_providers",
            "context_builder",
            "validator",
        ):
            self.assertNotIn(f"self.runtime.{component}", facade)

    def test_runtime_state_has_one_write_path_and_no_transaction_recorder(self):
        orchestrator = (CORE / "orchestrator.py").read_text(encoding="utf-8")

        self.assertFalse((CORE / "transaction_recorder.py").exists())
        self.assertNotIn("TransactionRecorder", orchestrator)
        self.assertNotIn("self._tasks.update_state", orchestrator)
        self.assertEqual(orchestrator.count("self._tasks.set_state(state)"), 1)
        self.assertNotIn("self.store.require(proposal_id)", orchestrator)
