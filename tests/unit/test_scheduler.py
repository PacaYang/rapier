"""Unit tests for the task Scheduler."""

import pytest

from rapier.core.task import Task
from rapier.core.execution_graph import ExecutionGraph
from rapier.core.enums import TaskStatus, FailurePolicy
from rapier.engine.scheduler import Scheduler


@pytest.fixture
def serial_scheduler(dummy_method):
    """Scheduler over a serial graph a -> b -> c."""
    graph = ExecutionGraph()
    for tid in ("a", "b", "c"):
        graph.add_node(Task(task_id=tid, method=dummy_method, inputs={}))
    graph.add_edge("a", "b")
    graph.add_edge("b", "c")
    return Scheduler(graph)


class TestScheduler:
    def test_initial_ready_tasks(self, serial_scheduler):
        ready = serial_scheduler.get_ready_tasks()
        assert [t.task_id for t in ready] == ["a"]

    def test_completion_unblocks_downstream(self, serial_scheduler):
        a = serial_scheduler.graph.tasks["a"]
        serial_scheduler.mark_running(a)
        serial_scheduler.mark_completed(a, TaskStatus.COMPLETED)

        ready = serial_scheduler.get_ready_tasks()
        assert [t.task_id for t in ready] == ["b"]
        assert "a" in serial_scheduler.completed_tasks

    def test_running_task_not_ready(self, serial_scheduler):
        a = serial_scheduler.graph.tasks["a"]
        serial_scheduler.mark_running(a)
        # 'a' is now running, should not be returned as ready
        assert all(t.task_id != "a" for t in serial_scheduler.get_ready_tasks())

    def test_failed_task_tracked(self, serial_scheduler):
        a = serial_scheduler.graph.tasks["a"]
        serial_scheduler.mark_running(a)
        serial_scheduler.mark_completed(a, TaskStatus.FAILED)
        assert "a" in serial_scheduler.failed_tasks
        assert serial_scheduler.has_failures()

    def test_mark_downstream_skipped(self, serial_scheduler):
        a = serial_scheduler.graph.tasks["a"]
        skipped = serial_scheduler.mark_downstream_skipped(a)
        assert set(skipped) == {"b", "c"}
        assert serial_scheduler.graph.tasks["b"].status == TaskStatus.SKIPPED
        assert serial_scheduler.graph.tasks["c"].status == TaskStatus.SKIPPED

    def test_can_continue_policy(self, serial_scheduler):
        a = serial_scheduler.graph.tasks["a"]
        a.failure_policy = FailurePolicy.CONTINUE
        assert serial_scheduler.can_continue(a)
        a.failure_policy = FailurePolicy.STOP
        assert not serial_scheduler.can_continue(a)

    def test_progress(self, serial_scheduler):
        progress = serial_scheduler.get_progress()
        assert progress["total"] == 3
        assert progress["pending"] == 3
        assert progress["completed"] == 0

        a = serial_scheduler.graph.tasks["a"]
        serial_scheduler.mark_running(a)
        serial_scheduler.mark_completed(a, TaskStatus.COMPLETED)
        progress = serial_scheduler.get_progress()
        assert progress["completed"] == 1
        assert progress["progress_pct"] > 0

    def test_is_finished(self, serial_scheduler):
        assert not serial_scheduler.is_finished()
        for tid in ("a", "b", "c"):
            task = serial_scheduler.graph.tasks[tid]
            serial_scheduler.mark_running(task)
            serial_scheduler.mark_completed(task, TaskStatus.COMPLETED)
        assert serial_scheduler.is_finished()

    def test_topological_order(self, serial_scheduler):
        order = serial_scheduler.get_topological_order()
        assert order == ["a", "b", "c"]

    def test_reset(self, serial_scheduler):
        a = serial_scheduler.graph.tasks["a"]
        serial_scheduler.mark_running(a)
        serial_scheduler.mark_completed(a, TaskStatus.COMPLETED)
        serial_scheduler.reset()
        assert len(serial_scheduler.completed_tasks) == 0
        assert len(serial_scheduler.running_tasks) == 0
