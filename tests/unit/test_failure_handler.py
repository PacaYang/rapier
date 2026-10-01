"""Unit tests for the FailureHandler."""

import pytest

from rapier.core.task import Task
from rapier.core.pipeline import Pipeline
from rapier.core.enums import FailurePolicy
from rapier.engine.failure_handler import FailureHandler, FailureAction


def make_task(policy=FailurePolicy.STOP, max_retries=0, retry_count=0):
    task = Task(task_id="t", method=None, inputs={})
    task.failure_policy = policy
    task.max_retries = max_retries
    task.retry_count = retry_count
    return task


@pytest.fixture
def handler():
    return FailureHandler()


class TestHandleFailure:
    def test_stop_policy(self, handler):
        task = make_task(FailurePolicy.STOP)
        decision = handler.handle_failure(task, Exception("e"))
        assert decision.action == FailureAction.STOP
        assert decision.should_stop_pipeline

    def test_continue_policy(self, handler):
        task = make_task(FailurePolicy.CONTINUE)
        decision = handler.handle_failure(task, Exception("e"))
        assert decision.action == FailureAction.CONTINUE
        assert decision.mark_downstream_skipped

    def test_retry_policy_with_attempts_left(self, handler):
        task = make_task(FailurePolicy.RETRY, max_retries=3, retry_count=0)
        decision = handler.handle_failure(task, Exception("e"))
        assert decision.action == FailureAction.RETRY
        assert decision.should_retry

    def test_retry_policy_exhausted(self, handler):
        task = make_task(FailurePolicy.RETRY, max_retries=2, retry_count=2)
        decision = handler.handle_failure(task, Exception("e"))
        assert decision.should_stop_pipeline


class TestShouldRetry:
    def test_retry_under_limit(self, handler):
        task = make_task(FailurePolicy.RETRY, max_retries=3, retry_count=1)
        assert handler.should_retry(task)

    def test_retry_at_limit(self, handler):
        task = make_task(FailurePolicy.RETRY, max_retries=2, retry_count=2)
        assert not handler.should_retry(task)

    def test_non_retry_policy(self, handler):
        task = make_task(FailurePolicy.STOP)
        assert not handler.should_retry(task)


class TestShouldContinueStop:
    def test_should_continue(self, handler):
        assert handler.should_continue(make_task(FailurePolicy.CONTINUE))
        assert not handler.should_continue(make_task(FailurePolicy.STOP))

    def test_should_stop_for_stop_policy(self, handler):
        assert handler.should_stop_pipeline(make_task(FailurePolicy.STOP))

    def test_should_stop_for_retry_exhausted(self, handler):
        task = make_task(FailurePolicy.RETRY, max_retries=1, retry_count=1)
        assert handler.should_stop_pipeline(task)

    def test_should_not_stop_for_continue(self, handler):
        assert not handler.should_stop_pipeline(make_task(FailurePolicy.CONTINUE))


class TestDownstreamMarking:
    def test_mark_downstream_on_continue(self, handler, dummy_method):
        pipeline = Pipeline(name="p")
        for tid in ("a", "b", "c"):
            pipeline.add_task(Task(task_id=tid, method=dummy_method, inputs={}))
        pipeline.add_connection("a", "b")
        pipeline.add_connection("b", "c")

        task = pipeline.tasks["a"]
        downstream = handler.mark_downstream_tasks(task, pipeline, FailureAction.CONTINUE)
        assert set(downstream) == {"b", "c"}

    def test_no_downstream_for_non_continue(self, handler, dummy_method):
        pipeline = Pipeline(name="p")
        pipeline.add_task(Task(task_id="a", method=dummy_method, inputs={}))
        pipeline.add_task(Task(task_id="b", method=dummy_method, inputs={}))
        pipeline.add_connection("a", "b")

        task = pipeline.tasks["a"]
        downstream = handler.mark_downstream_tasks(task, pipeline, FailureAction.STOP)
        assert downstream == []


class TestRecordFailure:
    def test_records_error_message(self, handler):
        task = make_task()
        handler.record_failure(task, ValueError("bad input"))
        assert task.error_message == "bad input"

    def test_appends_to_failures_metadata(self, handler):
        task = make_task()
        handler.record_failure(task, ValueError("first"))
        handler.record_failure(task, RuntimeError("second"))
        failures = task.metadata["failures"]
        assert len(failures) == 2
        assert failures[0]["error_type"] == "ValueError"
        assert failures[1]["error_type"] == "RuntimeError"
