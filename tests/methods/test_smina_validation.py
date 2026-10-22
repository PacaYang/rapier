"""Smina validates complete ligand outcomes and independent output artifacts."""

import copy

import pytest

from rapier import Task
from rapier.core.enums import TaskStatus
from rapier.core.method import RunContext
from rapier.data import Molecules, Poses, ProteinStructure, Scores
from rapier.methods.docking import smina


BLOCK = ("{name}\n  test\n\n  1  0  0  0  0  0  0  0  0  0999 V2000\n"
         "    1.0000    2.0000    3.0000 C   0  0  0  0  0  0  0  0  0  0  0  0\nM  END\n")


def result_pool(tmp_path):
    method = smina.SminaDocking()
    inputs = {"ligands": Molecules.write_sdf([BLOCK.format(name="a"), BLOCK.format(name="b")], tmp_path / "input.sdf")}
    task = Task("smina", method, inputs)
    task.work_dir = tmp_path / "work"
    metadata = {
        "ligand_outcomes": [{"index": 0, "id": "a", "success": True},
                            {"index": 1, "id": "b", "success": False, "error": "docking failed"}],
        "successful_ligands": 1, "failed_ligands": ["b"],
    }
    outputs = {
        "docked_poses": Poses.write_sdf([BLOCK.format(name="a")], tmp_path / "poses.sdf", copy.deepcopy(metadata)),
        "docking_scores": Scores.from_records([
            {"id": "a", "pose": 1, "affinity_kcal_mol": -7.5, "rmsd_lb": 0, "rmsd_ub": 0},
        ], tmp_path / "scores.csv", metadata=copy.deepcopy(metadata)),
    }
    return method, inputs, outputs, RunContext.for_task(task)


def test_smina_accepts_accounted_partial_result(tmp_path):
    method, inputs, outputs, ctx = result_pool(tmp_path)
    assert method.validate_outputs(inputs, outputs, ctx) == []


def test_smina_reconciles_sibling_outcomes_and_declared_counters(tmp_path):
    method, inputs, outputs, ctx = result_pool(tmp_path)
    metadata = outputs["docking_scores"].metadata
    metadata["ligand_outcomes"][1]["error"] = "different outcome"
    metadata.update(successful_ligands=True, failed_ligands=[])
    errors = method.validate_outputs(inputs, outputs, ctx)
    assert any("outcome metadata disagree" in error for error in errors)
    assert any("docking_scores successful_ligands" in error for error in errors)
    assert any("docking_scores failed_ligands" in error for error in errors)


@pytest.mark.parametrize("invalid_index", [True, 0.5, "0.5", -1])
def test_smina_outcome_indexes_must_be_exact_nonnegative_integers(tmp_path, invalid_index):
    method, inputs, outputs, ctx = result_pool(tmp_path)
    for artifact in outputs.values():
        artifact.metadata["ligand_outcomes"][0]["index"] = invalid_index
    errors = method.validate_outputs(inputs, outputs, ctx)
    assert any("no valid input index" in error for error in errors)
    assert any("did not account" in error for error in errors)


def test_smina_aggregates_invalid_pose_and_all_numeric_columns_without_sibling(tmp_path):
    method, inputs, outputs, ctx = result_pool(tmp_path)
    metadata = outputs["docking_scores"].metadata
    outputs = {"docking_scores": Scores.inline([
        {"id": "a", "pose": True, "affinity_kcal_mol": True, "rmsd_lb": "nan", "rmsd_ub": "invalid"},
    ], "json", metadata)}
    errors = method.validate_outputs(inputs, outputs, ctx)
    for fragment in ("Missing docked_poses", "no valid pose number", "invalid affinity", "invalid rmsd_lb", "invalid rmsd_ub"):
        assert any(fragment in error for error in errors), errors


def test_smina_keeps_valid_rows_for_checks_when_other_json_rows_are_malformed(tmp_path):
    method, inputs, outputs, ctx = result_pool(tmp_path)
    metadata = outputs["docking_scores"].metadata
    outputs["docking_scores"] = Scores.inline([
        None,
        {"id": "a", "pose": 1, "affinity_kcal_mol": "nan", "rmsd_lb": True, "rmsd_ub": 0},
    ], "json", metadata)
    errors = method.validate_outputs(inputs, outputs, ctx)
    assert any("invalid row" in error.lower() for error in errors)
    assert any("invalid affinity" in error for error in errors)
    assert any("invalid rmsd_lb" in error for error in errors)


