"""The shared preflight, pooled validation, and scheduler completion boundary."""

import json
from abc import ABC, abstractmethod
from datetime import datetime
from types import SimpleNamespace

import pytest

from rapier.core.enums import FailurePolicy, TaskStatus
from rapier.core.method import Method, OUTPUT_VALIDATION_VERSION
from rapier.core.pipeline import Pipeline
from rapier.core.specs import PortSpec
from rapier.core.task import Task
from rapier.data import File, Report
from rapier.engine.executor import ExecutionEngine
from rapier.engine.result import ExecutionResult
from rapier.engine.scheduler import Scheduler
from rapier.engine.state import ExecutionState
from rapier.engine.task_runner import TaskRunner
from rapier.methods.registry import MethodRegistry
from rapier.methods.runner import read_outputs, run_task_file, write_task_file


class PooledMethod(Method):
    outputs = {"reports": PortSpec(Report, "json", cardinality="many"),
               "summary": PortSpec(Report, "json")}
    events = []

    def check_dependencies(self, inputs, ctx):
        self.events.append("dependencies")
        return []

    def run(self, inputs, ctx):
        self.events.append("run")
        return {"reports": [{"n": 1}, {"n": 2}],
                "summary": {"total": 2}}

    def validate_outputs(self, inputs, outputs, ctx):
        self.events.append(("validate", len(outputs.get("reports", [])), "summary" in outputs))
        return []


class ReceiptNamedOutputMethod(PooledMethod):
    outputs = {"outputs": PortSpec(Report, "json")}

    def run(self, inputs, ctx):
        return {"outputs": {"answer": 42}}

    def validate_outputs(self, inputs, outputs, ctx):
        return [] if outputs["outputs"].load() == {"answer": 42} else ["report was overwritten"]


def make_task(tmp_path, method=None):
    task = Task("pooled", method or PooledMethod(), inputs={})
    task.work_dir = tmp_path / "work"
    return task


def test_environment_and_dependency_errors_prevent_execution(tmp_path, monkeypatch):
    task = make_task(tmp_path)
    monkeypatch.setattr(task.method, "validate_environment", lambda: (False, ["missing tool"]))
    monkeypatch.setattr(task.method, "check_dependencies", lambda inputs, ctx: ["missing MSA", "bad reference"])
    monkeypatch.setattr(task.method, "run", lambda *args: pytest.fail("run must not execute"))
    task.outputs = {"stale": "output"}
    task.error_message = "old error"
    assert task.method.execute(task) == TaskStatus.FAILED
    assert all(message in task.error_message for message in ("missing tool", "missing MSA", "bad reference"))
    assert task.outputs == {}


def test_generic_runner_checks_environment_and_all_dependencies(tmp_path, monkeypatch):
    events = []

    def environment(self):
        events.append("environment")
        return False, ["execution host tool is missing"]

    def dependencies(self, inputs, ctx):
        events.append("dependencies")
        return ["missing reference", "missing MSA"]

    monkeypatch.setattr(PooledMethod, "validate_environment", environment)
    monkeypatch.setattr(PooledMethod, "check_dependencies", dependencies)
    monkeypatch.setattr(PooledMethod, "run", lambda *args: pytest.fail("run must not execute"))
    task = make_task(tmp_path)
    result = run_task_file(write_task_file(task.method, task))
    assert result["status"] == "failed"
    assert result["outputs"] == {}
    assert events == ["environment", "dependencies"]
    for message in ("execution host tool is missing", "missing reference", "missing MSA"):
        assert message in result["error"]
    with pytest.raises(RuntimeError, match="execution host tool is missing"):
        read_outputs(task)


