"""Tests for Vina docking method."""

import pytest
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock

from rapier.methods.docking.vina import VinaDocking
from rapier.core.task import Task
from rapier.core.enums import TaskStatus


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
    assert method.docking_params['box_center'] == [1.0, 2.0, 3.0]
    assert method.docking_params['exhaustiveness'] == 8


def test_vina_has_steps():
    """Test that Vina is a multi-step method."""
    method = VinaDocking()

    assert hasattr(method, 'steps')
    assert len(method.steps) == 2
    assert method.steps[0].name == 'PrepareReceptor'
    assert method.steps[1].name == 'DockLigands'


def test_vina_specifications():
    """Test that specifications are defined."""
    method = VinaDocking()

    # Check specs exist
    assert method.input_spec is not None
    assert method.output_spec is not None
    assert method.env_requirements is not None
    assert method.resource_spec is not None

    # Check required inputs
    assert 'receptor' in method.input_spec.required_files
    assert 'ligands' in method.input_spec.required_files

    # Check expected outputs
    assert 'docked_poses' in method.output_spec.files
    assert 'docking_scores' in method.output_spec.files


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


def test_input_validation_invalid_box_params():
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
        f.write('$$$$\n')
        ligands_path = f.name

    try:
        inputs = {
            'receptor': receptor_path,
            'ligands': ligands_path
        }

        is_valid, errors = method.validate_inputs(inputs)
        assert not is_valid
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


def test_execute_step_invalid_index():
    """Test execute_step with invalid step index."""
    method = VinaDocking()
    task = Task(task_id='test', method=method, inputs={})

    status = method.execute_step(task, 999)
    assert status == TaskStatus.FAILED
    assert 'Invalid step index' in task.error_message


def test_from_config():
    """Test creating instance from configuration."""
    config = {
        'name': 'my_vina',
        'version': '1.2',
        'parameters': {
            'box_center': [5.0, 5.0, 5.0],
            'exhaustiveness': 16
        }
    }

    method = VinaDocking.from_config(config)

    assert method.name == 'my_vina'
    assert method.version == '1.2'
    assert method.docking_params['box_center'] == [5.0, 5.0, 5.0]
    assert method.docking_params['exhaustiveness'] == 16


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

    assert method.docking_params['box_center'] == [0.0, 0.0, 0.0]
    assert method.docking_params['box_size'] == [20.0, 20.0, 20.0]
    assert method.docking_params['exhaustiveness'] == 8
    assert method.docking_params['num_modes'] == 9
    assert method.docking_params['energy_range'] == 3


def test_register_name():
    """Test that register_name is defined for auto-discovery."""
    assert hasattr(VinaDocking, 'register_name')
    assert VinaDocking.register_name == 'vina_docking'


def test_step_resources():
    """Test that steps have resource specifications."""
    method = VinaDocking()

    prep_step = method.steps[0]
    dock_step = method.steps[1]

    assert 'cpus' in prep_step.resource_spec
    assert 'cpus' in dock_step.resource_spec

    # Docking should need more resources than prep
    assert dock_step.resource_spec['cpus'] >= prep_step.resource_spec['cpus']


@patch('rapier.methods.docking.vina.run_command')
def test_prepare_receptor_step(mock_run, tmp_path):
    """Test receptor preparation step."""
    mock_run.return_value = (0, '', '')  # Success

    method = VinaDocking()

    # Create dummy receptor file
    receptor_pdb = tmp_path / 'receptor.pdb'
    receptor_pdb.write_text('ATOM      1  CA  ALA A   1       0.000   0.000   0.000  1.00  0.00\n')

    task = Task(
        task_id='test',
        method=method,
        inputs={'receptor': str(receptor_pdb)}
    )
    task.work_dir = str(tmp_path / 'work')

    status = method._prepare_receptor(task)

    assert status == TaskStatus.COMPLETED
    assert 'receptor_pdbqt' in task.intermediate_outputs

    # Check that obabel was called
    mock_run.assert_called_once()
    call_args = mock_run.call_args[0][0]
    assert 'obabel' in call_args


def test_dock_ligands_missing_receptor():
    """Test docking step without receptor from previous step."""
    method = VinaDocking()

    task = Task(
        task_id='test',
        method=method,
        inputs={'ligands': '/path/to/ligands.sdf'}
    )

    status = method._dock_ligands(task)

    assert status == TaskStatus.FAILED
    assert 'Receptor PDBQT not found' in task.error_message
