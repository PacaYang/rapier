"""Hermetic tests for the scheduler-facing AutoDock Vina workflow."""

from pathlib import Path
import json

import pytest

from rapier.core.enums import TaskStatus
from rapier.core.method import RunContext
from rapier.core.task import Task
from rapier.data import Poses, Scores
from rapier.methods.docking.vina import VinaDocking
from rapier.methods.docking import vina_runner
from rapier.methods.runner import read_outputs, run_task_file


@pytest.fixture(autouse=True)
def available_mock_tools(monkeypatch):
    """Tool execution is mocked, so runtime availability must be mocked too."""
    monkeypatch.setattr(VinaDocking, "validate_environment", lambda self: (True, []))


def _fake_tools(command, cwd):
    tool = command[0]
    if tool == "mk_prepare_receptor.py":
        prefix = Path(command[command.index("-o") + 1])
        (prefix.parent / f"{prefix.name}.pdbqt").write_text("RECEPTOR\n")
        return 0, "", ""
    if tool == "mk_prepare_ligand.py":
        output = Path(command[command.index("-o") + 1])
        output.write_text("LIGAND\n")
        return 0, "", ""
    if tool == "vina":
        output = Path(command[command.index("--out") + 1])
        output.write_text(
            "REMARK VINA RESULT: -8.4 0.000 0.000\n"
            "ATOM      1  C   LIG     1       0.000   0.000   0.000\n"
        )
        log = Path(command[command.index("--log") + 1])
        log.write_text(
            "mode | affinity | dist from best mode\n"
            "-----+----------+--------------------\n"
            "1 -8.4 0.000 0.000\n"
        )
        return 0, log.read_text(), ""
    if tool == "mk_export.py":
        output = Path(command[command.index("-s") + 1])
        output.write_text(
            "pose\n  test\n\n  1  0  0  0  0  0  0  0  0  0999 V2000\n"
            "    0.0000    0.0000    0.0000 C   0  0  0  0  0  0  0  0  0  0  0  0\n"
            "M  END\n$$$$\n"
        )
        return 0, "", ""
    raise AssertionError(f"unexpected command: {command}")


def _task(tmp_path, sample_data_dir, method=None):
    method = method or VinaDocking(parameters={"seed": 17, "verbosity": 0})
    task = Task(
        task_id="vina_runner",
        method=method,
        inputs={
            "receptor": str(sample_data_dir / "receptor.pdb"),
            "ligands": str(sample_data_dir / "ligands.sdf"),
            "binding_site": {
                "center": [10.0, 15.0, 20.0],
                "size": [20.0, 20.0, 20.0],
            },
        },
    )
    task.work_dir = tmp_path / "work"
    return task


def test_get_command_writes_task_and_invokes_generic_runner(tmp_path, sample_data_dir):
    method = VinaDocking(parameters={"seed": 123, "scoring": "vinardo"})
    task = _task(tmp_path, sample_data_dir, method)

    command = method.get_command(task)
    manifest = json.loads((task.work_dir / "task.json").read_text())

    assert "-m rapier.methods.runner --task" in command
    assert not (task.work_dir / "vina_manifest.json").exists()
    assert manifest["method"]["config"]["seed"] == 123
    assert manifest["method"]["config"]["scoring"] == "vinardo"
    assert manifest["inputs"]["binding_site"]["center"] == [10.0, 15.0, 20.0]


