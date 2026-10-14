"""Tests for the Method parent class: unified input formatting, output arrangement, runner."""

import subprocess

import pytest

from rapier.core.enums import TaskStatus
from rapier.core.method import Method, MethodError
from rapier.core.specs import ConfigField, PortSpec
from rapier.core.task import Task
from rapier.data import BindingSite, Molecules, Report, Scores
from rapier.methods.screening.molecule_filter import MoleculeFilter


class CountAtoms(Method):
    """Toy method: heavy-atom count per molecule."""

    register_name = "count_atoms"
    inputs = {
        "molecules": PortSpec(Molecules, "sdf"),
        "site": PortSpec(BindingSite, "box", required=False),
    }
    outputs = {"counts": PortSpec(Scores, "csv"), "summary": PortSpec(Report, "json")}
    config_fields = {"minimum": ConfigField(int, default=0, minimum=0)}

    def run(self, inputs, ctx):
        rows = [{"id": r.id, "heavy_atoms": len(r.heavy_atoms)} for r in inputs["molecules"].records()]
        if any(row["heavy_atoms"] < self.config["minimum"] for row in rows):
            raise MethodError("molecule below minimum size")
        return {"counts": rows, "summary": {"molecules": len(rows)}}


def _task(method, inputs, tmp_path, task_id="count"):
    task = Task(task_id, method, inputs=inputs)
    task.work_dir = tmp_path / task_id
    return task


class TestLifecycle:
    def test_raw_inputs_in_typed_outputs_out(self, tmp_path, sample_data_dir):
        task = _task(CountAtoms(), {"molecules": str(sample_data_dir / "ligands.sdf")}, tmp_path)
        assert task.method.execute(task) == TaskStatus.COMPLETED, task.error_message
        # Inputs were typed and staged; outputs arranged into the declared port classes.
        assert isinstance(task.inputs["molecules"], Molecules)
        assert task.inputs["molecules"].path.parent == task.work_dir / "input" / "normalized"
        counts, summary = task.outputs["counts"], task.outputs["summary"]
        assert isinstance(counts, Scores) and counts.path.name == "counts.csv"
        assert {r["id"]: r["heavy_atoms"] for r in counts.records()} == {
            "ethanol": "3", "benzene": "6", "acetic_acid": "4",
        }
        assert isinstance(summary, Report) and summary.load() == {"molecules": 3}
        assert counts.provenance.producing_task == "count" and counts.provenance.output_port == "counts"

    def test_inputs_are_converted_to_an_accepted_format(self, tmp_path):
        pytest.importorskip("rdkit")
        library = tmp_path / "lib.smi"
        library.write_text("CCO ethanol\nc1ccccc1 benzene\n")
        task = _task(CountAtoms(), {"molecules": str(library)}, tmp_path)
        assert task.method.execute(task) == TaskStatus.COMPLETED, task.error_message
        staged = task.inputs["molecules"]
        assert staged.format == "sdf"
        assert staged.provenance.conversions[0]["converter"] == "rdkit.molecule.collection.smiles_to_sdf"
        assert {r["id"] for r in task.outputs["counts"].records()} == {"ethanol", "benzene"}

    def test_validation_collects_every_problem(self):
        valid, errors = CountAtoms().validate_inputs({"site": {"center": [0, 0], "size": [1, 1, 1]}, "bogus": 1})
        assert not valid
        assert any("Missing required input: molecules" in e for e in errors)
        assert any("box center" in e for e in errors)
        assert any("Unknown input port(s): bogus" in e for e in errors)

    def test_method_error_fails_the_task(self, tmp_path, sample_data_dir):
        task = _task(CountAtoms(config={"minimum": 5}), {"molecules": str(sample_data_dir / "ligands.sdf")}, tmp_path)
        assert task.method.execute(task) == TaskStatus.FAILED
        assert "molecule below minimum size" in task.error_message

    def test_config_and_outputs_are_strict(self):
        with pytest.raises(ValueError, match="Unknown configuration"):
            CountAtoms(config={"maximum": 1})
        with pytest.raises(ValueError, match="parameters, not both"):
            CountAtoms(config={}, parameters={})
        with pytest.raises(ValueError, match="Unknown output port"):
            CountAtoms().format_outputs({"nope": []})

    def test_check_config_only_sees_valid_fields(self):
        class Strict(CountAtoms):
            def check_config(self, config):
                return [] if config["minimum"] < 100 else ["minimum too large"]

        with pytest.raises(ValueError, match="must be of type int"):
            Strict(config={"minimum": "x"})  # Not a KeyError from the cross-field hook.
        with pytest.raises(ValueError, match="minimum too large"):
            Strict(config={"minimum": 200})


class TestGenericRunner:
    FILTER = {"criteria": [{"column": "heavy_atoms", "op": ">=", "value": 4}]}

    def test_scheduler_round_trip_in_a_fresh_interpreter(self, tmp_path, sample_data_dir):
        method = MoleculeFilter(name="gate", config=self.FILTER)
        task = _task(method, {"molecules": str(sample_data_dir / "ligands.sdf")}, tmp_path, "gate")
        task.inputs = method.format_inputs(task.inputs, tmp_path / "gate" / "input" / "normalized")
        command = method.get_command(task)
        assert "rapier.methods.runner" in command and (task.work_dir / "task.json").exists()

        result = subprocess.run(command, shell=True, capture_output=True, text=True)
        assert result.returncode == 0, result.stderr

        outputs = method.collect_outputs(_task(method, {}, tmp_path, "gate"))
        assert outputs["filtered_molecules"].ids() == ["benzene", "acetic_acid"]
        assert isinstance(outputs["filtered_scores"], Scores)

    def test_failed_job_is_reported_on_collect(self, tmp_path):
        from rapier.methods.runner import run_task_file, write_task_file

        method = MoleculeFilter(name="gate")
        task = _task(method, {"molecules": str(tmp_path / "missing.sdf")}, tmp_path, "gate")
        result = run_task_file(write_task_file(method, task))
        assert result["status"] == "failed" and "not found" in result["error"]
        with pytest.raises(RuntimeError, match="not found"):
            method.collect_outputs(task)
