"""End-to-end tests for failure handling policies (STOP, CONTINUE, RETRY)."""

import pytest

from rapier.core.task import Task
from rapier.core.pipeline import Pipeline
from rapier.core.enums import TaskStatus, FailurePolicy
from rapier.engine.scheduler import Scheduler
from rapier.engine.failure_handler import FailureHandler, FailureAction


def build_chain(methods_and_policies):
    """Build a serial pipeline a -> b -> c from (method, policy) tuples."""
    pipeline = Pipeline(name="failure_test")
    ids = []
    for i, (method, policy) in enumerate(methods_and_policies):
        tid = chr(ord("a") + i)
        ids.append(tid)
        task = Task(task_id=tid, method=method, inputs={})
        task.failure_policy = policy
        pipeline.add_task(task)
    for frm, to in zip(ids, ids[1:]):
        pipeline.add_connection(frm, to)
    return pipeline


def run_with_failure_handling(pipeline):
    """Run pipeline applying failure decisions; returns (scheduler, stopped)."""
    graph = pipeline.to_execution_graph()
    scheduler = Scheduler(graph)
    handler = FailureHandler()
    stopped = False

    while not scheduler.is_finished():
        ready = scheduler.get_ready_tasks()
        if not ready:
            break
        for task in ready:
            scheduler.mark_running(task)
            status = task.method.execute(task)

            if status == TaskStatus.FAILED:
                decision = handler.handle_failure(task, Exception("fail"))
                if decision.should_retry:
                    task.retry_count += 1
                    task.status = TaskStatus.PENDING
                    scheduler.running_tasks.discard(task.task_id)
                    continue
                scheduler.mark_completed(task, status)
                if decision.should_stop_pipeline:
                    stopped = True
                    return scheduler, stopped
                if decision.mark_downstream_skipped:
                    scheduler.mark_downstream_skipped(task)
            else:
                scheduler.mark_completed(task, status)

    return scheduler, stopped


class TestStopPolicy:
    def test_stop_halts_pipeline(self, dummy_method, failing_method):
        pipeline = build_chain([
            (dummy_method, FailurePolicy.STOP),
            (failing_method, FailurePolicy.STOP),
            (dummy_method, FailurePolicy.STOP),
        ])
        scheduler, stopped = run_with_failure_handling(pipeline)
        assert stopped
        # 'a' completed, 'b' failed, 'c' never ran
        assert "a" in scheduler.completed_tasks
        assert "b" in scheduler.failed_tasks
        assert "c" not in scheduler.completed_tasks


class TestContinuePolicy:
    def test_continue_skips_downstream(self, dummy_method):
        from tests.conftest import FailingMethod

        pipeline = build_chain([
            (dummy_method, FailurePolicy.CONTINUE),
            (FailingMethod(), FailurePolicy.CONTINUE),
            (DummyDownstream(), FailurePolicy.CONTINUE),
        ])
        scheduler, stopped = run_with_failure_handling(pipeline)
        assert not stopped
        assert "b" in scheduler.failed_tasks
        # downstream 'c' should be skipped
        assert "c" in scheduler.skipped_tasks


class TestRetryPolicy:
    def test_retry_then_succeed(self):
        method = FlakyMethod(fail_times=2)
        pipeline = Pipeline(name="retry_test")
        task = Task(task_id="a", method=method, inputs={})
        task.failure_policy = FailurePolicy.RETRY
        task.max_retries = 3
        pipeline.add_task(task)

        scheduler, stopped = run_with_failure_handling(pipeline)
        # After 2 failures it should succeed on the 3rd attempt
        assert "a" in scheduler.completed_tasks
        assert method.attempts == 3

    def test_retry_exhausted_stops(self):
        method = FlakyMethod(fail_times=10)
        pipeline = Pipeline(name="retry_exhausted")
        task = Task(task_id="a", method=method, inputs={})
        task.failure_policy = FailurePolicy.RETRY
        task.max_retries = 2
        pipeline.add_task(task)

        scheduler, stopped = run_with_failure_handling(pipeline)
        # retries exhausted -> stop
        assert stopped


class TestFailureHandlerDecisions:
    def test_stop_decision(self, failing_method):
        task = Task(task_id="x", method=failing_method, inputs={})
        task.failure_policy = FailurePolicy.STOP
        decision = FailureHandler().handle_failure(task, Exception("e"))
        assert decision.should_stop_pipeline

    def test_continue_decision(self, failing_method):
        task = Task(task_id="x", method=failing_method, inputs={})
        task.failure_policy = FailurePolicy.CONTINUE
        decision = FailureHandler().handle_failure(task, Exception("e"))
        assert decision.mark_downstream_skipped

    def test_retry_decision(self, failing_method):
        task = Task(task_id="x", method=failing_method, inputs={})
        task.failure_policy = FailurePolicy.RETRY
        task.max_retries = 1
        decision = FailureHandler().handle_failure(task, Exception("e"))
        assert decision.should_retry


# ---------------------------------------------------------------------------
# Helper methods used in these tests
# ---------------------------------------------------------------------------

from rapier.core.method import Method


class DummyDownstream(Method):
    """A method that should never run (downstream of a failure)."""

    def __init__(self):
        super().__init__(name="downstream")
        self.ran = False

    def execute(self, task):
        self.ran = True
        return TaskStatus.COMPLETED


class FlakyMethod(Method):
    """Fails the first ``fail_times`` attempts, then succeeds."""

    def __init__(self, fail_times=1):
        super().__init__(name="flaky")
        self.fail_times = fail_times
        self.attempts = 0

    def execute(self, task):
        self.attempts += 1
        if self.attempts <= self.fail_times:
            task.error_message = f"attempt {self.attempts} failed"
            return TaskStatus.FAILED
        return TaskStatus.COMPLETED
