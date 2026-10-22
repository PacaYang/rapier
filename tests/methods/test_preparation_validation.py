"""Preparation results account for every input before publication."""

import pytest

from rapier.core.enums import TaskStatus
from rapier.core.method import RunContext
from rapier.core.task import Task
from rapier.data import Molecules, ProteinStructure, Report
from rapier.methods.preparation.ligand_prep import LigandPreparation
from rapier.methods.preparation.protein_prep import ProteinPreparation


def context(method, tmp_path, inputs):
    task = Task("preparation", method, inputs=inputs)
    task.work_dir = tmp_path
    return task, RunContext.for_task(task)


def test_ligand_preparation_accounts_for_invalid_duplicate_occurrence(tmp_path):
    pytest.importorskip("rdkit")
    method = LigandPreparation(config={"energy_minimize": False})
    task, _ = context(method, tmp_path, {"ligands": ["CCO same", "invalid same", "CC same"]})

    assert method.execute(task) == TaskStatus.COMPLETED, task.error_message
    log = task.outputs["preparation_log"].load()
    assert (log["total"], log["successful"], log["failed"]) == (3, 2, 1)
    assert [(row["id"], row["occurrence"]) for row in log["successful_molecules"]] == [("same", 1), ("same", 3)]
    assert log["failed_molecules"][0]["occurrence"] == 2
    assert [record.properties["rapier_input_occurrence"] for record in task.outputs["prepared_ligands"].records()] == ["1", "3"]


def test_all_invalid_ligands_still_fail(tmp_path):
    pytest.importorskip("rdkit")
    method = LigandPreparation()
    task, _ = context(method, tmp_path, {"ligands": ["invalid first", "broken second"]})
    assert method.execute(task) == TaskStatus.FAILED
    assert "No valid molecules" in task.error_message
    assert not task.outputs


def test_ligand_validator_aggregates_inventory_and_report_errors(tmp_path):
    pytest.importorskip("rdkit")
    method = LigandPreparation(config={"energy_minimize": False})
    inputs = {"ligands": Molecules.from_smiles(["CCO", "CC"], names=["a", "b"])}
    _, ctx = context(method, tmp_path, inputs)
    outputs = method.format_outputs(method.run(inputs, ctx), ctx)
    # Corruption in a report must not mask the unexplained missing output.
    outputs["prepared_ligands"] = Molecules.write_sdf([], tmp_path / "missing.sdf")
    report = outputs["preparation_log"].load()
    report.update(total=5, failed=2)
    report["failed_molecules"] = [{"id": "unknown", "occurrence": 1}]
    outputs["preparation_log"] = Report.inline(report, "json")

    errors = method.validate_outputs(inputs, outputs, ctx)
    assert any("No molecules" in error for error in errors)
    assert any("total must equal" in error for error in errors)
    assert any("must explain" in error for error in errors)
    assert any("partition every input" in error for error in errors)
    assert any("does not match successful_molecules" in error for error in errors)


def test_ligand_validator_checks_molecules_when_log_is_malformed(tmp_path):
    pytest.importorskip("rdkit")
    method = LigandPreparation()
    inputs = {"ligands": Molecules.from_smiles(["CCO"], names=["a"])}
    _, ctx = context(method, tmp_path, inputs)
    broken_log = tmp_path / "broken.json"
    broken_log.write_text("{")
    outputs = {
        "prepared_ligands": Molecules.write_sdf(["a\ninvalid structure\n"], tmp_path / "bad.sdf"),
        "preparation_log": Report.from_file(broken_log, "json"),
    }
    errors = method.validate_outputs(inputs, outputs, ctx)
    assert any("unparsable" in error for error in errors)
    assert any("preparation_log cannot be read" in error for error in errors)


def test_protein_validator_checks_both_ports_together(tmp_path):
    method = ProteinPreparation(config={"output_format": "pdbqt"})
    _, ctx = context(method, tmp_path, {})
    outputs = {
        "prepared_receptor": ProteinStructure.from_file(tmp_path / "absent.pdb", "pdb"),
        "preparation_log": Report.inline({"steps": [], "warnings": "unexpected"}, "json"),
    }
    errors = method.validate_outputs({}, outputs, ctx)
    assert any("not found" in error for error in errors)
    assert any("format must be pdbqt" in error for error in errors)
    assert any("steps must describe" in error for error in errors)
    assert any("warnings must be" in error for error in errors)


def test_protein_dependencies_reject_bad_coordinates(tmp_path):
    method = ProteinPreparation()
    path = tmp_path / "bad.pdb"
    path.write_text("ATOM      1  CA  ALA A   1         nan   0.000   0.000  1.00  0.00\n")
    inputs = {"receptor": ProteinStructure.from_file(path, "pdb")}
    _, ctx = context(method, tmp_path, inputs)
    assert any("invalid atom coordinates" in error for error in method.check_dependencies(inputs, ctx))
