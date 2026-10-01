"""Ligand preparation method for molecular docking.

This method prepares ligand molecules for docking by generating 3D conformations,
adding hydrogens, and performing energy minimization.
"""

from typing import Dict, Any, List, Optional, Tuple
from pathlib import Path
import json

from ...core.task import Task
from ...core.enums import TaskStatus
from ...core.specs import ConfigField, ConfigSpec, InputSpec, OutputSpec, PortSpec, ResourceSpec
from ...artifacts import Artifact
from ..base_implementations import SingleStepMethod
from ..utils import validate_sdf_file


class LigandPreparation(SingleStepMethod):
    """Prepare ligand molecules for docking.

    This method takes 2D or SMILES structures and prepares them for docking:
    1. Generate 3D conformations
    2. Add hydrogens at physiological pH
    3. Energy minimize structures

    Parameters:
        - ph: pH for protonation (default: 7.4)
        - num_conformers: Number of conformers to generate (default: 1)
        - energy_minimize: Whether to minimize energy (default: True)
        - force_field: Force field for minimization ('UFF', 'MMFF', default: 'UFF')
    """

    register_name = "ligand_prep"
    contract_version = 2

    def __init__(
        self,
        name: str = "ligand_prep",
        version: str = "1.0",
        parameters: Optional[Dict[str, Any]] = None
    ):
        super().__init__(name=name, version=version, parameters=parameters)

        self.config_spec = ConfigSpec(fields={
            'ph': ConfigField((int, float), default=7.4, minimum=0, maximum=14),
            'num_conformers': ConfigField(int, default=1, minimum=1),
            'energy_minimize': ConfigField(bool, default=True),
            'force_field': ConfigField(str, default='UFF', choices=('UFF', 'MMFF')),
        })

        self.input_spec = InputSpec(
            ports={
                'ligands': PortSpec(
                    'molecule.collection', ('smiles', 'sdf'),
                    description='Input molecules',
                )
            }
        )

        # Define output specification
        self.output_spec = OutputSpec(
            ports={
                'prepared_ligands': PortSpec('molecule.collection', 'sdf'),
                'preparation_log': PortSpec('report', 'json'),
            }
        )

        # Define environment requirements
        self.env_requirements = {
            'software': ['python', 'rdkit', 'obabel'],
            'modules': [],
            'conda_env': None
        }

        # Define resource requirements
        self.resource_spec = ResourceSpec(
            cpus=2,
            memory="4GB",
            time="02:00:00",
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

        if 'ligands' not in inputs:
            errors.append("Missing required input 'ligands'")
            return False, errors

        ligands_path = Path(inputs['ligands'])
        if not ligands_path.exists():
            errors.append(f"Ligand file not found: {ligands_path}")
        elif ligands_path.stat().st_size == 0:
            errors.append(f"Ligand file is empty: {ligands_path}")

        return len(errors) == 0, errors

    def validate_environment(self) -> Tuple[bool, List[str]]:
        """Validate that required software is available.

        Returns:
            Tuple of (is_valid, error_list)
        """
        errors = []

        try:
            from rdkit import Chem
            from rdkit.Chem import AllChem
        except ImportError:
            errors.append("RDKit not available (required for ligand preparation)")

        return len(errors) == 0, errors

    def _generate_3d_conformer(self, mol):
        """Generate a 3D conformer for a molecule.

        Args:
            mol: RDKit molecule

        Returns:
            Molecule with 3D coordinates, or None on failure
        """
        from rdkit import Chem
        from rdkit.Chem import AllChem

        # Add hydrogens
        mol = Chem.AddHs(mol)

        # Generate conformer
        params = AllChem.ETKDGv3()
        params.randomSeed = 42  # For reproducibility

        result = AllChem.EmbedMolecule(mol, params)
        if result != 0:
            # Try with random coordinates if ETKDG fails
            result = AllChem.EmbedMolecule(mol, useRandomCoords=True, randomSeed=42)
        if result != 0:
            raise RuntimeError("RDKit failed to generate a 3D conformer")

        return mol

    def _minimize_energy(self, mol):
        """Minimize molecular energy.

        Args:
            mol: RDKit molecule with 3D coordinates

        Returns:
            Minimized molecule
        """
        from rdkit.Chem import AllChem

        if self.prep_params['force_field'] == 'MMFF':
            try:
                AllChem.MMFFOptimizeMolecule(mol)
            except Exception:
                # Fall back to UFF if MMFF fails
                AllChem.UFFOptimizeMolecule(mol)
        else:
            AllChem.UFFOptimizeMolecule(mol)

        return mol

    def execute(self, task: Task) -> TaskStatus:
        """Execute ligand preparation.

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

            # Import RDKit
            try:
                from rdkit import Chem
                from rdkit.Chem import AllChem
            except ImportError:
                task.error_message = "RDKit not available"
                return TaskStatus.FAILED

            # Load molecules
            ligands_path = Path(task.inputs['ligands'])
            if ligands_path.suffix in ['.smi', '.smiles']:
                with open(ligands_path) as f:
                    mols = []
                    for line in f:
                        parts = line.strip().split()
                        if parts:
                            mol = Chem.MolFromSmiles(parts[0])
                            if mol:
                                # Set name if provided
                                if len(parts) > 1:
                                    mol.SetProp('_Name', parts[1])
                                mols.append(mol)
            else:
                suppl = Chem.SDMolSupplier(str(ligands_path))
                mols = [m for m in suppl if m is not None]

            if not mols:
                task.error_message = "No valid molecules in input"
                return TaskStatus.FAILED

            # Prepare each molecule
            prepared = []
            log_data = {
                'total': len(mols),
                'successful': 0,
                'failed': 0,
                'failed_molecules': []
            }

            for idx, mol in enumerate(mols):
                try:
                    # Generate 3D conformer
                    mol_3d = self._generate_3d_conformer(mol)

                    # Energy minimize if requested
                    if self.prep_params['energy_minimize']:
                        mol_3d = self._minimize_energy(mol_3d)

                    prepared.append(mol_3d)
                    log_data['successful'] += 1

                except Exception as e:
                    log_data['failed'] += 1
                    mol_name = mol.GetProp('_Name') if mol.HasProp('_Name') else f"mol_{idx}"
                    log_data['failed_molecules'].append({
                        'name': mol_name,
                        'error': str(e)
                    })

            # Write prepared molecules
            output_dir = Path(task.work_dir) / 'outputs'
            output_dir.mkdir(parents=True, exist_ok=True)

            prepared_path = output_dir / 'prepared_ligands.sdf'
            writer = Chem.SDWriter(str(prepared_path))
            for mol in prepared:
                writer.write(mol)
            writer.close()

            # Write log
            log_path = output_dir / 'prep_log.json'
            with open(log_path, 'w') as f:
                json.dump(log_data, f, indent=2)

            # Update task outputs
            task.outputs = {
                'prepared_ligands': Artifact('molecule.collection', 'sdf', prepared_path),
                'preparation_log': Artifact('report', 'json', log_path),
            }

            return TaskStatus.COMPLETED

        except Exception as e:
            task.error_message = f"Ligand preparation failed: {e}"
            return TaskStatus.FAILED

    @classmethod
    def from_config(cls, config: Dict[str, Any]) -> 'LigandPreparation':
        """Create instance from configuration.

        Args:
            config: Configuration dictionary

        Returns:
            LigandPreparation instance
        """
        return cls(
            name=config.get('name', 'ligand_prep'),
            version=config.get('version', '1.0'),
            parameters=config.get('parameters', {})
        )
