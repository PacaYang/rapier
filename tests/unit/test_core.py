"""Unit tests for core classes: Task, Pipeline, ExecutionGraph."""

import pytest

from rapier.core.task import Task
from rapier.core.pipeline import Pipeline
from rapier.core.execution_graph import ExecutionGraph
from rapier.core.enums import TaskStatus, FailurePolicy
from rapier.core.exceptions import ConfigurationError, DependencyError


# ---------------------------------------------------------------------------
# Task tests
# ---------------------------------------------------------------------------

class TestTask:
    def test_task_creation(self, dummy_method):
        task = Task(task_id="t1", method=dummy_method, inputs={"a": 1})
        assert task.task_id == "t1"
        assert task.method is dummy_method
        assert task.inputs == {"a": 1}
        assert task.status == TaskStatus.PENDING
        assert task.outputs == {}
        assert task.intermediate_outputs == {}

    def test_task_defaults(self, dummy_method):
        task = Task(task_id="t1", method=dummy_method, inputs={})
        assert task.failure_policy == FailurePolicy.STOP
        assert task.max_retries == 0
        assert task.retry_count == 0
        assert task.error_message is None
        assert task.slurm_job_id is None

    def test_duration_none_until_complete(self, dummy_method):
        task = Task(task_id="t1", method=dummy_method, inputs={})
        assert task.duration is None

    def test_duration_computed(self, dummy_method):
        from datetime import datetime, timedelta
        task = Task(task_id="t1", method=dummy_method, inputs={})
        task.started_at = datetime(2020, 1, 1, 0, 0, 0)
        task.completed_at = task.started_at + timedelta(seconds=42)
        assert task.duration == 42.0

    def test_to_dict_roundtrip(self, dummy_method):
        task = Task(task_id="t1", method=dummy_method, inputs={"x": 1})
        task.status = TaskStatus.COMPLETED
        task.outputs = {"y": 2}

        data = task.to_dict()
        assert data["task_id"] == "t1"
        assert data["status"] == "COMPLETED"
        assert data["method_name"] == dummy_method.name

        restored = Task.from_dict(data, method=dummy_method)
        assert restored.task_id == "t1"
        assert restored.status == TaskStatus.COMPLETED
        assert restored.outputs == {"y": 2}

    def test_repr(self, dummy_method):
        task = Task(task_id="t1", method=dummy_method, inputs={})
        assert "t1" in repr(task)


# ---------------------------------------------------------------------------
# Pipeline tests
# ---------------------------------------------------------------------------

class TestPipeline:
    def test_pipeline_construction(self, sample_pipeline):
        assert sample_pipeline.name == "sample"
        assert len(sample_pipeline.tasks) == 3
        assert len(sample_pipeline.connections) == 2

    def test_add_duplicate_task_raises(self, dummy_method):
        pipeline = Pipeline(name="p")
        pipeline.add_task(Task(task_id="a", method=dummy_method, inputs={}))
        with pytest.raises(ConfigurationError):
            pipeline.add_task(Task(task_id="a", method=dummy_method, inputs={}))

    def test_add_connection_missing_task_raises(self, dummy_method):
        pipeline = Pipeline(name="p")
        pipeline.add_task(Task(task_id="a", method=dummy_method, inputs={}))
        with pytest.raises(ConfigurationError):
            pipeline.add_connection("a", "nonexistent")

    def test_upstream_downstream(self, sample_pipeline):
        assert sample_pipeline.get_upstream_tasks("dock") == ["filter"]
        assert sample_pipeline.get_downstream_tasks("filter") == ["dock"]
        assert sample_pipeline.get_upstream_tasks("filter") == []

    def test_pooling_rules(self, parallel_pipeline):
        assert "pool" in parallel_pipeline.pooling_nodes
        assert parallel_pipeline.pooling_rules["pool"] == "all"

    def test_add_pooling_rule_missing_task_raises(self):
        pipeline = Pipeline(name="p")
        with pytest.raises(ConfigurationError):
            pipeline.add_pooling_rule("ghost", "all")

    def test_validate_empty_pipeline(self):
        pipeline = Pipeline(name="empty")
        is_valid, errors = pipeline.validate()
        assert not is_valid
        assert any("no tasks" in e.lower() for e in errors)

    def test_validate_good_pipeline(self, sample_pipeline):
        is_valid, errors = sample_pipeline.validate()
        assert is_valid
        assert errors == []

    def test_validate_detects_cycle(self, dummy_method):
        pipeline = Pipeline(name="cyclic")
        pipeline.add_task(Task(task_id="a", method=dummy_method, inputs={}))
        pipeline.add_task(Task(task_id="b", method=dummy_method, inputs={}))
        pipeline.add_connection("a", "b")
        pipeline.add_connection("b", "a")
        is_valid, errors = pipeline.validate()
        assert not is_valid
        assert any("cycle" in e.lower() for e in errors)

    def test_to_execution_graph(self, sample_pipeline):
        graph = sample_pipeline.to_execution_graph()
        assert isinstance(graph, ExecutionGraph)
        assert len(graph.tasks) == 3


