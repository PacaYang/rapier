"""Similarity search method for finding similar molecules.

This method computes molecular fingerprints and searches for molecules
similar to query structures using Tanimoto similarity.
"""

from typing import Dict, Any, List, Optional, Tuple
from pathlib import Path
import json
import csv

from ...core.task import Task
from ...core.enums import TaskStatus
from ...core.specs import ConfigField, ConfigSpec, InputSpec, OutputSpec, PortSpec, ResourceSpec
from ...artifacts import Artifact
from ..base_implementations import SingleStepMethod
from ..utils import validate_sdf_file


class SimilaritySearch(SingleStepMethod):
    """Find molecules similar to query structures.

    This method uses molecular fingerprints (Morgan, MACCS, RDKit, etc.) to
    compute Tanimoto similarity between query molecules and a database.
    Molecules above a similarity threshold are returned ranked by similarity.

    Parameters:
        - fingerprint_type: Type of fingerprint ('morgan', 'maccs', 'rdkit')
        - radius: Radius for Morgan fingerprints (default: 2)
        - n_bits: Number of bits for fingerprints (default: 2048)
        - similarity_threshold: Minimum Tanimoto similarity (0-1, default: 0.7)
        - max_results: Maximum results per query (default: 100)
    """

    register_name = "similarity_search"
    contract_version = 2

    def __init__(
        self,
        name: str = "similarity_search",
        version: str = "1.0",
        parameters: Optional[Dict[str, Any]] = None
    ):
        super().__init__(name=name, version=version, parameters=parameters)

        self.config_spec = ConfigSpec(fields={
            'fingerprint_type': ConfigField(str, default='morgan', choices=('morgan', 'maccs', 'rdkit')),
            'radius': ConfigField(int, default=2, minimum=1),
            'n_bits': ConfigField(int, default=2048, minimum=1),
            'similarity_threshold': ConfigField((int, float), default=0.7, minimum=0, maximum=1),
            'max_results': ConfigField(int, default=100, minimum=1),
        })

        self.input_spec = InputSpec(
            ports={
                'queries': PortSpec(
                    'molecule.collection', ('smiles', 'sdf'),
                    description='Query molecules',
                ),
                'database': PortSpec(
                    'molecule.collection', 'sdf',
                    description='Molecule database',
                ),
            }
        )

        # Define output specification
        self.output_spec = OutputSpec(
            ports={
                'similar_molecules': PortSpec('molecule.collection', 'sdf'),
                'similarity_scores': PortSpec('table', 'csv'),
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
            cpus=4,
            memory="8GB",
            time="02:00:00",
            partition='cpu'
        )

        # Set search parameters
        self.search_params = self.normalize_config(parameters)

    def validate_inputs(self, inputs: Dict[str, Any]) -> Tuple[bool, List[str]]:
        """Validate input files.

        Args:
            inputs: Input file paths

        Returns:
            Tuple of (is_valid, error_list)
        """
        errors = []

        # Check required inputs
        if 'queries' not in inputs:
            errors.append("Missing required input 'queries'")
        if 'database' not in inputs:
            errors.append("Missing required input 'database'")

        if errors:
            return False, errors

        # Validate database file
        database_path = Path(inputs['database'])
        is_valid, error_msg = validate_sdf_file(database_path)
        if not is_valid:
            errors.append(error_msg)

        # Validate query file (can be SMILES or SDF)
        query_path = Path(inputs['queries'])
        if not query_path.exists():
            errors.append(f"Query file not found: {query_path}")
        elif query_path.stat().st_size == 0:
            errors.append(f"Query file is empty: {query_path}")

        return len(errors) == 0, errors

    def validate_environment(self) -> Tuple[bool, List[str]]:
        """Validate that required software is available.

        Returns:
            Tuple of (is_valid, error_list)
        """
        errors = []

        try:
            from rdkit import Chem
            from rdkit.Chem import AllChem, MACCSkeys, DataStructs
        except ImportError:
            errors.append("RDKit not available (required for similarity search)")

        return len(errors) == 0, errors

    def _compute_fingerprint(self, mol, fp_type: str):
        """Compute fingerprint for a molecule.

        Args:
            mol: RDKit molecule
            fp_type: Fingerprint type

        Returns:
            Fingerprint object
        """
        from rdkit.Chem import AllChem, MACCSkeys, Chem

        if fp_type == 'morgan':
            return AllChem.GetMorganFingerprintAsBitVect(
                mol,
                radius=self.search_params['radius'],
                nBits=self.search_params['n_bits']
            )
        elif fp_type == 'maccs':
            return MACCSkeys.GenMACCSKeys(mol)
        elif fp_type == 'rdkit':
            return Chem.RDKFingerprint(mol, fpSize=self.search_params['n_bits'])
        else:
            raise ValueError(f"Unknown fingerprint type: {fp_type}")

    def execute(self, task: Task) -> TaskStatus:
        """Execute similarity search.

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
                from rdkit.Chem import DataStructs
            except ImportError:
                task.error_message = "RDKit not available"
                return TaskStatus.FAILED

            # Load query molecules
            query_path = Path(task.inputs['queries'])
            if query_path.suffix in ['.smi', '.smiles']:
                with open(query_path) as f:
                    query_mols = [Chem.MolFromSmiles(line.split()[0]) for line in f]
                query_mols = [m for m in query_mols if m is not None]
            else:
                suppl = Chem.SDMolSupplier(str(query_path))
                query_mols = [m for m in suppl if m is not None]

            if not query_mols:
                task.error_message = "No valid query molecules"
                return TaskStatus.FAILED

            # Compute query fingerprints
            query_fps = [
                self._compute_fingerprint(mol, self.search_params['fingerprint_type'])
                for mol in query_mols
            ]

            # Load database and search
            database_path = Path(task.inputs['database'])
            db_suppl = Chem.SDMolSupplier(str(database_path))

            results = []
            for db_idx, db_mol in enumerate(db_suppl):
                if db_mol is None:
                    continue

                db_fp = self._compute_fingerprint(
                    db_mol,
                    self.search_params['fingerprint_type']
                )

                # Compare with all queries
                for query_idx, query_fp in enumerate(query_fps):
                    similarity = DataStructs.TanimotoSimilarity(query_fp, db_fp)

                    if similarity >= self.search_params['similarity_threshold']:
                        results.append({
                            'query_idx': query_idx,
                            'db_idx': db_idx,
                            'similarity': similarity,
                            'mol': db_mol
                        })

            # Sort by similarity
            results.sort(key=lambda x: x['similarity'], reverse=True)

            # Limit results
            if self.search_params['max_results']:
                results = results[:self.search_params['max_results']]

            # Write output files
            output_dir = Path(task.work_dir) / 'outputs'
            output_dir.mkdir(parents=True, exist_ok=True)

            # Write similar molecules to SDF
            similar_path = output_dir / 'similar.sdf'
            writer = Chem.SDWriter(str(similar_path))
            for result in results:
                mol = result['mol']
                mol.SetProp('query_idx', str(result['query_idx']))
                mol.SetProp('similarity', f"{result['similarity']:.3f}")
                writer.write(mol)
            writer.close()

            # Write similarity scores to CSV
            scores_path = output_dir / 'similarity_scores.csv'
            with open(scores_path, 'w', newline='') as f:
                writer = csv.writer(f)
                writer.writerow(['query_idx', 'db_idx', 'similarity'])
                for result in results:
                    writer.writerow([
                        result['query_idx'],
                        result['db_idx'],
                        f"{result['similarity']:.4f}"
                    ])

            # Update task outputs
            task.outputs = {
                'similar_molecules': Artifact('molecule.collection', 'sdf', similar_path),
                'similarity_scores': Artifact('table', 'csv', scores_path),
            }

            return TaskStatus.COMPLETED

        except Exception as e:
            task.error_message = f"Similarity search failed: {e}"
            return TaskStatus.FAILED

    @classmethod
    def from_config(cls, config: Dict[str, Any]) -> 'SimilaritySearch':
        """Create instance from configuration.

        Args:
            config: Configuration dictionary

        Returns:
            SimilaritySearch instance
        """
        return cls(
            name=config.get('name', 'similarity_search'),
            version=config.get('version', '1.0'),
            parameters=config.get('parameters', {})
        )
