import unittest
from dataclasses import replace

from mcp_autogui.core.models import (
    Action, ActionProposal, ActionType, AdapterCapabilities, AdapterDescriptor,
    AssertionSpec, CanonicalSnapshot, CanonicalWindowFact, CoordinateSpace, ExecutionReceipt,
    ExecutionStatus, OutputFact, Point, ReasonCode, Rect, StackingCapabilities,
    StackingModel, TaskContract, TaskLimits, TaskState, TaskStatus, WindowRole,
    new_id, utc_now,
)
from mcp_autogui.core.orchestrator import CoreOrchestrator
from mcp_autogui.core.evidence import AssertionResult, AssertionStatus
from mcp_autogui.core.proposal_validator import PreparedProposal, ProposalValidator, ValidationFailure
from mcp_autogui.core.task_state import TaskStateReducer


def descriptor():
    return AdapterDescriptor(
        "fixture",
        AdapterCapabilities(
            True, True, True,
            StackingCapabilities(StackingModel.HIT_TEST, hit_test=True),
            active_window=True, window_identity="stable",
        ),
    )


def snapshot(snapshot_id="snapshot-1", *, target="desktop"):
    bounds = Rect(0, 0, 1000, 800)
    return CanonicalSnapshot(
        snapshot_id, utc_now(), "env", CoordinateSpace("desktop-logical", bounds, "geometry-1"),
        (OutputFact("display", bounds),), Point(20, 20),
        (CanonicalWindowFact(
            target, bounds, app_id=target, title=target, visible=True, active=True,
            role=WindowRole.DESKTOP,
        ),),
    )


def contract(*, retries=1, max_steps=5):
    return TaskContract(
        "task-1", "test", limits=TaskLimits(max_steps=max_steps, max_retries=retries)
    )


def click(snapshot_id="snapshot-1"):
    return ActionProposal(
        new_id("proposal"), "fixture", snapshot_id,
        (Action(ActionType.POINTER_CLICK, Point(100, 100), "desktop-logical"),),
        claimed_intent="unknown-is-diagnostic-only",
    )


class FakeCompositor:
    descriptor = descriptor()

    def __init__(self, snapshots):
        self.snapshots = list(snapshots)
        self.latest = self.snapshots[0]

    def observe(self):
        if self.snapshots:
            self.latest = self.snapshots.pop(0)
        return self.latest

    def hit_test(self, point, current=None):
        current = current or self.latest
        return next((w.window_id for w in current.windows if w.geometry.contains(point)), None)


class FakeExecutor:
    def __init__(self):
        self.actions = []

    def execute(self, proposal):
        action = proposal.actions[0]
        self.actions.append(action)
        now = utc_now()
        return ExecutionReceipt(
            new_id("execution"), proposal.proposal_id, ExecutionStatus.DELIVERED,
            action, now, now,
        )