def test_smina_rejects_duplicate_scores_and_extra_pose_occurrences(tmp_path):
    method, inputs, outputs, ctx = result_pool(tmp_path)
    metadata = outputs["docking_scores"].metadata
    rows = outputs["docking_scores"].rows()
    outputs["docking_scores"] = Scores.from_records(rows + rows, tmp_path / "duplicate.csv", metadata=metadata)
    errors = method.validate_outputs(inputs, outputs, ctx)
    assert any("Duplicate docking score key" in error for error in errors)
    assert any("inventories differ" in error for error in errors)

    outputs["docking_scores"] = Scores.from_records(rows, tmp_path / "original.csv", metadata=metadata)
    outputs["docked_poses"] = Poses.write_sdf([BLOCK.format(name="a")] * 2, tmp_path / "extra_pose.sdf", metadata)
    errors = method.validate_outputs(inputs, outputs, ctx)
    assert any("poses-only=[('a', 2)]" in error for error in errors)


def test_smina_dependencies_aggregate_bad_receptor_ligand_and_box(tmp_path, monkeypatch):
    method = smina.SminaDocking(config={"box_size": [1, float("nan"), 1]})
    receptor = tmp_path / "invalid.pdbqt"
    receptor.write_text("RECEPTOR WITHOUT ATOMS\n")
    ligands = Molecules.write_sdf(["bad\ninvalid\n"], tmp_path / "invalid.sdf")
    task = Task("bad", method, {})
    task.work_dir = tmp_path / "work"
    errors = method.check_dependencies({"receptor": ProteinStructure.from_file(receptor), "ligands": ligands}, RunContext.for_task(task))
    assert any("no ATOM/HETATM" in error for error in errors)
    assert any("no usable heavy-atom coordinates" in error for error in errors)
    assert any("three finite positive numbers" in error for error in errors)

    monkeypatch.setattr(method, "validate_environment", lambda: (True, []))
    monkeypatch.setattr(method, "run", lambda *args: pytest.fail("run must not execute with dependency errors"))
    task.inputs = {"receptor": ProteinStructure.from_file(receptor), "ligands": ligands}
    assert method.execute(task) == TaskStatus.FAILED
    for fragment in ("no ATOM/HETATM", "no usable heavy-atom coordinates", "three finite positive numbers"):
        assert fragment in task.error_message


@pytest.mark.parametrize("failure", ["tool", "parse"])
def test_smina_suffixes_preserve_unique_input_ids_and_record_partial_failure(tmp_path, sample_data_dir, monkeypatch, failure):
    def fake_smina(command, cwd=None):
        ligand = str(command[command.index("--ligand") + 1])
        if "1_same.sdf" in ligand:
            if failure == "tool":
                return 1, "", "simulated failure"
            output = command[command.index("--out") + 1]
            from pathlib import Path
            Path(output).write_bytes(b"\xff invalid ligand output")
            return 0, "complete", ""
        output = command[command.index("--out") + 1]
        Molecules.write_sdf([BLOCK.format(name="result")], output)
        return 0, "mode | affinity | dist from best mode\n-----+----\n1 -7.0 0.000 0.000\n", ""

    monkeypatch.setattr(smina, "run_command", fake_smina)
    method = smina.SminaDocking()
    monkeypatch.setattr(method, "validate_environment", lambda: (True, []))
    inputs = {
        "receptor": sample_data_dir / "receptor.pdb",
        "ligands": Molecules.write_sdf([BLOCK.format(name=name) for name in ("same", "same", "same-2")], tmp_path / "duplicates.sdf"),
    }
    task = Task("smina_duplicates", method, inputs)
    task.work_dir = tmp_path / "work"
    assert method.execute(task) == TaskStatus.COMPLETED, task.error_message
    assert task.outputs["docked_poses"].ids() == ["same", "same-2"]
    assert task.outputs["docking_scores"].metadata["failed_ligands"] == ["same-3"]
