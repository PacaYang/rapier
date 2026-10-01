"""End-to-end test for a parallel pipeline with result pooling.

Tests the [A, B] -> pool -> C topology, where two upstream branches run and
their results are combined by the ResultPooler.
"""

import pytest

from rapier.core.task import Task
from rapier.core.pipeline import Pipeline
from rapier.core.enums import TaskStatus
from rapier.engine.scheduler import Scheduler
from rapier.engine.pooler import ResultPooler


def run_with_pooling(pipeline):
    """Run a pipeline locally, applying pooling rules at pooling nodes."""
    graph = pipeline.to_execution_graph()
    scheduler = Scheduler(graph)
    pooler = ResultPooler()
    pooled_results = {}

    while not scheduler.is_finished():
        ready = scheduler.get_ready_tasks()
        if not ready:
            break
        for task in ready:
            scheduler.mark_running(task)
            status = task.method.execute(task)

            # If this is a pooling node, pool upstream results
            if task.task_id in pipeline.pooling_rules:
                upstream_ids = pipeline.get_upstream_tasks(task.task_id)
                upstream_tasks = [graph.tasks[uid] for uid in upstream_ids]
                rule = pipeline.pooling_rules[task.task_id]
                pooled_results[task.task_id] = pooler.pool_results(upstream_tasks, rule)

            scheduler.mark_completed(task, status)
    return scheduler, pooled_results


class TestParallelPipeline:
    def test_parallel_branches_complete(self, parallel_pipeline):
        scheduler, _ = run_with_pooling(parallel_pipeline)
        assert "vina" in scheduler.completed_tasks
        assert "smina" in scheduler.completed_tasks
        assert "pool" in scheduler.completed_tasks
        assert "analyze" in scheduler.completed_tasks

    def test_pool_runs_after_both_branches(self, parallel_pipeline):
        scheduler, pooled = run_with_pooling(parallel_pipeline)
        # 'all' pooling collects both completed upstream tasks
        assert "pool" in pooled
        result = pooled["pool"]
        assert {t.task_id for t in result} == {"vina", "smina"}

    def test_both_branches_ready_initially(self, parallel_pipeline):
        graph = parallel_pipeline.to_execution_graph()
        scheduler = Scheduler(graph)
        ready_ids = {t.task_id for t in scheduler.get_ready_tasks()}
        assert ready_ids == {"vina", "smina"}


class TestPoolingStrategies:
    def _branch_tasks(self, scores):
        """Build completed tasks carrying a score output."""
        tasks = []
        for tid, score in scores.items():
            t = Task(task_id=tid, method=None, inputs={})
            t.status = TaskStatus.COMPLETED
            t.outputs = {"score": score}
            tasks.append(t)
        return tasks

    def test_best_pooling_selects_lowest_energy(self):
        pooler = ResultPooler()
        tasks = self._branch_tasks({"vina": -8.0, "smina": -9.5})
        best = pooler.pool_results(tasks, "best", metric="score", minimize=True)
        assert best.task_id == "smina"

    def test_all_pooling_collects_successful(self):
        pooler = ResultPooler()
        tasks = self._branch_tasks({"vina": -8.0, "smina": -9.5})
        result = pooler.pool_results(tasks, "all")
        assert len(result) == 2

    def test_merge_outputs_averages_scores(self):
        pooler = ResultPooler()
        tasks = self._branch_tasks({"vina": -8.0, "smina": -10.0})
        merged = pooler.merge_outputs(tasks)
        assert merged["score"] == pytest.approx(-9.0)


class TestParallelFromConfig:
    def test_build_parallel_config(self, sample_config_dir):
        from rapier.config.parser import ConfigParser

        config = ConfigParser().parse_file(str(sample_config_dir / "parallel.yaml"))
        pipeline = Pipeline.from_config(config)

        # pool_results has two upstream branches
        upstream = pipeline.get_upstream_tasks("pool_results")
        assert set(upstream) == {"vina_dock", "smina_dock"}

        # pooling rule is registered
        assert "pool_results" in pipeline.pooling_nodes
