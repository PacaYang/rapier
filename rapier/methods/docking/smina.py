"""Smina docking method.

Smina is a fork of AutoDock Vina with custom scoring functions and
enhanced features for virtual screening.
"""

from typing import Dict, Any, List, Optional, Tuple
from pathlib import Path
import csv

from ...core.task import Task
from ...core.enums import TaskStatus
from ...core.specs import ConfigField, ConfigSpec, InputSpec, OutputSpec, PortSpec, ResourceSpec
from ...artifacts import Artifact
from ..base_implementations import SingleStepMethod, ParallelizableMethod
from ..utils import (
    validate_pdb_file, validate_sdf_file, check_executable,
    run_command, parse_vina_output
)


class SminaDocking(SingleStepMethod, ParallelizableMethod):
    """Molecular docking using Smina.

    Smina is a fork of AutoDock Vina with additional scoring functions
    and better support for virtual screening. Unlike Vina, Smina can
    handle PDB receptors directly (no PDBQT conversion needed).

    This is a single-step method that is parallelizable across ligands.

    Parameters:
        - box_center: Docking box center [x, y, z] in Ångströms
        - box_size: Docking box size [dx, dy, dz] in Ångströms
        - exhaustiveness: Search exhaustiveness (default: 8)
        - num_modes: Number of binding modes (default: 9)
        - scoring: Scoring function ('vina', 'vinardo', 'ad4', default: 'vina')
        - minimize: Energy minimize poses (default: True)
    """

    register_name = "smina_docking"
    contract_version = 2

    def __init__(
        self,
        name: str = "smina_docking",
        version: str = "1.0",
        parameters: Optional[Dict[str, Any]] = None
    ):
        super().__init__(name=name, version=version, parameters=parameters)

        self.config_spec = ConfigSpec(fields={
            'box_center': ConfigField(list, default=[0.0, 0.0, 0.0]),
            'box_size': ConfigField(list, default=[20.0, 20.0, 20.0]),
            'exhaustiveness': ConfigField(int, default=8, minimum=1),
            'num_modes': ConfigField(int, default=9, minimum=1),
            'scoring': ConfigField(str, default='vina', choices=('vina', 'vinardo', 'ad4')),
            'minimize': ConfigField(bool, default=True),
        })
        self.docking_params = self.normalize_config(parameters)

        # Define specifications
        self.input_spec = InputSpec(ports={
            'receptor': PortSpec('structure.protein', ('pdb', 'pdbqt')),
            'ligands': PortSpec('molecule.collection', 'sdf'),
            'binding_site': PortSpec(
                'binding_site', 'box', required=False,
                description='Docking search box',
            ),
        })

        self.output_spec = OutputSpec(ports={
            'docked_poses': PortSpec('molecule.collection', 'sdf'),
            'docking_scores': PortSpec('table', 'csv'),
        })

        self.env_requirements = {
            'software': ['smina', 'obabel', 'python'],
            'modules': [],
            'conda_env': None
        }

        self.resource_spec = ResourceSpec(
            cpus=4,
            memory="8GB",
            time="02:00:00",
            partition='cpu'
        )

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
        if 'ligands' not in inputs:
            errors.append("Missing required input 'ligands'")

        if errors:
            return False, errors

        # Validate receptor (can be PDB or PDBQT)
        receptor_path = Path(inputs['receptor'])
        if not receptor_path.exists():
            errors.append(f"Receptor file not found: {receptor_path}")

        # Validate ligands
        ligands_path = Path(inputs['ligands'])
        is_valid, error_msg = validate_sdf_file(ligands_path)
        if not is_valid:
            errors.append(error_msg)

        return len(errors) == 0, errors

    def validate_environment(self) -> Tuple[bool, List[str]]:
        """Validate that required software is available.

        Returns:
            Tuple of (is_valid, error_list)
        """
        errors = []

        if not check_executable('smina'):
            errors.append("Smina not found in PATH")

        if not check_executable('obabel'):
            errors.append("Open Babel (obabel) not found in PATH")

        return len(errors) == 0, errors

    def execute(self, task: Task) -> TaskStatus:
        """Execute Smina docking.

        Args:
            task: The task to execute

        Returns:
            TaskStatus indicating success or failure
        """
        try:
            self._apply_binding_site(task.inputs)
            # Validate inputs
            is_valid, errors = self.validate_inputs(task.inputs)
            if not is_valid:
                task.error_message = f"Input validation failed: {errors}"
                return TaskStatus.FAILED

            receptor_path = Path(task.inputs['receptor'])
            ligands_path = Path(task.inputs['ligands'])

            work_dir = Path(task.work_dir)
            output_dir = work_dir / 'outputs'
            output_dir.mkdir(parents=True, exist_ok=True)

            temp_dir = work_dir / 'temp'
            temp_dir.mkdir(parents=True, exist_ok=True)

            # Load ligands
            try:
                from rdkit import Chem
            except ImportError:
                task.error_message = "RDKit not available"
                return TaskStatus.FAILED

            suppl = Chem.SDMolSupplier(str(ligands_path))
            ligand_mols = [m for m in suppl if m is not None]

            if not ligand_mols:
                task.error_message = "No valid ligands in input file"
                return TaskStatus.FAILED

            # Dock each ligand
            all_scores = []

            for idx, mol in enumerate(ligand_mols):
                mol_name = mol.GetProp('_Name') if mol.HasProp('_Name') else f"ligand_{idx}"

                # Write ligand
                ligand_sdf = temp_dir / f"{mol_name}.sdf"
                writer = Chem.SDWriter(str(ligand_sdf))
                writer.write(mol)
                writer.close()

                # Run Smina
                out_sdf = temp_dir / f"{mol_name}_out.sdf"

                smina_cmd = [
                    'smina',
                    '--receptor', str(receptor_path),
                    '--ligand', str(ligand_sdf),
                    '--out', str(out_sdf),
                    '--center_x', str(self.docking_params['box_center'][0]),
                    '--center_y', str(self.docking_params['box_center'][1]),
                    '--center_z', str(self.docking_params['box_center'][2]),
                    '--size_x', str(self.docking_params['box_size'][0]),
                    '--size_y', str(self.docking_params['box_size'][1]),
                    '--size_z', str(self.docking_params['box_size'][2]),
                    '--exhaustiveness', str(self.docking_params['exhaustiveness']),
                    '--num_modes', str(self.docking_params['num_modes']),
                    '--scoring', self.docking_params['scoring']
                ]

                if self.docking_params['minimize']:
                    smina_cmd.append('--minimize')

                returncode, stdout, stderr = run_command(smina_cmd, cwd=work_dir)

                if returncode == 0 and out_sdf.exists():
                    # Parse score from output
                    poses = parse_vina_output(stdout)
                    if poses:
                        best_score = poses[0]['affinity']
                        all_scores.append({
                            'ligand': mol_name,
                            'score': best_score,
                            'scoring_function': self.docking_params['scoring']
                        })

            # Write scores
            scores_csv = output_dir / 'docking_scores.csv'
            with open(scores_csv, 'w', newline='') as f:
                if all_scores:
                    writer = csv.DictWriter(f, fieldnames=['ligand', 'score', 'scoring_function'])
                    writer.writeheader()
                    writer.writerows(all_scores)

            # Combine docked poses
            docked_sdf = output_dir / 'docked_poses.sdf'
            combined_writer = Chem.SDWriter(str(docked_sdf))
            for sdf_file in temp_dir.glob("*_out.sdf"):
                mol_suppl = Chem.SDMolSupplier(str(sdf_file))
                for mol in mol_suppl:
                    if mol:
                        combined_writer.write(mol)
            combined_writer.close()

            # Update task outputs
            task.outputs = {
                'docked_poses': Artifact('molecule.collection', 'sdf', docked_sdf),
                'docking_scores': Artifact('table', 'csv', scores_csv),
            }

            return TaskStatus.COMPLETED

        except Exception as e:
            task.error_message = f"Smina docking failed: {e}"
            return TaskStatus.FAILED

    def _apply_binding_site(self, inputs: Dict[str, Any]) -> None:
        site = inputs.get('binding_site')
        if site is None:
            return
        if not isinstance(site, Artifact) or site.format != 'box':
            raise ValueError("Smina currently requires binding_site format 'box'")
        self.docking_params['box_center'] = list(site.value['center'])
        self.docking_params['box_size'] = list(site.value['size'])

    @classmethod
    def from_config(cls, config: Dict[str, Any]) -> 'SminaDocking':
        """Create instance from configuration.

        Args:
            config: Configuration dictionary

        Returns:
            SminaDocking instance
        """
        return cls(
            name=config.get('name', 'smina_docking'),
            version=config.get('version', '1.0'),
            parameters=config.get('parameters', {})
        )