class ProposalValidatorTests(unittest.TestCase):
    def validator(self, **kwargs):
        return ProposalValidator(
            descriptor(), lambda _point, current: current.windows[0].window_id, **kwargs
        )

    def test_unknown_intent_does_not_require_confirmation(self):
        self.assertIsInstance(self.validator().prepare(snapshot(), click()), PreparedProposal)

    def test_shell_keyboard_input_is_allowed_without_an_active_window(self):
        desktop = replace(snapshot(), windows=())
        for action in (
            Action(ActionType.KEYBOARD_KEY, parameters={"key": "win"}),
            Action(ActionType.KEYBOARD_TEXT, parameters={"text": "Text Editor"}),
            Action(ActionType.KEYBOARD_SHORTCUT, parameters={"keys": ["ctrl", "l"]}),
        ):
            proposal = ActionProposal("shell-input", "fixture", desktop.snapshot_id, (action,))
            self.assertIsInstance(self.validator().prepare(desktop, proposal), PreparedProposal)

    def test_done_must_be_last(self):
        proposal = ActionProposal(
            "p", "fixture", "snapshot-1",
            (Action(ActionType.DONE), Action(ActionType.KEYBOARD_KEY, parameters={"key": "enter"})),
        )
        result = self.validator().prepare(snapshot(), proposal)
        self.assertEqual(result.reason_code, ReasonCode.MODEL_PROTOCOL_INVALID)
        self.assertFalse(result.retryable)

    def test_invalid_parameters_and_static_restrictions_are_nonretryable(self):
        invalid = ActionProposal(
            "p1", "fixture", "snapshot-1",
            (Action(ActionType.KEYBOARD_SHORTCUT, parameters={"keys": []}),),
        )
        result = self.validator().prepare(snapshot(), invalid)
        self.assertEqual(result.reason_code, ReasonCode.INVALID_ACTION_PARAMETERS)
        restricted = self.validator(
            denied_actions=frozenset({ActionType.POINTER_CLICK})
        ).prepare(snapshot(), click())
        self.assertEqual(restricted.reason_code, ReasonCode.ACTION_RESTRICTED)

        invalid_type = ActionProposal(
            "p2", "fixture", "snapshot-1", (Action("pointer.teleport"),)
        )
        result = self.validator().prepare(snapshot(), invalid_type)
        self.assertEqual(result.reason_code, ReasonCode.MODEL_PROTOCOL_INVALID)

        invalid_shape = ActionProposal(
            "p3", "fixture", "snapshot-1",
            (Action(ActionType.KEYBOARD_KEY, Point(10, 10), "desktop-logical", {"key": "a"}),),
        )
        result = self.validator().prepare(snapshot(), invalid_shape)
        self.assertEqual(result.reason_code, ReasonCode.INVALID_ACTION_PARAMETERS)

    def test_recheck_ignores_unrelated_environment_change(self):
        prepared = self.validator().prepare(snapshot(), click())
        latest = replace(snapshot("snapshot-2"), environment_version="animation")
        self.assertIs(self.validator().recheck(prepared, latest), prepared)

    def test_recheck_detects_target_change_as_retryable(self):
        validator = ProposalValidator(
            descriptor(), lambda _point, current: current.windows[0].window_id
        )
        prepared = validator.prepare(snapshot(), click())
        result = validator.recheck(prepared, snapshot("snapshot-2", target="overlay"))
        self.assertEqual(result.reason_code, ReasonCode.TARGET_DISAPPEARED)
        self.assertTrue(result.retryable)

    def test_recheck_tracks_cursor_needed_after_non_pointer_action(self):
        proposal = ActionProposal(
            "p", "fixture", "snapshot-1",
            (
                Action(ActionType.KEYBOARD_KEY, parameters={"key": "tab"}),
                Action(
                    ActionType.POINTER_MOVE, Point(30, 20), "desktop-logical",
                    {"relative": True},
                ),
            ),
        )
        validator = self.validator()
        prepared = validator.prepare(snapshot(), proposal)
        latest = replace(snapshot("snapshot-2"), cursor=Point(25, 20))
        result = validator.recheck(prepared, latest)
        self.assertEqual(result.reason_code, ReasonCode.CURSOR_ORIGIN_CHANGED)
        self.assertTrue(result.retryable)