# ---------------------------------------------------------------------------
# ExecutionGraph tests
# ---------------------------------------------------------------------------

class TestExecutionGraph:
    def _build(self, dummy_method, edges, ids):
        graph = ExecutionGraph()
        for tid in ids:
            graph.add_node(Task(task_id=tid, method=dummy_method, inputs={}))
        for frm, to in edges:
            graph.add_edge(frm, to)
        return graph

    def test_add_node_and_edge(self, dummy_method):
        graph = self._build(dummy_method, [("a", "b")], ["a", "b"])
        assert "a" in graph.tasks
        assert graph.get_downstream_tasks("a") == ["b"]
        assert graph.get_upstream_tasks("b") == ["a"]

    def test_add_duplicate_node_raises(self, dummy_method):
        graph = ExecutionGraph()
        graph.add_node(Task(task_id="a", method=dummy_method, inputs={}))
        with pytest.raises(DependencyError):
            graph.add_node(Task(task_id="a", method=dummy_method, inputs={}))

    def test_cycle_detection_on_edge(self, dummy_method):
        graph = self._build(dummy_method, [("a", "b")], ["a", "b"])
        with pytest.raises(DependencyError):
            graph.add_edge("b", "a")

    def test_topological_order(self, dummy_method):
        graph = self._build(
            dummy_method, [("a", "b"), ("b", "c")], ["a", "b", "c"]
        )
        order = graph.get_topological_order()
        assert order.index("a") < order.index("b") < order.index("c")

    def test_get_ready_tasks_initial(self, dummy_method):
        graph = self._build(
            dummy_method, [("a", "b"), ("b", "c")], ["a", "b", "c"]
        )
        ready = graph.get_ready_tasks()
        assert [t.task_id for t in ready] == ["a"]

    def test_get_ready_tasks_after_completion(self, dummy_method):
        graph = self._build(
            dummy_method, [("a", "b"), ("b", "c")], ["a", "b", "c"]
        )
        graph.mark_complete("a", TaskStatus.COMPLETED)
        ready = graph.get_ready_tasks()
        assert [t.task_id for t in ready] == ["b"]

    def test_get_all_downstream(self, dummy_method):
        graph = self._build(
            dummy_method, [("a", "b"), ("b", "c")], ["a", "b", "c"]
        )
        assert graph.get_all_downstream_tasks("a") == {"b", "c"}

    def test_is_complete(self, dummy_method):
        graph = self._build(dummy_method, [("a", "b")], ["a", "b"])
        assert not graph.is_complete()
        graph.mark_complete("a", TaskStatus.COMPLETED)
        graph.mark_complete("b", TaskStatus.COMPLETED)
        assert graph.is_complete()

    def test_from_pipeline(self, sample_pipeline):
        graph = ExecutionGraph.from_pipeline(sample_pipeline)
        assert len(graph.tasks) == 3
        assert len(graph.graph.edges()) == 2
