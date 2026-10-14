"""End-to-end tests for the Vina docking and ligand filter methods.

These tests exercise VinaDocking with the checked-in sample data. Real docking
requires AutoDock Vina and Meeko, so those tests are skipped when the
executables are unavailable.
"""

import shutil

import pytest

from rapier.methods.docking.vina import VinaDocking
from rapier.core.task import Task
from rapier.core.enums import TaskStatus


VINA_AVAILABLE = shutil.which("vina") is not None
MEEKO_AVAILABLE = shutil.which("mk_prepare_ligand.py") is not None

try:
    import rdkit  # noqa: F401
    RDKIT_AVAILABLE = True
except ImportError:
    RDKIT_AVAILABLE = False


class TestVinaStructure:
    """Tests that run without Vina installed."""

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
        if not (VINA_AVAILABLE and MEEKO_AVAILABLE):
            assert not is_valid
            assert len(errors) > 0


@pytest.mark.vina
@pytest.mark.skipif(
    not (VINA_AVAILABLE and MEEKO_AVAILABLE and RDKIT_AVAILABLE),
    reason="Vina, Meeko, or RDKit not available",
)
class TestVinaExecution:
    """Tests that require a real Vina installation."""

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
        assert status == TaskStatus.COMPLETED, task.error_message
        assert "filtered_ligands" in task.outputs

        # All three small molecules (ethanol, benzene, acetic acid) pass MW<500
        filtered = task.outputs["filtered_ligands"]
        assert filtered.path.exists()
        assert filtered.ids() == ["ethanol", "benzene", "acetic_acid"]
        # Every molecule's properties come back as an id-keyed score table.
        properties = {row["id"]: row for row in task.outputs["properties"].records()}
        assert set(properties) == {"ethanol", "benzene", "acetic_acid"}
        assert float(properties["benzene"]["mw"]) == pytest.approx(78.11, abs=0.01)