class TaskStateReducerTests(unittest.TestCase):
    def test_final_assertions_cannot_complete_an_intermediate_plan_step(self):
        reducer = TaskStateReducer()
        task = TaskContract(
            "task-1", "complete both steps",
            assertions=(AssertionSpec("final", "active_window.title", "contains", "saved"),),
            steps=("open", "save"),
        )
        passed = AssertionResult(
            "final", task.assertions[0], AssertionStatus.PASSED, (),
            utc_now(), "transiently true",
        )

        intermediate = reducer.reduce(task, TaskState("task-1", plan_step=0), (passed,))
        final = reducer.reduce(task, TaskState("task-1", plan_step=1), (passed,))

        self.assertEqual(intermediate.status, TaskStatus.RUNNING)
        self.assertEqual(intermediate.completed_assertions, ())
        self.assertEqual(final.status, TaskStatus.COMPLETED)

    def test_retryable_validation_consumes_only_retry_budget(self):
        reducer = TaskStateReducer()
        task = contract(retries=1)
        first = reducer.validation_failure(task, TaskState("task-1"), retryable=True)
        second = reducer.validation_failure(task, first, retryable=True)
        self.assertEqual((first.status, first.retries, first.step), (TaskStatus.RETRYING, 1, 0))
        self.assertEqual((second.status, second.retries), (TaskStatus.FAILED, 1))

    def test_nonretryable_validation_and_execution_fail_without_retry(self):
        reducer = TaskStateReducer()
        state = reducer.validation_failure(contract(), TaskState("task-1"), retryable=False)
        self.assertEqual((state.status, state.retries), (TaskStatus.FAILED, 0))
        self.assertEqual(reducer.execution_failure(TaskState("task-1")).status, TaskStatus.FAILED)

    def test_recoverable_postcondition_uses_step_budget_not_retry_budget(self):
        reducer = TaskStateReducer()
        task = TaskContract(
            "task-1", "test", limits=TaskLimits(max_steps=3, max_retries=1),
            assertions=(AssertionSpec("pass", "active_window.title", "contains", "PASS"),),
        )
        failed = AssertionResult(
            "pass", task.assertions[0], AssertionStatus.FAILED, (), utc_now(), "not yet"
        )

        first = reducer.reduce(task, TaskState("task-1", step=1), (failed,))
        second = reducer.reduce(task, TaskState("task-1", step=2), (failed,))
        exhausted = reducer.reduce(task, TaskState("task-1", step=3), (failed,))

        self.assertEqual((first.status, first.retries), (TaskStatus.RETRYING, 0))
        self.assertEqual((second.status, second.retries), (TaskStatus.RETRYING, 0))
        self.assertEqual(exhausted.status, TaskStatus.FAILED)


