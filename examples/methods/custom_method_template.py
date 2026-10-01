"""Custom method template.

This is a template for creating new screening methods in Rapier.
Copy this file and modify it to implement your own method.

To use this template:
1. Copy this file to a new file with your method name
2. Rename the class and update the register_name
3. Implement the required methods
4. Define input/output specifications
5. Test your method
6. Register with the platform
"""

from typing import Dict, Any, List, Optional, Tuple
from pathlib import Path
import json

from rapier.core.task import Task
from rapier.core.enums import TaskStatus
from rapier import Artifact
from rapier.core.specs import (
    ConfigField, ConfigSpec, InputSpec, OutputSpec, PortSpec, ResourceSpec,
)
from rapier.methods.base_implementations import SingleStepMethod  # or MultiStepMethod


class MyCustomMethod(SingleStepMethod):
    """Brief description of what your method does.

    Longer description explaining the algorithm, its purpose,
    and how it fits into screening workflows.

    Parameters:
        - param1: Description of parameter 1 (type, default, valid range)
        - param2: Description of parameter 2
        - param3: Description of parameter 3
    """

    # This name is used for auto-discovery and config files
    register_name = "my_custom_method"
    contract_version = 2

    def __init__(
        self,
        name: str = "my_custom_method",
        version: str = "1.0",
        parameters: Optional[Dict[str, Any]] = None
    ):
        """Initialize the method.

        Args:
            name: Method instance name
            version: Method version
            parameters: Method-specific parameters
        """
        super().__init__(name=name, version=version, parameters=parameters)

        self.config_spec = ConfigSpec(fields={
            'param1': ConfigField(str, default='default_value'),
            'param2': ConfigField(int, default=42, minimum=0),
            'param3': ConfigField(bool, default=True),
        })
        self.my_params = self.normalize_config(parameters)

        # Define input specification
        self.input_spec = InputSpec(ports={
            'input_molecules': PortSpec(
                'molecule.collection', 'sdf', description='Molecules to process'
            ),
            'reference': PortSpec(
                'structure.protein', ('pdb', 'cif'), required=False,
                description='Optional reference structure',
            ),
        })

        # Define output specification
        self.output_spec = OutputSpec(ports={
            'results': PortSpec('molecule.collection', 'sdf'),
            'scores': PortSpec('table', 'csv'),
            'log': PortSpec('report', 'json'),
        })

        # Define environment requirements
        # List all software dependencies
        self.env_requirements = {
            'software': ['python', 'rdkit'],  # Executables that must be in PATH
            'modules': [],  # SLURM modules to load (e.g., ['conda/4.10'])
            'conda_env': None  # Conda environment name (if needed)
        }

        # Define resource requirements
        # Specify SLURM resources needed
        self.resource_spec = ResourceSpec(
            cpus=4,  # Number of CPU cores
            memory="8GB",
            time="01:00:00",
            partition='cpu',  # SLURM partition ('cpu', 'gpu', etc.)
            gpus=0  # Number of GPUs (if needed)
        )

    def validate_inputs(self, inputs: Dict[str, Any]) -> Tuple[bool, List[str]]:
        """Validate that input files exist and are valid.

        Args:
            inputs: Dictionary mapping input names to file paths

        Returns:
            Tuple of (is_valid, error_list)
        """
        errors = []

        # Check required inputs are present
        if 'input_molecules' not in inputs:
            errors.append("Missing required input 'input_molecules'")
            return False, errors

        # Validate file existence and format
        input_path = Path(inputs['input_molecules'])
        if not input_path.exists():
            errors.append(f"Input file not found: {input_path}")
        elif input_path.stat().st_size == 0:
            errors.append(f"Input file is empty: {input_path}")

        # Add any format-specific validation
        # For example, check if SDF file has molecules:
        # from rapier.methods.utils import validate_sdf_file
        # is_valid, error_msg = validate_sdf_file(input_path)
        # if not is_valid:
        #     errors.append(error_msg)

        return len(errors) == 0, errors

    def validate_environment(self) -> Tuple[bool, List[str]]:
        """Validate that required software is available.

        Returns:
            Tuple of (is_valid, error_list)
        """
        errors = []

        # Check for required executables
        from rapier.methods.utils import check_executable

        if not check_executable('python'):
            errors.append("Python not found in PATH")

        # Check for required Python packages
        try:
            import rdkit
        except ImportError:
            errors.append("RDKit not available")

        # Add any other environment checks

        return len(errors) == 0, errors

    def execute(self, task: Task) -> TaskStatus:
        """Execute the method logic.

        This is where you implement your actual algorithm.

        Args:
            task: The task to execute, containing:
                  - task.inputs: Dict of input file paths
                  - task.work_dir: Working directory for this task
                  - task.outputs: Dict to populate with output paths

        Returns:
            TaskStatus.COMPLETED on success, TaskStatus.FAILED on failure
        """
        try:
            # 1. Validate inputs
            is_valid, errors = self.validate_inputs(task.inputs)
            if not is_valid:
                task.error_message = f"Input validation failed: {errors}"
                return TaskStatus.FAILED

            # 2. Set up directories
            work_dir = Path(task.work_dir)
            output_dir = work_dir / 'outputs'
            output_dir.mkdir(parents=True, exist_ok=True)

            temp_dir = work_dir / 'temp'
            temp_dir.mkdir(parents=True, exist_ok=True)

            # 3. Load input data
            input_path = Path(task.inputs['input_molecules'])

            # Example: Load molecules with RDKit
            try:
                from rdkit import Chem
            except ImportError:
                task.error_message = "RDKit not available"
                return TaskStatus.FAILED

            suppl = Chem.SDMolSupplier(str(input_path))
            molecules = [m for m in suppl if m is not None]

            if not molecules:
                task.error_message = "No valid molecules in input"
                return TaskStatus.FAILED

            # 4. Process data (YOUR ALGORITHM GOES HERE)
            results = []
            scores = []

            for idx, mol in enumerate(molecules):
                # Example processing
                mol_name = mol.GetProp('_Name') if mol.HasProp('_Name') else f"mol_{idx}"

                # Do your computation here
                # result = your_algorithm(mol, self.my_params)

                # Store results
                # results.append(processed_mol)
                # scores.append({'name': mol_name, 'score': result})
                pass

            # 5. Write output files
            results_path = output_dir / 'results.sdf'
            # Write results (example)
            # writer = Chem.SDWriter(str(results_path))
            # for mol in results:
            #     writer.write(mol)
            # writer.close()

            scores_path = output_dir / 'scores.csv'
            # Write scores (example)
            # import csv
            # with open(scores_path, 'w', newline='') as f:
            #     writer = csv.DictWriter(f, fieldnames=['name', 'score'])
            #     writer.writeheader()
            #     writer.writerows(scores)

            log_path = output_dir / 'processing_log.json'
            log_data = {
                'total_molecules': len(molecules),
                'processed': len(results),
                'parameters': self.my_params
            }
            with open(log_path, 'w') as f:
                json.dump(log_data, f, indent=2)

            # 6. Update task outputs
            task.outputs = {
                'results': Artifact('molecule.collection', 'sdf', results_path),
                'scores': Artifact('table', 'csv', scores_path),
                'log': Artifact('report', 'json', log_path),
            }

            return TaskStatus.COMPLETED

        except Exception as e:
            task.error_message = f"Method execution failed: {e}"
            return TaskStatus.FAILED

    @classmethod
    def from_config(cls, config: Dict[str, Any]) -> 'MyCustomMethod':
        """Create instance from configuration dictionary.

        This allows your method to be instantiated from pipeline configs.

        Args:
            config: Configuration dictionary with keys:
                    - name: Method instance name
                    - version: Method version
                    - parameters: Method parameters

        Returns:
            Instance of your method
        """
        return cls(
            name=config.get('name', 'my_custom_method'),
            version=config.get('version', '1.0'),
            parameters=config.get('parameters', {})
        )


# Example usage:
if __name__ == '__main__':
    # Create method instance
    method = MyCustomMethod(
        name='test_method',
        version='1.0',
        parameters={'param1': 'value1', 'param2': 100}
    )

    # Check environment
    is_valid, errors = method.validate_environment()
    if not is_valid:
        print(f"Environment validation failed: {errors}")
    else:
        print("Environment is valid")

    # Create a test task (normally done by the pipeline engine)
    from rapier.core.task import Task

    task = Task(
        task_id='test_task',
        method=method,
        inputs={'input_molecules': '/path/to/molecules.sdf'}
    )

    # Validate inputs
    is_valid, errors = method.validate_inputs(task.inputs)
    if not is_valid:
        print(f"Input validation failed: {errors}")
    else:
        print("Inputs are valid")

    # Execute (would normally be done via SLURM)
    # status = method.execute(task)
    # print(f"Execution status: {status}")
