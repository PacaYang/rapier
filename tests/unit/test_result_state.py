"""Unit tests for engine result and state dataclasses."""

from datetime import datetime, timedelta

import pytest

from rapier.engine.result import ExecutionResult, TaskResult
from rapier.engine.state import ExecutionState, ExecutionPhase


class TestTaskResult:
    def test_defaults(self):
        r = TaskResult(task_id="t", status="COMPLETED")
        assert r.outputs == {}
        assert r.retries == 0
        assert r.error is None


class TestExecutionResult:
    def _result(self):
        start = datetime(2020, 1, 1, 0, 0, 0)
        result = ExecutionResult(pipeline_name="p", success=True, start_time=start)
        result.end_time = start + timedelta(seconds=10)
        result.completed_tasks = ["a", "b"]
        result.failed_tasks = ["c"]
        result.task_results = {
            "a": TaskResult("a", "COMPLETED", outputs={"x": 1}, duration=2.0),
            "b": TaskResult("b", "COMPLETED", duration=3.0),
            "c": TaskResult("c", "FAILED", error="boom"),
        }
        return result

    def test_duration(self):
        result = self._result()
        assert result.duration == 10.0

    def test_total_tasks(self):
        result = self._result()
        assert result.total_tasks == 3

    def test_get_task_result(self):
        result = self._result()
        assert result.get_task_result("a").status == "COMPLETED"
        assert result.get_task_result("missing") is None

    def test_final_outputs(self):
        result = self._result()
        outputs = result.get_final_outputs()
        assert "a" in outputs
        assert "c" not in outputs  # failed

    def test_generate_report(self):
        result = self._result()
        report = result.generate_report()
        assert "SUCCESS" in report
        assert "a" in report
        assert "c" in report

    def test_dict_roundtrip(self):
        result = self._result()
        data = result.to_dict()
        restored = ExecutionResult.from_dict(data)
        assert restored.pipeline_name == "p"
        assert restored.total_tasks == 3
        assert restored.get_task_result("a").outputs == {"x": 1}

    def test_save_load(self, tmp_path):
        result = self._result()
        path = tmp_path / "result.json"
        result.save(path)
        loaded = ExecutionResult.load(path)
        assert loaded.success
        assert loaded.completed_tasks == ["a", "b"]


class TestExecutionState:
    def test_create_initial(self):
        state = ExecutionState.create_initial("pid", "pname", ["a", "b", "c"])
        assert state.total_tasks == 3
        assert len(state.tasks_pending) == 3
        assert state.phase == ExecutionPhase.INITIALIZING

    def test_status_transition(self):
        state = ExecutionState.create_initial("pid", "pname", ["a"])
        state.update_task_status("a", "RUNNING")
        assert "a" in state.tasks_running
        assert "a" not in state.tasks_pending
        state.update_task_status("a", "COMPLETED")
        assert "a" in state.tasks_completed

    def test_progress(self):
        state = ExecutionState.create_initial("pid", "pname", ["a", "b"])
        assert state.get_progress() == 0.0
        state.update_task_status("a", "COMPLETED")
        assert state.get_progress() == 50.0

    def test_is_finished(self):
        state = ExecutionState.create_initial("pid", "pname", ["a"])
        assert not state.is_finished
        state.phase = ExecutionPhase.COMPLETED
        assert state.is_finished

    def test_dict_roundtrip(self):
        state = ExecutionState.create_initial("pid", "pname", ["a", "b"])
        state.update_task_status("a", "COMPLETED")
        data = state.to_dict()
        restored = ExecutionState.from_dict(data)
        assert restored.pipeline_id == "pid"
        assert "a" in restored.tasks_completed

    def test_save_load(self, tmp_path):
        state = ExecutionState.create_initial("pid", "pname", ["a"])
        path = tmp_path / "state.json"
        state.save(path)
        loaded = ExecutionState.load(path)
        assert loaded.pipeline_name == "pname"
