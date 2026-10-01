"""End-to-end test for a simple serial pipeline.

Drives a real Scheduler over a pipeline of in-memory methods, executing each
method's ``execute()`` directly. This exercises the full orchestration path
(ready-task detection, completion, dependency unblocking) without a cluster.
"""

import pytest

from rapier.core.enums import TaskStatus
from rapier.engine.scheduler import Scheduler
from rapier.config.parser import ConfigParser
from rapier.core.pipeline import Pipeline


def run_pipeline_locally(pipeline):
    """Execute a pipeline by running each task's method in dependency order.

    Returns a dict of task_id -> final TaskStatus.
    """
    graph = pipeline.to_execution_graph()
    scheduler = Scheduler(graph)
    results = {}

    while not scheduler.is_finished():
        ready = scheduler.get_ready_tasks()
        if not ready:
            break
        for task in ready:
            scheduler.mark_running(task)
            status = task.method.execute(task)
            scheduler.mark_completed(task, status)
            results[task.task_id] = status
            # On failure with STOP policy, stop scheduling
            if status == TaskStatus.FAILED:
                from rapier.core.enums import FailurePolicy
                if task.failure_policy == FailurePolicy.STOP:
                    return results
    return results


class TestSimpleSerialPipeline:
    def test_all_tasks_complete(self, sample_pipeline):
        results = run_pipeline_locally(sample_pipeline)
        assert results["filter"] == TaskStatus.COMPLETED
        assert results["dock"] == TaskStatus.COMPLETED
        assert results["score"] == TaskStatus.COMPLETED

    def test_execution_order_respects_dependencies(self, sample_methods):
        order = []

        # Wrap execute to record order
        for name, method in sample_methods.items():
            original = method.execute

            def make_tracked(orig, nm):
                def tracked(task):
                    order.append(nm)
                    return orig(task)
                return tracked

            method.execute = make_tracked(original, name)

        pipeline = Pipeline(name="ordered")
        from rapier.core.task import Task
        pipeline.add_task(Task(task_id="filter", method=sample_methods["filter"], inputs={}))
        pipeline.add_task(Task(task_id="dock", method=sample_methods["dock"], inputs={}))
        pipeline.add_task(Task(task_id="score", method=sample_methods["score"], inputs={}))
        pipeline.add_connection("filter", "dock")
        pipeline.add_connection("dock", "score")

        run_pipeline_locally(pipeline)
        assert order == ["filter", "dock", "score"]

    def test_methods_executed_once(self, sample_pipeline, sample_methods):
        run_pipeline_locally(sample_pipeline)
        for method in sample_methods.values():
            assert method.execute_count == 1


class TestPipelineFromConfig:
    def test_build_from_fixture_config(self, sample_config_dir):
        config = ConfigParser().parse_file(str(sample_config_dir / "simple.yaml"))
        assert config.name == "simple_test_pipeline"
        assert len(config.methods) == 3

        pipeline = Pipeline.from_config(config)
        assert len(pipeline.tasks) == 3
        # filter_step -> dock_step -> score_step
        assert pipeline.get_downstream_tasks("filter_step") == ["dock_step"]
        assert pipeline.get_downstream_tasks("dock_step") == ["score_step"]

    def test_config_pipeline_validates(self, sample_config_dir):
        config = ConfigParser().parse_file(str(sample_config_dir / "simple.yaml"))
        pipeline = Pipeline.from_config(config)
        is_valid, errors = pipeline.validate()
        assert is_valid, errors


class TestExecutionEngineDryRun:
    def test_engine_completes_serial(self, sample_pipeline, tmp_path):
        from rapier.engine.executor import ExecutionEngine

        engine = ExecutionEngine(
            max_parallel=2,
            poll_interval=0,
            dry_run=True,
            state_dir=tmp_path / "state",
        )
        result = engine.execute(sample_pipeline, save_state=False)
        # In dry-run, the SLURM client reports COMPLETED for every job
        assert result.success
        assert set(result.completed_tasks) == {"filter", "dock", "score"}
