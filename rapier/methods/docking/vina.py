"""AutoDock Vina method and its normalized Rapier contract."""

from typing import Dict, Any, List, Optional, Tuple
from pathlib import Path
import json
import csv
import shlex
import sys

from ...core.task import Task
from ...core.enums import TaskStatus
from ...core.specs import ConfigField, ConfigSpec, InputSpec, OutputSpec, PortSpec, ResourceSpec
from ...artifacts import Artifact
from ..base_implementations import MultiStepMethod, MethodStep, ParallelizableMethod
from ..utils import (
    validate_pdb_file, validate_sdf_file, check_executable,
    run_command, parse_box_spec, parse_vina_output
)
from .vina_runner import run_manifest


class VinaDocking(MultiStepMethod, ParallelizableMethod):
    """Molecular docking using AutoDock Vina.

    The scheduler-facing workflow is implemented by ``vina_runner`` and is
    driven by a JSON manifest generated for each task.  Receptors and ligands
    are prepared with the official Meeko command-line tools, then AutoDock
    Vina is run once per ligand so failures can be reported independently.

    The docking step is parallelizable - large ligand sets can be split
    across multiple SLURM jobs.

    Parameters:
        - box_center: Docking box center [x, y, z] in Ångströms
        - box_size: Docking box size [dx, dy, dz] in Ångströms
        - exhaustiveness: Search exhaustiveness (default: 8, range: 1-32)
        - num_modes: Number of binding modes to generate (default: 9)
        - energy_range: Maximum energy difference for modes (kcal/mol, default: 3)
        - scoring: Vina scoring function (``vina`` or ``vinardo``)
        - cpu: Vina worker count; omitted uses the task's allocated CPUs
        - seed: Optional random seed for reproducible docking
    """

    register_name = "vina_docking"
    contract_version = 2

    def __init__(
        self,
        name: str = "vina_docking",
        version: str = "1.2.5",
        parameters: Optional[Dict[str, Any]] = None
    ):
        super().__init__(name=name, version=version, parameters=parameters)

        self.config_spec = ConfigSpec(fields={
            # Deprecated compatibility fields; use the binding_site input in v2 YAML.
            'box_center': ConfigField(list, default=[0.0, 0.0, 0.0]),
            'box_size': ConfigField(list, default=[20.0, 20.0, 20.0]),
            'exhaustiveness': ConfigField(int, default=8, minimum=1, maximum=32),
            'num_modes': ConfigField(int, default=9, minimum=1),
            'energy_range': ConfigField((int, float), default=3, minimum=0),
            'scoring': ConfigField(str, default='vina', choices=('vina', 'vinardo')),
            'cpu': ConfigField((int, type(None)), default=None),
            'seed': ConfigField((int, type(None)), default=None),
            'min_rmsd': ConfigField((int, float, type(None)), default=None),
            'verbosity': ConfigField(int, default=1, minimum=0, maximum=2),
            'allow_partial_failures': ConfigField(bool, default=True),
        })
        self.docking_params = self.normalize_config(parameters)

        # Define the two steps
        self.steps = [
            MethodStep(
                name='PrepareReceptor',
                description='Prepare receptor PDBQT and Vina search-box config with Meeko',
                input_spec={'receptor': 'PDB/PDBQT structure', 'binding_site': 'box'},
                output_spec={'receptor_pdbqt': 'PDBQT file'},
                resource_spec={'cpus': 1, 'memory': '2GB', 'time': '00:10:00'}
            ),
            MethodStep(
                name='DockLigands',
                description='Prepare SDF ligands with Meeko and dock with AutoDock Vina',
                input_spec={'receptor_pdbqt': 'PDBQT file', 'ligands': 'SDF file'},
                output_spec={'docked_poses': 'SDF file', 'scores': 'CSV file'},
                resource_spec={'cpus': 4, 'memory': '8GB', 'time': '02:00:00'}
            )
        ]

        # Define overall specifications
        self.input_spec = InputSpec(ports={
            'receptor': PortSpec('structure.protein', ('pdb', 'cif', 'pdbqt')),
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
            'software': [
                'vina',
                'mk_prepare_receptor.py',
                'mk_prepare_ligand.py',
                'mk_export.py',
                'python',
            ],
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

        # Check required inputs
        if 'receptor' not in inputs:
            errors.append("Missing required input 'receptor'")
        if 'ligands' not in inputs:
            errors.append("Missing required input 'ligands'")

        if errors:
            return False, errors

        # Validate receptor
        receptor_value = inputs['receptor']
        receptor_path = Path(receptor_value)
        if receptor_path.suffix.lower() in ('.pdb', '.pdbqt'):
            is_valid, error_msg = validate_pdb_file(receptor_path)
            if not is_valid:
                errors.append(error_msg)
        elif receptor_path.suffix.lower() in ('.cif', '.mmcif'):
            if not receptor_path.exists():
                errors.append(f"Structure file not found: {receptor_path}")
            elif receptor_path.stat().st_size == 0:
                errors.append(f"Structure file is empty: {receptor_path}")
        else:
            errors.append(
                f"Unsupported receptor format '{receptor_path.suffix or '<none>'}'; "
                "expected PDB, CIF, or PDBQT"
            )

        # Validate ligands
        ligands_path = Path(inputs['ligands'])
        is_valid, error_msg = validate_sdf_file(ligands_path)
        if not is_valid:
            errors.append(error_msg)

        # Validate legacy box parameters.  A normalized binding_site/box input
        # takes precedence, but these fields remain supported for v1 callers.
        for name in ('box_center', 'box_size'):
            value = self.docking_params.get(name)
            if not isinstance(value, list) or len(value) != 3:
                errors.append(f"{name} must be a list of 3 numbers [x, y, z]")
            elif not all(isinstance(item, (int, float)) and not isinstance(item, bool) for item in value):
                errors.append(f"{name} must contain only numbers")
        if isinstance(self.docking_params.get('box_size'), list) and len(self.docking_params['box_size']) == 3:
            if any(value <= 0 for value in self.docking_params['box_size'] if isinstance(value, (int, float))):
                errors.append("box_size dimensions must be positive")

        if 'binding_site' in inputs:
            try:
                self._box_from_inputs(inputs)
            except (TypeError, ValueError) as exc:
                errors.append(str(exc))

        return len(errors) == 0, errors

    def validate_environment(self) -> Tuple[bool, List[str]]:
        """Validate that required software is available.

        Returns:
            Tuple of (is_valid, error_list)
        """
        errors = []

        required_tools = (
            ('vina', 'AutoDock Vina (vina)'),
            ('mk_prepare_receptor.py', 'Meeko receptor preparation (mk_prepare_receptor.py)'),
            ('mk_prepare_ligand.py', 'Meeko ligand preparation (mk_prepare_ligand.py)'),
            ('mk_export.py', 'Meeko pose export (mk_export.py)'),
            ('python', 'Python'),
        )
        for executable, label in required_tools:
            if not check_executable(executable):
                errors.append(f"{label} not found in PATH")

        # Meeko's receptor preparation imports ProDy at runtime.  It is not
        # guaranteed by the standalone CLI scripts, so report the missing
        # optional module before submitting a docking job.
        try:
            import prody  # noqa: F401
        except ImportError:
            errors.append("ProDy Python package not found (required by Meeko receptor preparation)")

        return len(errors) == 0, errors

    @staticmethod
    def _input_path(value: Any, name: str) -> Path:
        if isinstance(value, Artifact):
            if not value.is_file:
                raise ValueError(f"Input '{name}' must be a file-backed artifact")
            return value.path
        return Path(value)

    def _box_from_inputs(self, inputs: Dict[str, Any]) -> Dict[str, List[float]]:
        site = inputs.get('binding_site')
        if site is None:
            center = self.docking_params['box_center']
            size = self.docking_params['box_size']
        elif isinstance(site, Artifact):
            if site.type != 'binding_site' or site.format != 'box' or site.storage.value != 'inline':
                raise ValueError("Vina requires binding_site as an inline binding_site/box artifact")
            value = site.value
            center = value.get('center') if isinstance(value, dict) else None
            size = value.get('size') if isinstance(value, dict) else None
        elif isinstance(site, dict):
            center = site.get('center')
            size = site.get('size')
        else:
            raise ValueError("Vina requires binding_site with center and size")
        if not isinstance(center, (list, tuple)) or len(center) != 3:
            raise ValueError("binding_site center must contain three numbers")
        if not isinstance(size, (list, tuple)) or len(size) != 3:
            raise ValueError("binding_site size must contain three numbers")
        if not all(isinstance(item, (int, float)) and not isinstance(item, bool) for item in list(center) + list(size)):
            raise ValueError("binding_site center and size must contain only numbers")
        if any(item <= 0 for item in size):
            raise ValueError("binding_site size dimensions must be positive")
        return {'center': [float(item) for item in center], 'size': [float(item) for item in size]}

    def prepare(self, task: Task) -> None:
        """Write the deterministic manifest consumed by the scheduler runner."""
        work_dir = Path(task.work_dir or (Path.cwd() / 'runs' / task.task_id))
        work_dir.mkdir(parents=True, exist_ok=True)
        task.work_dir = work_dir
        receptor = self._input_path(task.inputs['receptor'], 'receptor')
        ligands = self._input_path(task.inputs['ligands'], 'ligands')
        box = self._box_from_inputs(task.inputs)
        config = dict(self.docking_params)
        if config.get('cpu') is None:
            resources = task.metadata.get('resources', {})
            config['cpu'] = resources.get('cpus', self.resource_spec.cpus)
        manifest = {
            'manifest_version': 1,
            'work_dir': str(work_dir),
            'receptor': str(receptor),
            'ligands': str(ligands),
            'box': box,
            'config': config,
        }
        manifest_path = work_dir / 'vina_manifest.json'
        manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True))
        task.intermediate_outputs = task.intermediate_outputs or {}
        task.intermediate_outputs['vina_manifest'] = str(manifest_path)

    def get_command(self, task: Task) -> str:
        """Return the real Vina workflow command submitted to SLURM."""
        self.prepare(task)
        manifest_path = Path(task.intermediate_outputs['vina_manifest']).resolve()
        return shlex.join([sys.executable, '-m', 'rapier.methods.docking.vina_runner', '--manifest', str(manifest_path)])

    def execute(self, task: Task) -> TaskStatus:
        """Run the same manifest workflow used by SLURM, in-process."""
        try:
            self.prepare(task)
            run_manifest(Path(task.intermediate_outputs['vina_manifest']))
            self.collect_outputs(task)
            return TaskStatus.COMPLETED
        except Exception as exc:
            task.error_message = f"Vina docking failed: {exc}"
            return TaskStatus.FAILED

    def collect_outputs(self, task: Task) -> Dict[str, Any]:
        """Collect typed artifacts and preserve per-ligand failure metadata."""
        output_dir = Path(task.work_dir) / 'outputs'
        poses = output_dir / 'docked_poses.sdf'
        scores = output_dir / 'docking_scores.csv'
        summary_path = output_dir / 'vina_run.json'
        if not poses.exists() or not scores.exists():
            raise FileNotFoundError('Vina outputs are incomplete; expected docked_poses.sdf and docking_scores.csv')
        metadata: Dict[str, Any] = {}
        if summary_path.exists():
            summary = json.loads(summary_path.read_text())
            metadata = {
                'failed_ligands': summary.get('failed_ligands', []),
                'successful_ligands': summary.get('successful_ligands', 0),
                'run_summary': str(summary_path),
            }
            task.intermediate_outputs['vina_run_summary'] = str(summary_path)
        task.outputs = {
            'docked_poses': Artifact('molecule.collection', 'sdf', poses, metadata=metadata),
            'docking_scores': Artifact('table', 'csv', scores, metadata=metadata),
        }
        return task.outputs

    def execute_step(self, task: Task, step_index: int) -> TaskStatus:
        """Execute a specific step of the docking workflow.

        This explicit two-step hook is retained for v1 callers.  Normal local
        and SLURM execution goes through :meth:`execute` and the manifest
        runner above, which uses Meeko for all preparation and export.

        Args:
            task: The task to execute
            step_index: Which step to execute (0 = PrepareReceptor, 1 = DockLigands)

        Returns:
            TaskStatus indicating success or failure
        """
        if step_index == 0:
            return self._prepare_receptor(task)
        elif step_index == 1:
            return self._dock_ligands(task)
        else:
            task.error_message = f"Invalid step index: {step_index}"
            return TaskStatus.FAILED

    def _prepare_receptor(self, task: Task) -> TaskStatus:
        """Step 1: Prepare receptor by converting to PDBQT.

        Args:
            task: The task to execute

        Returns:
            TaskStatus indicating success or failure
        """
        try:
            receptor_path = Path(task.inputs['receptor'])
            work_dir = Path(task.work_dir)
            output_dir = work_dir / 'step1_receptor'
            output_dir.mkdir(parents=True, exist_ok=True)

            receptor_pdbqt = output_dir / 'receptor.pdbqt'

            # Use Open Babel to convert PDB to PDBQT
            cmd = [
                'obabel',
                str(receptor_path),
                '-O', str(receptor_pdbqt),
                '-xr',  # Rigid receptor
                '-p', '7.4'  # pH for protonation
            ]

            returncode, stdout, stderr = run_command(cmd, cwd=work_dir)

            if returncode != 0:
                task.error_message = f"Receptor preparation failed: {stderr}"
                return TaskStatus.FAILED

            # Store output for next step
            task.intermediate_outputs = task.intermediate_outputs or {}
            task.intermediate_outputs['receptor_pdbqt'] = str(receptor_pdbqt)

            return TaskStatus.COMPLETED

        except Exception as e:
            task.error_message = f"Receptor preparation failed: {e}"
            return TaskStatus.FAILED

    def _dock_ligands(self, task: Task) -> TaskStatus:
        """Step 2: Dock ligands against prepared receptor.

        Args:
            task: The task to execute

        Returns:
            TaskStatus indicating success or failure
        """
        try:
            self._apply_binding_site(task.inputs)
            # Get receptor from previous step
            if not task.intermediate_outputs or 'receptor_pdbqt' not in task.intermediate_outputs:
                task.error_message = "Receptor PDBQT not found from previous step"
                return TaskStatus.FAILED

            receptor_pdbqt = Path(task.intermediate_outputs['receptor_pdbqt'])
            ligands_path = Path(task.inputs['ligands'])

            work_dir = Path(task.work_dir)
            output_dir = work_dir / 'step2_docking'
            output_dir.mkdir(parents=True, exist_ok=True)

            # Load ligands and convert to PDBQT
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
            docked_mols = []

            for idx, mol in enumerate(ligand_mols):
                mol_name = mol.GetProp('_Name') if mol.HasProp('_Name') else f"ligand_{idx}"

                # Write ligand to temporary SDF
                temp_sdf = output_dir / f"{mol_name}.sdf"
                writer = Chem.SDWriter(str(temp_sdf))
                writer.write(mol)
                writer.close()

                # Convert to PDBQT
                ligand_pdbqt = output_dir / f"{mol_name}.pdbqt"
                cmd = ['obabel', str(temp_sdf), '-O', str(ligand_pdbqt), '-p', '7.4']
                run_command(cmd)

                # Run Vina
                out_pdbqt = output_dir / f"{mol_name}_out.pdbqt"
                vina_cmd = [
                    'vina',
                    '--receptor', str(receptor_pdbqt),
                    '--ligand', str(ligand_pdbqt),
                    '--out', str(out_pdbqt),
                    '--center_x', str(self.docking_params['box_center'][0]),
                    '--center_y', str(self.docking_params['box_center'][1]),
                    '--center_z', str(self.docking_params['box_center'][2]),
                    '--size_x', str(self.docking_params['box_size'][0]),
                    '--size_y', str(self.docking_params['box_size'][1]),
                    '--size_z', str(self.docking_params['box_size'][2]),
                    '--exhaustiveness', str(self.docking_params['exhaustiveness']),
                    '--num_modes', str(self.docking_params['num_modes']),
                    '--energy_range', str(self.docking_params['energy_range'])
                ]

                returncode, stdout, stderr = run_command(vina_cmd, cwd=work_dir)

                if returncode == 0 and out_pdbqt.exists():
                    # Parse results
                    poses = parse_vina_output(stdout)
                    if poses:
                        best_score = poses[0]['affinity']
                        all_scores.append({
                            'ligand': mol_name,
                            'affinity_kcal_mol': best_score
                        })

                        # Convert output back to SDF
                        docked_sdf = output_dir / f"{mol_name}_docked.sdf"
                        run_command(['obabel', str(out_pdbqt), '-O', str(docked_sdf)])

            # Write final outputs
            final_output_dir = work_dir / 'outputs'
            final_output_dir.mkdir(parents=True, exist_ok=True)

            # Write scores CSV
            scores_csv = final_output_dir / 'docking_scores.csv'
            with open(scores_csv, 'w', newline='') as f:
                if all_scores:
                    writer = csv.DictWriter(f, fieldnames=['ligand', 'affinity_kcal_mol'])
                    writer.writeheader()
                    writer.writerows(all_scores)

            # Combine docked poses into one SDF
            docked_sdf = final_output_dir / 'docked_poses.sdf'
            combined_writer = Chem.SDWriter(str(docked_sdf))
            for sdf_file in output_dir.glob("*_docked.sdf"):
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
            task.error_message = f"Docking failed: {e}"
            return TaskStatus.FAILED

    def _apply_binding_site(self, inputs: Dict[str, Any]) -> None:
        site = inputs.get('binding_site')
        if site is None:
            return
        if not isinstance(site, Artifact) or site.format != 'box':
            raise ValueError("Vina currently requires binding_site format 'box'")
        self.docking_params['box_center'] = list(site.value['center'])
        self.docking_params['box_size'] = list(site.value['size'])

    @classmethod
    def from_config(cls, config: Dict[str, Any]) -> 'VinaDocking':
        """Create instance from configuration.

        Args:
            config: Configuration dictionary

        Returns:
            VinaDocking instance
        """
        return cls(
            name=config.get('name', 'vina_docking'),
            version=config.get('version', '1.2.5'),
            parameters=config.get('parameters', {})
        )
