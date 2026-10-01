"""Protein preparation method for molecular docking.

This method prepares protein receptor structures for docking by fixing
structural issues, adding hydrogens, and assigning charges.
"""

from typing import Dict, Any, List, Optional, Tuple
from pathlib import Path
import json

from ...core.task import Task
from ...core.enums import TaskStatus
from ...core.specs import ConfigField, ConfigSpec, InputSpec, OutputSpec, PortSpec, ResourceSpec
from ...artifacts import Artifact
from ..base_implementations import SingleStepMethod
from ..utils import validate_pdb_file, check_executable, run_command


class ProteinPreparation(SingleStepMethod):
    """Prepare protein receptor structures for docking.

    This method prepares proteins by:
    1. Fixing structural issues (missing atoms, alternate conformations)
    2. Adding hydrogens at specified pH
    3. Assigning partial charges
    4. Converting to PDBQT format (if requested)

    Parameters:
        - ph: pH for protonation (default: 7.4)
        - remove_waters: Remove water molecules (default: True)
        - remove_hetero: Remove heteroatoms except water (default: False)
        - add_charges: Add Gasteiger charges (default: True)
        - output_format: Output format ('pdb', 'pdbqt', default: 'pdbqt')
    """

    register_name = "protein_prep"
    contract_version = 2

    def __init__(
        self,
        name: str = "protein_prep",
        version: str = "1.0",
        parameters: Optional[Dict[str, Any]] = None
    ):
        super().__init__(name=name, version=version, parameters=parameters)

        self.config_spec = ConfigSpec(fields={
            'ph': ConfigField((int, float), default=7.4, minimum=0, maximum=14),
            'remove_waters': ConfigField(bool, default=True),
            'remove_hetero': ConfigField(bool, default=False),
            'add_charges': ConfigField(bool, default=True),
            'output_format': ConfigField(str, default='pdbqt', choices=('pdb', 'pdbqt')),
        })

        self.input_spec = InputSpec(
            ports={
                'receptor': PortSpec(
                    'structure.protein', 'pdb',
                    description='Protein structure; CIF is converted when an adapter is available',
                )
            }
        )

        # Define output specification
        self.output_spec = OutputSpec(
            ports={
                'prepared_receptor': PortSpec('structure.protein', ('pdb', 'pdbqt')),
                'preparation_log': PortSpec('report', 'json'),
            }
        )

        # Define environment requirements
        self.env_requirements = {
            'software': ['python', 'obabel', 'pdb2pqr'],
            'modules': [],
            'conda_env': None
        }

        # Define resource requirements
        self.resource_spec = ResourceSpec(
            cpus=1,
            memory="4GB",
            time="00:30:00",
            partition='cpu'
        )

        # Set preparation parameters
        self.prep_params = self.normalize_config(parameters)

    def validate_inputs(self, inputs: Dict[str, Any]) -> Tuple[bool, List[str]]:
        """Validate input files.

        Args:
            inputs: Input file paths

        Returns:
            Tuple of (is_valid, error_list)
        """
        errors = []

        if 'receptor' not in inputs:
            errors.append("Missing required input 'receptor'")
            return False, errors

        receptor_path = Path(inputs['receptor'])
        is_valid, error_msg = validate_pdb_file(receptor_path)
        if not is_valid:
            errors.append(error_msg)

        return len(errors) == 0, errors

    def validate_environment(self) -> Tuple[bool, List[str]]:
        """Validate that required software is available.

        Returns:
            Tuple of (is_valid, error_list)
        """
        errors = []

        # Check for Open Babel
        if not check_executable('obabel'):
            errors.append("Open Babel (obabel) not found")

        return len(errors) == 0, errors

    def _fix_structure(self, pdb_path: Path, output_path: Path) -> Tuple[bool, str]:
        """Fix structural issues in PDB file.

        This uses Open Babel to fix common issues like missing atoms.

        Args:
            pdb_path: Input PDB path
            output_path: Output PDB path

        Returns:
            Tuple of (success, message)
        """
        cmd = ['obabel', str(pdb_path), '-O', str(output_path), '-h']

        returncode, stdout, stderr = run_command(cmd)

        if returncode != 0:
            return False, f"Structure fixing failed: {stderr}"

        return True, "Structure fixed successfully"

    def _remove_waters_and_hetero(self, pdb_path: Path, output_path: Path) -> None:
        """Remove water molecules and heteroatoms from PDB.

        Args:
            pdb_path: Input PDB path
            output_path: Output PDB path
        """
        with open(pdb_path) as f_in, open(output_path, 'w') as f_out:
            for line in f_in:
                # Skip water molecules
                if self.prep_params['remove_waters'] and 'HOH' in line:
                    continue

                # Skip heteroatoms (except keep if not removing)
                if self.prep_params['remove_hetero'] and line.startswith('HETATM'):
                    continue

                f_out.write(line)

    def _convert_to_pdbqt(self, pdb_path: Path, pdbqt_path: Path) -> Tuple[bool, str]:
        """Convert PDB to PDBQT format with charges.

        Args:
            pdb_path: Input PDB path
            pdbqt_path: Output PDBQT path

        Returns:
            Tuple of (success, message)
        """
        # Use Open Babel to convert and add Gasteiger charges
        cmd = [
            'obabel',
            str(pdb_path),
            '-O', str(pdbqt_path),
            '-xr'  # Rigid molecule (for receptor)
        ]

        if self.prep_params['add_charges']:
            cmd.extend(['-p', str(self.prep_params['ph'])])

        returncode, stdout, stderr = run_command(cmd)

        if returncode != 0:
            return False, f"PDBQT conversion failed: {stderr}"

        return True, "Converted to PDBQT successfully"

    def execute(self, task: Task) -> TaskStatus:
        """Execute protein preparation.

        Args:
            task: The task to execute

        Returns:
            TaskStatus indicating success or failure
        """
        try:
            # Validate inputs
            is_valid, errors = self.validate_inputs(task.inputs)
            if not is_valid:
                task.error_message = f"Input validation failed: {errors}"
                return TaskStatus.FAILED

            # Create working directory
            work_dir = Path(task.work_dir)
            temp_dir = work_dir / 'temp'
            temp_dir.mkdir(parents=True, exist_ok=True)

            output_dir = work_dir / 'outputs'
            output_dir.mkdir(parents=True, exist_ok=True)

            # Track preparation steps
            log_data = {
                'steps': [],
                'warnings': []
            }

            receptor_path = Path(task.inputs['receptor'])
            current_path = receptor_path

            # Step 1: Remove waters/heteroatoms if requested
            if self.prep_params['remove_waters'] or self.prep_params['remove_hetero']:
                cleaned_path = temp_dir / 'cleaned.pdb'
                self._remove_waters_and_hetero(current_path, cleaned_path)
                current_path = cleaned_path
                log_data['steps'].append('Removed waters/heteroatoms')

            # Step 2: Fix structure
            fixed_path = temp_dir / 'fixed.pdb'
            success, message = self._fix_structure(current_path, fixed_path)
            if not success:
                task.error_message = message
                return TaskStatus.FAILED
            current_path = fixed_path
            log_data['steps'].append('Fixed structure and added hydrogens')

            # Step 3: Convert to final format
            if self.prep_params['output_format'] == 'pdbqt':
                final_path = output_dir / 'prepared_receptor.pdbqt'
                success, message = self._convert_to_pdbqt(current_path, final_path)
                if not success:
                    task.error_message = message
                    return TaskStatus.FAILED
                log_data['steps'].append('Converted to PDBQT with charges')
            else:
                # Just copy the PDB
                final_path = output_dir / 'prepared_receptor.pdb'
                import shutil
                shutil.copy(current_path, final_path)
                log_data['steps'].append('Output as PDB')

            # Write log
            log_path = output_dir / 'prep_log.json'
            with open(log_path, 'w') as f:
                json.dump(log_data, f, indent=2)

            # Update task outputs
            task.outputs = {
                'prepared_receptor': Artifact(
                    'structure.protein', self.prep_params['output_format'], final_path
                ),
                'preparation_log': Artifact('report', 'json', log_path),
            }

            return TaskStatus.COMPLETED

        except Exception as e:
            task.error_message = f"Protein preparation failed: {e}"
            return TaskStatus.FAILED

    @classmethod
    def from_config(cls, config: Dict[str, Any]) -> 'ProteinPreparation':
        """Create instance from configuration.

        Args:
            config: Configuration dictionary

        Returns:
            ProteinPreparation instance
        """
        return cls(
            name=config.get('name', 'protein_prep'),
            version=config.get('version', '1.0'),
            parameters=config.get('parameters', {})
        )