def test_complete_pool_validated_once_locally_and_by_runner(tmp_path):
    PooledMethod.events = []
    task = make_task(tmp_path)
    assert task.method.execute(task) == TaskStatus.COMPLETED
    assert PooledMethod.events == ["dependencies", "run", ("validate", 2, True)]
    assert task.method.collect_outputs(task) == task.outputs
    assert PooledMethod.events.count(("validate", 2, True)) == 1

    PooledMethod.events = []
    result = run_task_file(write_task_file(task.method, task))
    assert result["status"] == "completed", result["error"]
    assert result["validation_version"] == OUTPUT_VALIDATION_VERSION
    assert len(read_outputs(task)["reports"]) == 2
    assert PooledMethod.events == ["dependencies", "run", ("validate", 2, True)]


def test_normalization_collects_errors_before_semantic_validation(tmp_path):
    class Broken(PooledMethod):
        outputs = dict(PooledMethod.outputs, files=PortSpec(File, "text", cardinality="many"))

        def run(self, inputs, ctx):
            ctx.output_errors.append("summary could not be derived")
            return {"reports": [42, Report.inline({"n": 2}, "json"), object()],
                    "files": [ctx.output_dir / "missing1.txt", ctx.output_dir / "missing2.txt"],
                    "unknown": True}

        def validate_outputs(self, inputs, outputs, ctx):
            assert len(outputs["reports"]) == 1
            assert len(outputs["files"]) == 2
            return ["report identities disagree", "summary count mismatch"]

    task = make_task(tmp_path, Broken())
    assert task.method.execute(task) == TaskStatus.FAILED
    for message in ("summary could not be derived", "Unknown output port", "item 0", "item 2",
                    "missing1.txt", "missing2.txt", "Missing required output: summary",
                    "report identities disagree", "summary count mismatch"):
        assert message in task.error_message
    assert task.outputs == {}


def test_required_many_empty_collection_is_passed_to_child(tmp_path):
    task = make_task(tmp_path)
    task.method.run = lambda inputs, ctx: {"reports": [], "summary": {"total": 0}}
    task.method.validate_outputs = lambda inputs, outputs, ctx: (
        ["expected nonempty reports"] if not outputs["reports"] else [])
    assert task.method.execute(task) == TaskStatus.FAILED
    assert "expected nonempty reports" in task.error_message
    assert "Cannot use" not in task.error_message


@pytest.mark.parametrize("use_paths", [False, True])
def test_invalid_singleton_retains_all_artifacts_for_pooled_validation(tmp_path, use_paths):
    task = make_task(tmp_path)
    values = [tmp_path / "missing1.json", tmp_path / "missing2.json"]
    if not use_paths:
        values = [Report.from_file(path) for path in values]
    task.method.run = lambda inputs, ctx: {
        "reports": [],
        "summary": values,
    }
    pools = []
    task.method.validate_outputs = lambda inputs, outputs, ctx: pools.append(outputs) or []
    assert task.method.execute(task) == TaskStatus.FAILED
    assert len(pools) == 1
    assert len(pools[0]["summary"]) == 2
    assert "expects one artifact" in task.error_message
    assert "missing1.json" in task.error_message
    assert "missing2.json" in task.error_message


def test_scheduler_receipt_does_not_overwrite_output_named_outputs(tmp_path):
    task = make_task(tmp_path, ReceiptNamedOutputMethod())
    result = run_task_file(write_task_file(task.method, task))
    assert result["status"] == "completed", result["error"]
    outputs = read_outputs(task)
    assert outputs["outputs"].path.name == "outputs_artifact.json"
    assert outputs["outputs"].load() == {"answer": 42}


def test_explicit_artifact_at_receipt_path_fails_validation(tmp_path):
    task = make_task(tmp_path, ReceiptNamedOutputMethod())

    def collide(inputs, ctx):
        path = ctx.output_dir / "outputs.json"
        path.write_text(json.dumps({"answer": 42}))
        return {"outputs": Report.from_file(path)}

    task.method.run = collide
    assert task.method.execute(task) == TaskStatus.FAILED
    assert "reserved for the validation receipt" in task.error_message
    assert task.outputs == {}


