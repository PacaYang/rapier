"""Tests for Vina docking method."""

import pytest
from pathlib import Path
from unittest.mock import patch

from rapier.data import Molecules, Poses, ProteinStructure, Scores
from rapier.core.method import RunContext
from rapier.core.task import Task
from rapier.methods.docking.vina import VinaDocking


def test_vina_initialization():
    """Test VinaDocking initialization."""
    method = VinaDocking(
        name='vina',
        version='1.2',
        parameters={
            'box_center': [1.0, 2.0, 3.0],
            'box_size': [20.0, 20.0, 20.0],
            'exhaustiveness': 8
        }
    )

    assert method.name == 'vina'
    assert method.version == '1.2'
    assert method.config['box_center'] == [1.0, 2.0, 3.0]
    assert method.config['exhaustiveness'] == 8


def test_vina_specifications():
    """Test that specifications are defined."""
    method = VinaDocking()

    # Check specs exist
    assert method.input_spec is not None
    assert method.output_spec is not None
    assert method.env_requirements is not None
    assert method.resource_spec is not None

    # Typed ports: required inputs and the unified docking outputs
    assert method.input_spec.required == ['receptor', 'ligands']
    assert method.input_spec.ports['receptor'].data_class is ProteinStructure
    assert method.input_spec.ports['ligands'].data_class is Molecules
    assert method.output_spec.ports['docked_poses'].data_class is Poses
    assert method.output_spec.ports['docking_scores'].data_class is Scores


def test_input_validation_missing_files():
    """Test input validation with missing files."""
    method = VinaDocking()

    # Missing both inputs
    is_valid, errors = method.validate_inputs({})
    assert not is_valid
    assert len(errors) == 2

    # Missing ligands
    is_valid, errors = method.validate_inputs({'receptor': '/path/to/receptor.pdb'})
    assert not is_valid
    assert any('ligands' in err for err in errors)


def test_input_validation_nonexistent_files(tmp_path):
    """Test input validation with non-existent files."""
    method = VinaDocking()

    inputs = {
        'receptor': str(tmp_path / 'receptor.pdb'),
        'ligands': str(tmp_path / 'ligands.sdf')
    }

    is_valid, errors = method.validate_inputs(inputs)
    assert not is_valid
    assert len(errors) > 0


def test_input_validation_invalid_box_params(tmp_path):
    """Test input validation with invalid box parameters."""
    method = VinaDocking(parameters={
        'box_center': [1.0, 2.0],  # Should be 3 values
        'box_size': [20.0, 20.0, 20.0]
    })

    # Create dummy files
    import tempfile
    with tempfile.NamedTemporaryFile(mode='w', suffix='.pdb', delete=False) as f:
        f.write('ATOM      1  CA  ALA A   1       0.000   0.000   0.000  1.00  0.00\n')
        receptor_path = f.name

    with tempfile.NamedTemporaryFile(mode='w', suffix='.sdf', delete=False) as f:
        f.write('lig\n\n\n  0  0  0  0  0  0  0  0  0  0999 V2000\nM  END\n$$$$\n')
        ligands_path = f.name

    try:
        inputs = {
            'receptor': receptor_path,
            'ligands': ligands_path
        }

        normalized = method.format_inputs(inputs)
        is_valid, errors = method.validate_inputs(normalized)
        assert is_valid, errors
        task = Task("invalid_box", method, normalized)
        task.work_dir = tmp_path / "work"
        errors = method.check_dependencies(normalized, RunContext.for_task(task))
        assert any('box_center' in err for err in errors)
    finally:
        Path(receptor_path).unlink()
        Path(ligands_path).unlink()


def test_environment_validation():
    """Test environment validation."""
    method = VinaDocking()

    is_valid, errors = method.validate_environment()

    # In test environment, Vina likely won't be installed
    # Just check that validation runs without crashing
    assert isinstance(is_valid, bool)
    assert isinstance(errors, list)


@patch('rapier.methods.docking.vina.check_executable')
@patch.dict('sys.modules', {'prody': object()})
def test_environment_validation_mocked(mock_check):
    """Test environment validation with mocked executables."""
    method = VinaDocking()

    # Mock that executables are found
    mock_check.return_value = True

    is_valid, errors = method.validate_environment()
    assert is_valid
    assert len(errors) == 0

    # Mock that Vina is missing
    def check_side_effect(name):
        return name != 'vina'

    mock_check.side_effect = check_side_effect

    is_valid, errors = method.validate_environment()
    assert not is_valid
    assert any('Vina' in err for err in errors)


def test_constructor_takes_pipeline_name_and_config():
    """Pipelines build methods as Method(name=<step id>, config=<config>)."""
    method = VinaDocking(name='my_vina', version='1.2', config={
        'box_center': [5.0, 5.0, 5.0],
        'exhaustiveness': 16,
    })

    assert method.name == 'my_vina'
    assert method.version == '1.2'
    assert method.config['box_center'] == [5.0, 5.0, 5.0]
    assert method.config['exhaustiveness'] == 16


def test_parallelizable():
    """Test that VinaDocking is parallelizable."""
    from rapier.methods.base_implementations import ParallelizableMethod

    method = VinaDocking()

    assert isinstance(method, ParallelizableMethod)
    assert hasattr(method, 'can_parallelize')
    assert method.can_parallelize()


def test_default_parameters():
    """Test default docking parameters."""
    method = VinaDocking()

    assert method.config['box_center'] == [0.0, 0.0, 0.0]
    assert method.config['box_size'] == [20.0, 20.0, 20.0]
    assert method.config['exhaustiveness'] == 8
    assert method.config['num_modes'] == 9
    assert method.config['energy_range'] == 3


def test_register_name():
    """Test that register_name is defined for auto-discovery."""
    assert hasattr(VinaDocking, 'register_name')
    assert VinaDocking.register_name == 'vina_docking'


def test_binding_site_input_overrides_deprecated_box_config(tmp_path, sample_data_dir):
    method = VinaDocking(config={'box_center': [1.0, 1.0, 1.0]})
    inputs = method.format_inputs({
        'receptor': str(sample_data_dir / 'receptor.pdb'),
        'ligands': str(sample_data_dir / 'ligands.sdf'),
        'binding_site': {'center': [9.0, 8.0, 7.0], 'size': [10.0, 10.0, 10.0]},
    }, tmp_path)
    assert method._box(inputs)['center'] == [9.0, 8.0, 7.0]
    assert method._box({})['center'] == [1.0, 1.0, 1.0]


def test_invalid_config_rejected_at_construction():
    import pytest
    with pytest.raises(ValueError, match="exhaustiveness"):
        VinaDocking(config={'exhaustiveness': 99})
    with pytest.raises(ValueError, match="Unknown configuration"):
        VinaDocking(config={'not_a_setting': 1})
