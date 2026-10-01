"""Ligand filtering method for property-based molecular screening.

This method filters molecules based on drug-likeness criteria such as
Lipinski's Rule of Five and other physicochemical properties.
"""

from typing import Dict, Any, List, Optional, Tuple
from pathlib import Path
import json

from ...core.task import Task
from ...core.enums import TaskStatus
from ...core.specs import ConfigField, ConfigSpec, InputSpec, OutputSpec, PortSpec, ResourceSpec
from ...artifacts import Artifact
from ..base_implementations import SingleStepMethod
from ..utils import validate_sdf_file, load_molecules_from_sdf, write_molecules_to_sdf


class LigandFilter(SingleStepMethod):
    """Filter molecules by physicochemical properties.

    This method applies drug-likeness filters based on Lipinski's Rule of Five
    and other common criteria. Molecules that pass all filters are written to
    the output file.

    Filters available:
        - mw_min, mw_max: Molecular weight bounds (Da)
        - logp_min, logp_max: LogP (lipophilicity) bounds
        - hba_max: Hydrogen bond acceptors maximum
        - hbd_max: Hydrogen bond donors maximum
        - tpsa_max: Topological polar surface area maximum (Ų)
        - rotatable_bonds_max: Maximum rotatable bonds

    Default Lipinski's Rule of Five:
        - MW ≤ 500 Da
        - LogP ≤ 5
        - HBA ≤ 10
        - HBD ≤ 5
    """

    register_name = "ligand_filter"
    contract_version = 2

    def __init__(
        self,
        name: str = "ligand_filter",
        version: str = "1.0",
        parameters: Optional[Dict[str, Any]] = None
    ):
        super().__init__(name=name, version=version, parameters=parameters)

        self.config_spec = ConfigSpec(fields={
            'mw_min': ConfigField((int, float), default=0),
            'mw_max': ConfigField((int, float), default=500),
            'logp_min': ConfigField((int, float), default=-5),
            'logp_max': ConfigField((int, float), default=5),
            'hba_max': ConfigField(int, default=10, minimum=0),
            'hbd_max': ConfigField(int, default=5, minimum=0),
            'tpsa_max': ConfigField((int, float), default=140, minimum=0),
            'rotatable_bonds_max': ConfigField(int, default=10, minimum=0),
        })

        self.input_spec = InputSpec(
            ports={
                'ligands': PortSpec(
                    'molecule.collection', 'sdf',
                    description='Molecules to filter',
                )
            }
        )

        # Define output specification
        self.output_spec = OutputSpec(
            ports={
                'filtered_ligands': PortSpec(
                    'molecule.collection', 'sdf',
                    description='Molecules passing the filters',
                ),
                'statistics': PortSpec('report', 'json', description='Filtering statistics'),
            }
        )

        # Define environment requirements
        self.env_requirements = {
            'software': ['python', 'rdkit'],
            'modules': [],
            'conda_env': None
        }

        # Define resource requirements
        self.resource_spec = ResourceSpec(
            cpus=1,
            memory="4GB",
            time="01:00:00",
            partition='cpu'
        )

        # Set default filter parameters
        self.filter_params = self.normalize_config(parameters)
        if self.filter_params['mw_min'] > self.filter_params['mw_max']:
            raise ValueError("mw_min cannot be greater than mw_max")
        if self.filter_params['logp_min'] > self.filter_params['logp_max']:
            raise ValueError("logp_min cannot be greater than logp_max")

    def validate_inputs(self, inputs: Dict[str, Any]) -> Tuple[bool, List[str]]:
        """Validate that input files exist and are valid.

        Args:
            inputs: Input file paths

        Returns:
            Tuple of (is_valid, error_list)
        """
        errors = []

        # Check required input
        if 'ligands' not in inputs:
            errors.append("Missing required input 'ligands'")
            return False, errors

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

        # Check Python
        try:
            import sys
            if sys.version_info < (3, 7):
                errors.append("Python 3.7+ required")
        except Exception as e:
            errors.append(f"Python check failed: {e}")

        # Check RDKit (note: may not be installed in test environment)
        try:
            import rdkit
            from rdkit import Chem
            from rdkit.Chem import Descriptors
        except ImportError:
            errors.append("RDKit not available (required for molecular filtering)")

        return len(errors) == 0, errors

    def execute(self, task: Task) -> TaskStatus:
        """Execute the ligand filtering.

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
                from rdkit.Chem import Descriptors, Lipinski
            except ImportError:
                task.error_message = "RDKit not available"
                return TaskStatus.FAILED

            # Load molecules
            ligands_path = Path(task.inputs['ligands'])
            suppl = Chem.SDMolSupplier(str(ligands_path))

            # Filter molecules
            passed = []
            stats = {
                'total': 0,
                'passed': 0,
                'failed': 0,
                'failed_by_reason': {}
            }

            for mol in suppl:
                stats['total'] += 1

                if mol is None:
                    stats['failed'] += 1
                    continue

                # Calculate properties
                mw = Descriptors.MolWt(mol)
                logp = Descriptors.MolLogP(mol)
                hba = Descriptors.NOCount(mol)
                hbd = Descriptors.NHOHCount(mol)
                tpsa = Descriptors.TPSA(mol)
                rot_bonds = Descriptors.NumRotatableBonds(mol)

                # Apply filters
                reasons = []
                if mw < self.filter_params['mw_min'] or mw > self.filter_params['mw_max']:
                    reasons.append('molecular_weight')
                if logp < self.filter_params['logp_min'] or logp > self.filter_params['logp_max']:
                    reasons.append('logp')
                if hba > self.filter_params['hba_max']:
                    reasons.append('hba')
                if hbd > self.filter_params['hbd_max']:
                    reasons.append('hbd')
                if tpsa > self.filter_params['tpsa_max']:
                    reasons.append('tpsa')
                if rot_bonds > self.filter_params['rotatable_bonds_max']:
                    reasons.append('rotatable_bonds')

                if reasons:
                    stats['failed'] += 1
                    for reason in reasons:
                        stats['failed_by_reason'][reason] = stats['failed_by_reason'].get(reason, 0) + 1
                else:
                    stats['passed'] += 1
                    passed.append(mol)

            # Write filtered molecules
            output_dir = Path(task.work_dir) / 'outputs'
            output_dir.mkdir(parents=True, exist_ok=True)

            filtered_path = output_dir / 'filtered.sdf'
            writer = Chem.SDWriter(str(filtered_path))
            for mol in passed:
                writer.write(mol)
            writer.close()

            # Write statistics
            stats_path = output_dir / 'filter_stats.json'
            with open(stats_path, 'w') as f:
                json.dump(stats, f, indent=2)

            # Update task outputs
            task.outputs = {
                'filtered_ligands': Artifact('molecule.collection', 'sdf', filtered_path),
                'statistics': Artifact('report', 'json', stats_path),
            }

            return TaskStatus.COMPLETED

        except Exception as e:
            task.error_message = f"Filter execution failed: {e}"
            return TaskStatus.FAILED

    @classmethod
    def from_config(cls, config: Dict[str, Any]) -> 'LigandFilter':
        """Create a LigandFilter instance from configuration.

        Args:
            config: Configuration dictionary

        Returns:
            LigandFilter instance
        """
        return cls(
            name=config.get('name', 'ligand_filter'),
            version=config.get('version', '1.0'),
            parameters=config.get('parameters', {})
        )
