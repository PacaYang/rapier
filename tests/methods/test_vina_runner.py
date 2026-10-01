"""Hermetic tests for the scheduler-facing AutoDock Vina workflow."""

from pathlib import Path
import json

from rapier.core.enums import TaskStatus
from rapier.core.task import Task
from rapier.methods.docking.vina import VinaDocking
from rapier.methods.docking import vina_runner


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
        output.write_text("pose\n$$$$\n")
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


def test_get_command_writes_manifest_and_invokes_runner_module(tmp_path, sample_data_dir):
    method = VinaDocking(parameters={"seed": 123, "scoring": "vinardo"})
    task = _task(tmp_path, sample_data_dir, method)

    command = method.get_command(task)
    manifest = json.loads((task.work_dir / "vina_manifest.json").read_text())

    assert "vina_runner" in command
    assert "echo 'Executing" not in command
    assert manifest["config"]["seed"] == 123
    assert manifest["config"]["scoring"] == "vinardo"
    assert manifest["box"]["center"] == [10.0, 15.0, 20.0]


def test_runner_prepares_docks_exports_and_collects_typed_outputs(
    tmp_path, sample_data_dir, monkeypatch
):
    monkeypatch.setattr(vina_runner, "_run_tool", _fake_tools)
    task = _task(tmp_path, sample_data_dir)

    status = task.method.execute(task)

    assert status == TaskStatus.COMPLETED
    assert task.outputs["docked_poses"].type == "molecule.collection"
    assert task.outputs["docked_poses"].format == "sdf"
    assert task.outputs["docking_scores"].format == "csv"
    assert task.outputs["docking_scores"].path.read_text().count("-8.4") == 3
    assert (task.work_dir / "outputs" / "vina_run.json").exists()


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
