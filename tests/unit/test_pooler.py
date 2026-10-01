"""Unit tests for the ResultPooler."""

import pytest

from rapier.core.task import Task
from rapier.core.enums import TaskStatus
from rapier.engine.pooler import ResultPooler


def make_task(task_id, status, outputs=None, method=None):
    task = Task(task_id=task_id, method=method, inputs={})
    task.status = status
    task.outputs = outputs or {}
    return task


@pytest.fixture
def pooler():
    return ResultPooler()


class TestPoolingRules:
    def test_empty_tasks_raises(self, pooler):
        with pytest.raises(ValueError):
            pooler.pool_results([], "all")

    def test_any_returns_first_success(self, pooler):
        tasks = [
            make_task("a", TaskStatus.FAILED),
            make_task("b", TaskStatus.COMPLETED),
            make_task("c", TaskStatus.COMPLETED),
        ]
        result = pooler.pool_results(tasks, "any")
        assert len(result) == 1
        assert result[0].task_id == "b"

    def test_any_no_success(self, pooler):
        tasks = [make_task("a", TaskStatus.FAILED)]
        assert pooler.pool_results(tasks, "any") == []

    def test_all_returns_all_successful(self, pooler):
        tasks = [
            make_task("a", TaskStatus.COMPLETED),
            make_task("b", TaskStatus.FAILED),
            make_task("c", TaskStatus.COMPLETED),
        ]
        result = pooler.pool_results(tasks, "all")
        assert {t.task_id for t in result} == {"a", "c"}

    def test_majority_success(self, pooler):
        tasks = [
            make_task("a", TaskStatus.COMPLETED),
            make_task("b", TaskStatus.COMPLETED),
            make_task("c", TaskStatus.FAILED),
        ]
        result = pooler.pool_results(tasks, "majority")
        assert len(result) == 2

    def test_majority_failure(self, pooler):
        tasks = [
            make_task("a", TaskStatus.COMPLETED),
            make_task("b", TaskStatus.FAILED),
            make_task("c", TaskStatus.FAILED),
        ]
        result = pooler.pool_results(tasks, "majority")
        assert result == []

    def test_best_maximize(self, pooler):
        tasks = [
            make_task("a", TaskStatus.COMPLETED, {"score": 0.5}),
            make_task("b", TaskStatus.COMPLETED, {"score": 0.9}),
            make_task("c", TaskStatus.COMPLETED, {"score": 0.2}),
        ]
        result = pooler.pool_results(tasks, "best", metric="score")
        assert result.task_id == "b"

    def test_best_minimize(self, pooler):
        tasks = [
            make_task("a", TaskStatus.COMPLETED, {"energy": -8.0}),
            make_task("b", TaskStatus.COMPLETED, {"energy": -9.5}),
            make_task("c", TaskStatus.COMPLETED, {"energy": -7.0}),
        ]
        result = pooler.pool_results(tasks, "best", metric="energy", minimize=True)
        assert result.task_id == "b"

    def test_best_bracket_syntax(self, pooler):
        tasks = [
            make_task("a", TaskStatus.COMPLETED, {"score": 0.5}),
            make_task("b", TaskStatus.COMPLETED, {"score": 0.9}),
        ]
        result = pooler.pool_results(tasks, "best[score]")
        assert result.task_id == "b"

    def test_best_requires_metric(self, pooler):
        tasks = [make_task("a", TaskStatus.COMPLETED, {"score": 0.5})]
        with pytest.raises(ValueError):
            pooler.pool_results(tasks, "best")

    def test_best_no_metric_in_outputs(self, pooler):
        tasks = [make_task("a", TaskStatus.COMPLETED, {"other": 1})]
        with pytest.raises(ValueError):
            pooler.pool_results(tasks, "best", metric="score")

    def test_first_completed(self, pooler):
        tasks = [
            make_task("a", TaskStatus.FAILED),
            make_task("b", TaskStatus.COMPLETED),
        ]
        result = pooler.pool_results(tasks, "first")
        assert result.task_id == "b"

    def test_unknown_rule_raises(self, pooler):
        tasks = [make_task("a", TaskStatus.COMPLETED)]
        with pytest.raises(ValueError):
            pooler.pool_results(tasks, "bogus")


class TestCustomPooling:
    def test_custom_function(self, pooler):
        pooler.register_custom_function("count", lambda tasks: len(tasks))
        tasks = [
            make_task("a", TaskStatus.COMPLETED),
            make_task("b", TaskStatus.COMPLETED),
        ]
        result = pooler.pool_results(tasks, "custom[count]")
        assert result == 2

    def test_custom_unregistered_raises(self, pooler):
        tasks = [make_task("a", TaskStatus.COMPLETED)]
        with pytest.raises(ValueError):
            pooler.pool_results(tasks, "custom[missing]")


class TestMergeOutputs:
    def test_merge_lists_concat(self, pooler):
        tasks = [
            make_task("a", TaskStatus.COMPLETED, {"hits": [1, 2]}),
            make_task("b", TaskStatus.COMPLETED, {"hits": [3, 4]}),
        ]
        merged = pooler.merge_outputs(tasks, merge_strategy="concat")
        assert sorted(merged["hits"]) == [1, 2, 3, 4]

    def test_merge_numbers_average(self, pooler):
        tasks = [
            make_task("a", TaskStatus.COMPLETED, {"score": 2.0}),
            make_task("b", TaskStatus.COMPLETED, {"score": 4.0}),
        ]
        merged = pooler.merge_outputs(tasks)
        assert merged["score"] == 3.0

    def test_merge_no_successful(self, pooler):
        tasks = [make_task("a", TaskStatus.FAILED, {"x": 1})]
        assert pooler.merge_outputs(tasks) == {}