def test_generated_artifact_names_do_not_overwrite_sibling_ports(tmp_path):
    class ConflictingNames(PooledMethod):
        outputs = {"outputs": PortSpec(Report, "json"),
                   "outputs_artifact": PortSpec(Report, "json"),
                   "reports": PortSpec(Report, "json", cardinality="many"),
                   "reports_0": PortSpec(Report, "json")}

        def run(self, inputs, ctx):
            return {"outputs": {"answer": 42}, "outputs_artifact": {"answer": 43},
                    "reports": [{"answer": 44}, {"answer": 45}], "reports_0": {"answer": 46}}

        def validate_outputs(self, inputs, outputs, ctx):
            assert outputs["outputs"].load() == {"answer": 42}
            assert outputs["outputs_artifact"].load() == {"answer": 43}
            assert [report.load() for report in outputs["reports"]] == [{"answer": 44}, {"answer": 45}]
            assert outputs["reports_0"].load() == {"answer": 46}
            return []

    task = make_task(tmp_path, ConflictingNames())
    assert task.method.execute(task) == TaskStatus.COMPLETED, task.error_message


def test_contract_reports_wrong_type_alongside_missing_file(tmp_path):
    task = make_task(tmp_path)
    task.method.run = lambda inputs, ctx: {"reports": [], "summary": File.from_file(tmp_path / "wrong.txt", "text")}
    assert task.method.execute(task) == TaskStatus.FAILED
    assert "wrong.txt" in task.error_message
    assert "produced file/text; expected types ['report']" in task.error_message


def test_contract_type_error_survives_artifact_validator_exception(tmp_path):
    from rapier.artifacts import ArtifactRegistry, ArtifactType, OutputSpec

    registry = ArtifactRegistry()

    def broken_validator(artifact):
        raise ValueError("malformed artifact payload")

    registry.register(ArtifactType("file", ("text",), validator=broken_validator))
    registry.register(ArtifactType("report", ("json",)))
    spec = OutputSpec({"summary": PooledMethod.outputs["summary"]})
    valid, errors = spec.validate({"summary": File.from_file(tmp_path / "wrong.txt", "text")}, registry)
    assert not valid
    assert any("malformed artifact payload" in error for error in errors)
    assert any("produced file/text; expected types ['report']" in error for error in errors)


def test_semantic_validator_exception_preserves_other_errors(tmp_path):
    task = make_task(tmp_path)
    task.method.run = lambda inputs, ctx: {"reports": [tmp_path / "missing.json"]}

    def broken_validator(*args):
        raise ValueError("broken validator")

    task.method.validate_outputs = broken_validator
    assert task.method.execute(task) == TaskStatus.FAILED
    assert "missing.json" in task.error_message
    assert "Missing required output: summary" in task.error_message
    assert "broken validator" in task.error_message


def test_registration_requires_both_hooks_and_accepts_inheritance():
    registry = MethodRegistry()
    names = ["validation_missing", "validation_half", "validation_inherited"]
    try:
        with pytest.raises(ValueError, match="check_dependencies, validate_outputs"):
            registry.register(names[0], Method)

        class Half(Method):
            def check_dependencies(self, inputs, ctx):
                return []

        with pytest.raises(ValueError, match="validate_outputs"):
            registry.register(names[1], Half)

        class Abstract(PooledMethod, ABC):
            @abstractmethod
            def validate_outputs(self, inputs, outputs, ctx):
                pass

        with pytest.raises(ValueError, match="validate_outputs"):
            registry.register(names[1], Abstract)

        class Inherited(PooledMethod):
            pass

        registry.register(names[2], Inherited)
        assert registry.get(names[2]) is Inherited
    finally:
        for name in names:
            if registry.has(name):
                registry.unregister(name)


