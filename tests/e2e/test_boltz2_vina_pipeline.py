"""Integration coverage for the normalized Boltz-2 to Vina handoff."""

import os
import shutil
from pathlib import Path

import pytest

from rapier.artifacts import Artifact, ArtifactStorage, InputResolver
from rapier.config import ConfigParser
from rapier.core.enums import TaskStatus
from rapier.core.pipeline import Pipeline


ROOT = Path(__file__).resolve().parents[2]
EXAMPLE = ROOT / "examples" / "pipelines" / "07_boltz2_vina.yaml"


def test_supplied_pipeline_resolves_boltz_outputs_into_vina(tmp_path):
    pytest.importorskip("rdkit")
    pipeline = Pipeline.from_config(ConfigParser().parse_file(str(EXAMPLE)))
    predict = pipeline.tasks["predict"]
    prepare = pipeline.tasks["prepare_ligands"]
    dock = pipeline.tasks["dock"]
    resolver = InputResolver()

    def stage(task):
        """What TaskRunner.prepare_task does: resolve bindings, then let the method format them."""
        task.inputs = task.method.format_inputs(resolver.resolve_task(task), task.input_dir / "normalized")

    for task in (predict, prepare, dock):
        task.work_dir = tmp_path / task.task_id
        task.input_dir = task.work_dir / "input"
        task.output_dir = task.work_dir / "output"
        task.log_dir = task.work_dir / "logs"
        for directory in (task.input_dir, task.output_dir, task.log_dir):
            directory.mkdir(parents=True, exist_ok=True)

    stage(predict)
    assert predict.inputs["proteins"][0].type == "sequence.protein"
    assert predict.inputs["ligands"][0].type == "molecule.collection"

    receptor = ROOT / "tests" / "fixtures" / "sample_data" / "receptor.pdb"
    predict.outputs = {
        "vina_receptor": Artifact("structure.protein", "pdb", receptor),
        "docking_box": Artifact(
            "binding_site",
            "box",
            {"center": [10.0, 20.0, 30.0], "size": [24.0, 24.0, 24.0]},
            storage=ArtifactStorage.INLINE,
        ),
    }
    stage(prepare)
    assert prepare.method.execute(prepare) == TaskStatus.COMPLETED, prepare.error_message
    stage(dock)

    assert dock.inputs["receptor"].type == "structure.protein"
    assert dock.inputs["receptor"].is_file
    assert dock.inputs["ligands"].type == "molecule.collection"
    assert dock.inputs["ligands"].format == "sdf"
    assert dock.inputs["binding_site"].value["center"] == [10.0, 20.0, 30.0]

    from rdkit import Chem

    molecules = [molecule for molecule in Chem.SDMolSupplier(str(dock.inputs["ligands"].path)) if molecule]
    assert len(molecules) == 2
    valid, errors = dock.method.input_spec.validate(dock.inputs)
    assert valid, errors
    valid, errors = dock.method.validate_inputs(dock.inputs)
    assert valid, errors


def _external_tools_available():
    executables = (
        "boltz", "vina", "mk_prepare_receptor.py",
        "mk_prepare_ligand.py", "mk_export.py",
    )
    if not all(shutil.which(name) for name in executables):
        return False
    try:
        import prody  # noqa: F401
    except ImportError:
        return False
    return True


@pytest.mark.boltz2_vina
@pytest.mark.skipif(
    os.environ.get("RAPIER_RUN_BOLTZ2_VINA_E2E") != "1",
    reason="set RAPIER_RUN_BOLTZ2_VINA_E2E=1 to run the expensive external pipeline",
)
@pytest.mark.skipif(not _external_tools_available(), reason="Boltz, Vina, or Meeko CLI is unavailable")
def test_supplied_pipeline_with_real_boltz_and_vina(tmp_path):
    """Run the shipped example through real external tools when explicitly enabled."""
    pytest.importorskip("gemmi")
    pytest.importorskip("rdkit")
    pipeline = Pipeline.from_config(ConfigParser().parse_file(str(EXAMPLE)))
    resolver = InputResolver()

    for task_id in ("predict", "prepare_ligands", "dock"):
        task = pipeline.tasks[task_id]
        task.work_dir = tmp_path / task_id
        task.input_dir = task.work_dir / "input"
        task.output_dir = task.work_dir / "output"
        task.log_dir = task.work_dir / "logs"
        for directory in (task.input_dir, task.output_dir, task.log_dir):
            directory.mkdir(parents=True, exist_ok=True)
        task.inputs = task.method.format_inputs(resolver.resolve_task(task), task.input_dir / "normalized")
        valid, errors = task.method.validate_inputs(task.inputs)
        assert valid, errors
        valid, errors = task.method.validate_environment()
        assert valid, errors
        status = task.method.execute(task)
        assert status == TaskStatus.COMPLETED, task.error_message

    predict = pipeline.tasks["predict"]
    dock = pipeline.tasks["dock"]
    assert predict.outputs["vina_receptor"].path.exists()
    assert all(value > 0 for value in predict.outputs["docking_box"].value["size"])
    assert dock.outputs["docked_poses"].path.exists()
    assert dock.outputs["docking_scores"].path.exists()