class OrchestratorTests(unittest.TestCase):
    def runtime(self, snapshots, *, executor=None, denied=frozenset(), provider=None):
        return CoreOrchestrator(
            FakeCompositor(snapshots), executor or FakeExecutor(),
            proposal_provider=provider, denied_actions=denied,
        )

    @staticmethod
    def submit(runtime, proposal):
        runtime.register_task(contract())
        runtime.observe("task-1")
        runtime.submit_proposal("task-1", proposal)

    def test_sequence_executes_in_order_and_consumes_one_step(self):
        actions = (
            Action(ActionType.POINTER_MOVE, Point(100, 100), "desktop-logical"),
            Action(ActionType.POINTER_SCROLL, parameters={"clicks": -3}),
        )
        executor = FakeExecutor()
        runtime = self.runtime([snapshot(), snapshot("snapshot-2")], executor=executor)
        runtime.register_task(contract())
        runtime.observe("task-1")
        runtime.submit_proposal("task-1", ActionProposal("p", "fixture", "snapshot-1", actions))
        receipt = runtime.execute("p")
        self.assertEqual(receipt.status, ExecutionStatus.DELIVERED)
        self.assertEqual(executor.actions, list(actions))
        self.assertEqual(runtime.status("task-1").step, 1)

    def test_static_restriction_and_stale_target_inject_nothing(self):
        executor = FakeExecutor()
        proposal = click()
        restricted = self.runtime(
            [snapshot()], executor=executor,
            denied=frozenset({ActionType.POINTER_CLICK}),
        )
        self.submit(restricted, proposal)
        result = restricted.execute(proposal.proposal_id)
        self.assertEqual(result.reason_code, ReasonCode.ACTION_RESTRICTED)
        self.assertEqual(executor.actions, [])

        executor = FakeExecutor()
        proposal = click()
        stale = self.runtime(
            [snapshot(), snapshot("snapshot-2", target="overlay")], executor=executor
        )
        self.submit(stale, proposal)
        result = stale.execute(proposal.proposal_id)
        self.assertIsInstance(result, ValidationFailure)
        self.assertTrue(result.retryable)
        self.assertEqual(executor.actions, [])
        self.assertEqual(stale.status("task-1").status, TaskStatus.RETRYING)

    def test_duplicate_execute_does_not_reinject(self):
        executor = FakeExecutor()
        runtime = self.runtime([snapshot(), snapshot("snapshot-2")], executor=executor)
        proposal = click()
        self.submit(runtime, proposal)
        first = runtime.execute(proposal.proposal_id)
        second = runtime.execute(proposal.proposal_id)
        self.assertIs(first, second)
        self.assertEqual(len(executor.actions), 1)

    def test_step_budget_blocks_another_sequence_without_injection(self):
        executor = FakeExecutor()
        runtime = self.runtime(
            [snapshot(), snapshot("snapshot-2")], executor=executor
        )
        runtime.register_task(contract(max_steps=1))
        runtime.observe("task-1")
        first = click()
        runtime.submit_proposal("task-1", first)
        self.assertEqual(runtime.execute(first.proposal_id).status, ExecutionStatus.DELIVERED)

        second = click("snapshot-2")
        runtime.submit_proposal("task-1", second)
        result = runtime.execute(second.proposal_id)
        self.assertEqual(result.reason_code, ReasonCode.MODEL_PLANNING_INVALID)
        self.assertEqual(len(executor.actions), 1)
        self.assertEqual(runtime.status("task-1").step, 1)

    def test_partial_sequence_fails_without_replay(self):
        class FailsSecond(FakeExecutor):
            def execute(self, proposal):
                if len(self.actions) == 1:
                    self.actions.append(proposal.actions[0])
                    now = utc_now()
                    return ExecutionReceipt(
                        new_id("execution"), proposal.proposal_id,
                        ExecutionStatus.FAILED, None, now, now,
                        ReasonCode.EXECUTOR_ACTION_FAILED,
                    )
                return super().execute(proposal)

        actions = (
            Action(ActionType.POINTER_MOVE, Point(100, 100), "desktop-logical"),
            Action(ActionType.POINTER_SCROLL, parameters={"clicks": -3}),
            Action(ActionType.POINTER_MOVE, Point(200, 200), "desktop-logical"),
        )
        executor = FailsSecond()
        runtime = self.runtime([snapshot(), snapshot("snapshot-2")], executor=executor)
        runtime.register_task(contract())
        runtime.observe("task-1")
        runtime.submit_proposal("task-1", ActionProposal("p", "fixture", "snapshot-1", actions))
        receipt = runtime.execute("p")
        self.assertEqual(receipt.status, ExecutionStatus.FAILED)
        self.assertEqual(executor.actions, list(actions[:2]))
        self.assertEqual(runtime.status("task-1").status, TaskStatus.FAILED)

    def test_empty_assertions_require_delivery_then_done(self):
        class Provider:
            provider_id = "fixture"
            def __init__(self):
                self.index = 0
                self.feedback = []
            def propose(self, context):
                self.index += 1
                action = (
                    Action(ActionType.POINTER_CLICK, Point(100, 100), "desktop-logical")
                    if self.index == 1 else Action(ActionType.DONE)
                )
                return ActionProposal(f"p{self.index}", "fixture", context.based_on_snapshot, (action,))
            def record_execution(self, task_id, receipt):
                self.feedback.append((task_id, receipt.status.value))
            def record_outcome(self, task_id, **outcome):
                self.feedback.append((task_id, outcome["status"]))

        provider = Provider()
        runtime = self.runtime(
            [snapshot(), snapshot("s2"), snapshot("s3"), snapshot("s4")], provider=provider
        )
        runtime.register_task(contract())
        first = runtime.run_step("task-1")
        second = runtime.run_step("task-1")
        self.assertEqual(first["state"].status, TaskStatus.RUNNING)
        self.assertEqual(second["state"].status, TaskStatus.DELIVERED_UNVERIFIED)
        self.assertEqual(provider.feedback[-1], ("task-1", "partial"))

    def test_step_plan_advances_inside_one_run_without_ending_the_task(self):
        class Provider:
            provider_id = "fixture"
            def __init__(self):
                self.index = 0
                self.goals = []
                self.resets = []
            def propose(self, context):
                self.index += 1
                self.goals.append(context.goal)
                return ActionProposal(
                    f"plan-{self.index}", "fixture", context.based_on_snapshot,
                    (
                        Action(ActionType.POINTER_CLICK, Point(100, 100), "desktop-logical"),
                        Action(ActionType.DONE),
                    ),
                )
            def record_execution(self, *args, **kwargs):
                return None
            def reset(self, task_id):
                self.resets.append(task_id)

        provider = Provider()
        runtime = self.runtime(
            [snapshot(), snapshot("s2"), snapshot("s3"), snapshot("s4")],
            provider=provider,
        )
        runtime.register_task(TaskContract(
            "task-1", "Open and edit a document.",
            steps=("Open the editor.", "Enter the text."),
            limits=TaskLimits(max_steps=4, max_retries=1),
        ))

        outcome = runtime.run("task-1")

        self.assertEqual(outcome["state"].status, TaskStatus.DELIVERED_UNVERIFIED)
        self.assertEqual(outcome["state"].plan_step, 1)
        self.assertEqual(provider.goals, ["Open the editor.", "Enter the text."])
        self.assertEqual(provider.resets, ["task-1"])
        self.assertTrue(any(
            event.event_type == "task.plan_step_advanced"
            for event in runtime.ledger.events("task-1")
        ))

    def test_run_reprompts_after_a_model_protocol_error_without_injecting(self):
        class Provider:
            provider_id = "fixture"

            def __init__(self):
                self.calls = 0

            def propose(self, context):
                self.calls += 1
                if self.calls == 1:
                    error = ValueError("exactly one action is required")
                    error.debug_ref = "model-output-invalid"
                    raise error
                return ActionProposal(
                    "valid", "fixture", context.based_on_snapshot,
                    (Action(ActionType.POINTER_CLICK, Point(100, 100), "desktop-logical"),),
                )

        provider = Provider()
        runtime = self.runtime(
            [snapshot(), snapshot("s2"), snapshot("s3"), snapshot("s4")],
            provider=provider,
        )
        runtime.register_task(TaskContract(
            "task-1", "Click once.", limits=TaskLimits(max_steps=1, max_retries=1),
        ))

        outcome = runtime.run("task-1")

        self.assertEqual(provider.calls, 2)
        self.assertEqual(len(runtime.executor.actions), 1)
        self.assertEqual(outcome["state"].step, 1)

    def test_protocol_violation_is_attributed_as_model_protocol_invalid(self):
        class Provider:
            provider_id = "fixture"

            def __init__(self):
                self.calls = 0

            def propose(self, context):
                self.calls += 1
                if self.calls == 1:
                    error = ValueError("exactly one action is required")
                    error.debug_ref = "model-output-invalid"
                    error.reason_code = ReasonCode.MODEL_PROTOCOL_INVALID
                    raise error
                return ActionProposal(
                    "valid", "fixture", context.based_on_snapshot,
                    (Action(ActionType.POINTER_CLICK, Point(100, 100), "desktop-logical"),),
                )

        provider = Provider()
        runtime = self.runtime(
            [snapshot(), snapshot("s2"), snapshot("s3"), snapshot("s4")],
            provider=provider,
        )
        runtime.register_task(TaskContract(
            "task-1", "Click once.", limits=TaskLimits(max_steps=1, max_retries=1),
        ))

        runtime.run("task-1")

        codes = [
            attribution.code
            for attribution in runtime.attributions("task-1")
        ]
        self.assertIn(ReasonCode.MODEL_PROTOCOL_INVALID, codes)

    def test_done_before_any_delivery_fails(self):
        class DoneProvider:
            provider_id = "done"
            def propose(self, context):
                return ActionProposal("done", "done", context.based_on_snapshot, (Action(ActionType.DONE),))
            def record_outcome(self, *args, **kwargs):
                return None

        runtime = self.runtime([snapshot(), snapshot("s2")], provider=DoneProvider())
        runtime.register_task(contract())
        self.assertEqual(runtime.run_step("task-1")["state"].status, TaskStatus.FAILED)

    def test_done_with_assertions_evaluates_without_prior_delivery(self):
        class DoneProvider:
            provider_id = "done"
            def propose(self, context):
                return ActionProposal("done", "done", context.based_on_snapshot, (Action(ActionType.DONE),))
            def record_outcome(self, *args, **kwargs):
                return None

        runtime = self.runtime([snapshot()], provider=DoneProvider())
        runtime.register_task(TaskContract(
            "task-1", "test",
            assertions=(AssertionSpec("ready", "active_window.app_id", "equals", "desktop"),),
        ))
        evaluated = []
        runtime.evaluate = lambda task_id: (
            evaluated.append(task_id) or (), (), TaskState(task_id, TaskStatus.COMPLETED)
        )
        state = runtime.run_step("task-1")["state"]
        self.assertEqual(state.status, TaskStatus.COMPLETED)
        self.assertEqual(evaluated, ["task-1"])

    def test_run_reprompts_after_done_when_observed_assertions_are_not_complete(self):
        class DoneProvider:
            provider_id = "done"
            def __init__(self):
                self.index = 0
            def propose(self, context):
                self.index += 1
                return ActionProposal(
                    f"done-{self.index}", "done", context.based_on_snapshot,
                    (Action(ActionType.DONE),),
                )
            def record_outcome(self, *args, **kwargs):
                return None

        provider = DoneProvider()
        runtime = self.runtime([snapshot(), snapshot("s2")], provider=provider)
        runtime.register_task(TaskContract(
            "task-1", "verify the final state",
            assertions=(AssertionSpec("ready", "active_window.app_id", "equals", "desktop"),),
        ))
        evaluated = []
        def evaluate(task_id):
            evaluated.append(task_id)
            status = TaskStatus.RUNNING if len(evaluated) == 1 else TaskStatus.COMPLETED
            return (), (), TaskState(task_id, status)
        runtime.evaluate = evaluate

        outcome = runtime.run("task-1", max_iterations=2)

        self.assertEqual(outcome["state"].status, TaskStatus.COMPLETED)
        self.assertEqual(provider.index, 2)

    def test_done_after_unknown_delivery_does_not_claim_delivered_unverified(self):
        class UnknownExecutor(FakeExecutor):
            def execute(self, proposal):
                self.actions.append(proposal.actions[0])
                now = utc_now()
                return ExecutionReceipt(
                    new_id("execution"), proposal.proposal_id,
                    ExecutionStatus.UNKNOWN, None, now, now,
                )

        class Provider:
            provider_id = "fixture"
            def __init__(self):
                self.index = 0
            def propose(self, context):
                self.index += 1
                action = (
                    Action(ActionType.POINTER_CLICK, Point(100, 100), "desktop-logical")
                    if self.index == 1 else Action(ActionType.DONE)
                )
                return ActionProposal(f"p{self.index}", "fixture", context.based_on_snapshot, (action,))
            def record_execution(self, *args, **kwargs):
                return None
            def record_outcome(self, *args, **kwargs):
                return None

        runtime = self.runtime(
            [snapshot(), snapshot("s2"), snapshot("s3")],
            executor=UnknownExecutor(), provider=Provider(),
        )
        runtime.register_task(contract())
        runtime.run_step("task-1")
        state = runtime.run_step("task-1")["state"]
        self.assertEqual(state.status, TaskStatus.FAILED)


if __name__ == "__main__":
    unittest.main()