@pytest.mark.parametrize("titles, expected", [
    (("", "dup", "dup", "dup-2", "dup"), ["ligand_0", "dup", "dup-3", "dup-2", "dup-4"]),
    (("a", "a", "a-2"), ["a", "a-3", "a-2"]),
    (("", "ligand_0"), ["ligand_0-2", "ligand_0"]),
])
def test_vina_ligand_inventory_preserves_blank_titles_and_duplicate_occurrences(
    tmp_path, sample_data_dir, monkeypatch, titles, expected,
):
    source = tmp_path / "ligands.sdf"
    source.write_text("".join(
        f"{title}\n  program\n\n  1  0  0  0  0  0  0  0  0  0999 V2000\n"
        "    0.0000    0.0000    0.0000 C   0  0  0  0  0  0  0  0  0  0  0  0\n"
        "M  END\n$$$$\n"
        for title in titles
    ))
    output_dir = tmp_path / "split"
    output_dir.mkdir()

    records = vina_runner.split_sdf(source, output_dir)

    assert [record["name"] for record in records] == expected
    assert Path(records[0]["sdf"]).read_text().startswith(f"{titles[0]}\n  program\n")
    monkeypatch.setattr(vina_runner, "_run_tool", _fake_tools)
    task = _task(tmp_path, sample_data_dir)
    task.inputs["ligands"] = str(source)
    assert task.method.execute(task) == TaskStatus.COMPLETED, task.error_message
    assert [record.id for record in task.outputs["docked_poses"].records()] == expected


def test_generic_scheduler_runner_executes_vina_and_writes_validated_receipt(
    tmp_path, sample_data_dir, monkeypatch,
):
    monkeypatch.setattr(vina_runner, "_run_tool", _fake_tools)
    task = _task(tmp_path, sample_data_dir)
    calls = []
    validate = VinaDocking.validate_outputs

    def record_validation(self, inputs, outputs, ctx):
        calls.append(set(outputs))
        return validate(self, inputs, outputs, ctx)

    monkeypatch.setattr(VinaDocking, "validate_outputs", record_validation)
    task.method.get_command(task)
    result = run_task_file(task.work_dir / "task.json")

    assert result["status"] == "completed", result["error"]
    assert result["validation_version"] == 1
    assert calls == [{"docked_poses", "docking_scores"}]
    assert len(read_outputs(task)["docked_poses"].records()) == 3
    manifest = json.loads((task.work_dir / "vina_manifest.json").read_text())
    assert manifest["box"]["center"] == [10.0, 15.0, 20.0]


def test_runner_prepares_docks_exports_and_collects_typed_outputs(
    tmp_path, sample_data_dir, monkeypatch
):
    monkeypatch.setattr(vina_runner, "_run_tool", _fake_tools)
    task = _task(tmp_path, sample_data_dir)

    status = task.method.execute(task)

    assert status == TaskStatus.COMPLETED, task.error_message
    poses, scores = task.outputs["docked_poses"], task.outputs["docking_scores"]
    assert isinstance(poses, Poses) and poses.type == "molecule.poses"
    assert poses.format == "sdf"
    assert isinstance(scores, Scores) and scores.format == "csv"
    assert scores.path.read_text().count("-8.4") == 3
    assert (task.work_dir / "outputs" / "vina_run.json").exists()
    # Unified keys: every score row and every pose title is the same molecule id.
    assert [(row["id"], row["pose"]) for row in scores.records()] == [
        ("ethanol", 1), ("benzene", 1), ("acetic_acid", 1),
    ]
    assert [record.id for record in poses.records()] == ["ethanol", "benzene", "acetic_acid"]
    assert scores.directions()["affinity_kcal_mol"] == "lower"


def test_runner_keeps_successful_ligands_when_one_fails(tmp_path, sample_data_dir, monkeypatch):
    def one_failure(command, cwd):
        if command[0] == "vina" and "benzene" in command[command.index("--ligand") + 1]:
            return 1, "", "simulated ligand failure"
        return _fake_tools(command, cwd)

    monkeypatch.setattr(vina_runner, "_run_tool", one_failure)
    task = _task(tmp_path, sample_data_dir)

    assert task.method.execute(task) == TaskStatus.COMPLETED
    metadata = task.outputs["docking_scores"].metadata
    assert "benzene" in metadata["failed_ligands"]
    assert metadata["successful_ligands"] == 2