def test_collection_rejects_legacy_receipt_and_removed_files(tmp_path):
    task = make_task(tmp_path)
    receipt = task.work_dir / "outputs" / "outputs.json"
    receipt.parent.mkdir(parents=True)
    receipt.write_text(json.dumps({"status": "completed", "outputs": {}}))
    with pytest.raises(RuntimeError, match="rerun"):
        read_outputs(task)

    result = run_task_file(write_task_file(task.method, task))
    assert result["status"] == "completed"
    (receipt.parent / "summary.json").unlink()
    with pytest.raises(ValueError, match="summary.json"):
        read_outputs(task)


def test_auto_discovery_reports_invalid_class_and_continues(monkeypatch, capsys):
    from rapier.methods import registry as registry_module

    class Incomplete(Method):
        register_name = "validation_incomplete"

    class Complete(PooledMethod):
        register_name = "validation_complete"

    module = SimpleNamespace(Incomplete=Incomplete, Complete=Complete)
    real_import = registry_module.importlib.import_module
    monkeypatch.setattr(registry_module.pkgutil, "walk_packages", lambda **kwargs: [(None, "validation_methods", False)])
    monkeypatch.setattr(registry_module.importlib, "import_module",
                        lambda name: module if name == "validation_methods" else real_import(name))
    registry = MethodRegistry()
    try:
        assert registry.auto_discover() == 1
        assert registry.has(Complete.register_name)
        assert not registry.has(Incomplete.register_name)
        warning = capsys.readouterr().out
        assert "Incomplete" in warning
        assert "check_dependencies, validate_outputs" in warning
    finally:
        if registry.has(Complete.register_name):
            registry.unregister(Complete.register_name)


def test_prepare_task_defers_environment_check_to_runtime(tmp_path):
    task = make_task(tmp_path)
    task.method.validate_environment = lambda: pytest.fail("submission host environment must not be checked")
    assert TaskRunner(dry_run=True).prepare_task(task)


def test_failed_local_retry_cannot_collect_previous_scheduler_receipt(tmp_path):
    task = make_task(tmp_path)
    result = run_task_file(write_task_file(task.method, task))
    assert result["status"] == "completed"
    task.method.check_dependencies = lambda inputs, ctx: ["dependency lost"]
    assert task.method.execute(task) == TaskStatus.FAILED
    with pytest.raises(FileNotFoundError, match="No validated"):
        task.method.collect_outputs(task)


def test_collection_failure_overrides_scheduler_success(tmp_path):
    class BrokenCollector(PooledMethod):
        def collect_outputs(self, task):
            raise ValueError("missing pooled output")

    task = make_task(tmp_path, BrokenCollector())
    task.failure_policy = FailurePolicy.CONTINUE
    downstream = Task("downstream", PooledMethod(), inputs={})
    pipeline = Pipeline("invalid_outputs")
    pipeline.add_task(task)
    pipeline.add_task(downstream)
    pipeline.add_connection(task.task_id, downstream.task_id)
    engine = ExecutionEngine(state_dir=tmp_path / "state")
    engine.pipeline = pipeline
    engine.scheduler = Scheduler(pipeline.to_execution_graph())
    engine.scheduler.mark_running(task)
    engine.state = ExecutionState.create_initial("invalid_outputs", "invalid_outputs", list(pipeline.tasks))
    engine.result = ExecutionResult("invalid_outputs", False, datetime.now())
    task.status = TaskStatus.COMPLETED

    engine._handle_task_completion(task, TaskStatus.COMPLETED)

    assert task.status == TaskStatus.FAILED
    assert "missing pooled output" in task.error_message
    assert task.task_id in engine.scheduler.failed_tasks
    assert task.task_id not in engine.scheduler.completed_tasks
    assert downstream.task_id in engine.scheduler.skipped_tasks
    assert not engine.result.completed_tasks
    assert task.task_id in engine.state.tasks_failed
    assert task.task_id not in engine.state.tasks_completed
    assert engine.state.task_statuses[task.task_id] == "FAILED"

