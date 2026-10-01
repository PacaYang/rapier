"""End-to-end test for the Vina docking multi-step method.

These tests exercise the VinaDocking method structure with the checked-in
sample data. The actual docking step requires AutoDock Vina and Open Babel,
so those tests are skipped when the executables are unavailable.
"""

import shutil

import pytest

from rapier.methods.docking.vina import VinaDocking
from rapier.core.task import Task
from rapier.core.enums import TaskStatus


VINA_AVAILABLE = shutil.which("vina") is not None
OBABEL_AVAILABLE = shutil.which("obabel") is not None

try:
    import rdkit  # noqa: F401
    RDKIT_AVAILABLE = True
except ImportError:
    RDKIT_AVAILABLE = False


class TestVinaStructure:
    """Tests that run without Vina installed."""

    def test_multi_step_definition(self):
        method = VinaDocking()
        assert len(method.steps) == 2
        assert method.steps[0].name == "PrepareReceptor"
        assert method.steps[1].name == "DockLigands"

    def test_validate_inputs_with_sample_data(self, sample_data_dir):
        method = VinaDocking(parameters={
            "box_center": [0, 0, 0],
            "box_size": [20, 20, 20],
        })
        inputs = {
            "receptor": str(sample_data_dir / "receptor.pdb"),
            "ligands": str(sample_data_dir / "ligands.sdf"),
        }
        is_valid, errors = method.validate_inputs(inputs)
        assert is_valid, errors

    def test_environment_validation_reports_missing(self):
        method = VinaDocking()
        is_valid, errors = method.validate_environment()
        if not (VINA_AVAILABLE and OBABEL_AVAILABLE):
            assert not is_valid
            assert len(errors) > 0


@pytest.mark.vina
@pytest.mark.skipif(
    not (VINA_AVAILABLE and OBABEL_AVAILABLE and RDKIT_AVAILABLE),
    reason="Vina, Open Babel, or RDKit not available",
)
class TestVinaExecution:
    """Tests that require a real Vina installation."""

    def test_prepare_receptor_step(self, sample_data_dir, tmp_path):
        method = VinaDocking()
        task = Task(
            task_id="vina_test",
            method=method,
            inputs={
                "receptor": str(sample_data_dir / "receptor.pdb"),
                "ligands": str(sample_data_dir / "ligands.sdf"),
            },
        )
        task.work_dir = str(tmp_path / "work")

        status = method._prepare_receptor(task)
        assert status == TaskStatus.COMPLETED
        assert "receptor_pdbqt" in task.intermediate_outputs

    def test_full_docking(self, sample_data_dir, tmp_path):
        method = VinaDocking(parameters={
            "box_center": [0, 0, 0],
            "box_size": [20, 20, 20],
            "exhaustiveness": 1,  # fast for testing
        })
        task = Task(
            task_id="vina_full",
            method=method,
            inputs={
                "receptor": str(sample_data_dir / "receptor.pdb"),
                "ligands": str(sample_data_dir / "ligands.sdf"),
            },
        )
        task.work_dir = str(tmp_path / "work")

        status = method.execute(task)
        assert status == TaskStatus.COMPLETED
        assert "docking_scores" in task.outputs


@pytest.mark.skipif(not RDKIT_AVAILABLE, reason="RDKit not available")
class TestLigandFilterExecution:
    """E2E test of LigandFilter, which only needs RDKit."""

    def test_filter_sample_ligands(self, sample_data_dir, tmp_path):
        from rapier.methods.screening.ligand_filter import LigandFilter

        method = LigandFilter(parameters={"mw_max": 500})
        task = Task(
            task_id="filter_test",
            method=method,
            inputs={"ligands": str(sample_data_dir / "ligands.sdf")},
        )
        task.work_dir = str(tmp_path / "work")

        status = method.execute(task)
        assert status == TaskStatus.COMPLETED
        assert "filtered_ligands" in task.outputs

        # All three small molecules (ethanol, benzene, acetic acid) pass MW<500
        from pathlib import Path
        filtered = Path(task.outputs["filtered_ligands"])
        assert filtered.exists()