def test_runner_retries_vina_without_log_for_legacy_builds(
    tmp_path, sample_data_dir, monkeypatch
):
    seen = {"fallback": 0}

    def legacy_vina(command, cwd):
        if command[0] == "vina" and "--log" in command:
            seen["fallback"] += 1
            return 1, "", "unrecognised option '--log'"
        if command[0] == "vina":
            output = Path(command[command.index("--out") + 1])
            output.write_text("REMARK VINA RESULT: -8.4 0.000 0.000\n")
            return 0, "1 -8.4 0.000 0.000\n", ""
        return _fake_tools(command, cwd)

    monkeypatch.setattr(vina_runner, "_run_tool", legacy_vina)
    task = _task(tmp_path, sample_data_dir)

    assert task.method.execute(task) == TaskStatus.COMPLETED
    assert seen["fallback"] == 3
    assert task.outputs["docking_scores"].path.exists()


def test_disallowed_partial_run_still_accounts_for_all_ligands_and_validates_pool(
    tmp_path, sample_data_dir, monkeypatch,
):
    def one_failure(command, cwd):
        if command[0] == "vina" and "benzene" in command[command.index("--ligand") + 1]:
            return 1, "", "simulated failure"
        return _fake_tools(command, cwd)

    monkeypatch.setattr(vina_runner, "_run_tool", one_failure)
    method = VinaDocking(config={"allow_partial_failures": False})
    task = _task(tmp_path, sample_data_dir, method)
    calls = []
    validate = method.validate_outputs

    def record_validation(inputs, outputs, ctx):
        calls.append(set(outputs))
        return validate(inputs, outputs, ctx)

    monkeypatch.setattr(method, "validate_outputs", record_validation)

    assert method.execute(task) == TaskStatus.FAILED
    assert len(calls) == 1
    summary = json.loads((task.work_dir / "outputs" / "vina_run.json").read_text())
    assert len(summary["ligands"]) == 3
    assert summary["failed_ligands"] == ["benzene"]
    assert "allow_partial_failures=false" in task.error_message
    assert not task.outputs


def test_pooled_validator_checks_bad_scores_even_when_pose_port_is_missing(
    tmp_path, sample_data_dir, monkeypatch,
):
    monkeypatch.setattr(vina_runner, "_run_tool", _fake_tools)
    task = _task(tmp_path, sample_data_dir)
    assert task.method.execute(task) == TaskStatus.COMPLETED, task.error_message
    scores = task.outputs["docking_scores"]
    rows = scores.rows()
    rows[0]["affinity_kcal_mol"] = "nan"
    outputs = {"docking_scores": Scores.from_records(rows, tmp_path / "bad.csv", metadata=scores.metadata)}

    errors = task.method.validate_outputs(task.inputs, outputs, RunContext.for_task(task))

    assert any("Missing docked_poses" in error for error in errors)
    assert any("invalid affinity_kcal_mol" in error for error in errors)


def test_pooled_validator_aggregates_bad_summary_and_numeric_rows(
    tmp_path, sample_data_dir, monkeypatch,
):
    monkeypatch.setattr(vina_runner, "_run_tool", _fake_tools)
    task = _task(tmp_path, sample_data_dir)
    assert task.method.execute(task) == TaskStatus.COMPLETED, task.error_message
    (task.work_dir / "outputs" / "vina_run.json").write_text("{bad summary")
    rows = task.outputs["docking_scores"].rows()
    rows[0]["affinity_kcal_mol"] = "nan"
    rows[1]["rmsd_ub"] = "infinity"
    Scores.from_records(rows, task.work_dir / "outputs" / "docking_scores.csv")
    ctx = RunContext.for_task(task)
    outputs = task.method.format_outputs(task.method._collect(ctx.output_dir, ctx.output_errors), ctx)
    errors = ctx.output_errors + task.method.validate_outputs(task.inputs, outputs, ctx)

    assert any("Could not read Vina run summary" in error for error in errors)
    assert any("row 0 has invalid affinity_kcal_mol" in error for error in errors)
    assert any("row 1 has invalid rmsd_ub" in error for error in errors)
