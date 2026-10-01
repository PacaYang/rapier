# Method Development Guide

This guide explains how to develop custom screening methods for the Rapier platform.

## Table of Contents

1. [Overview](#overview)
2. [Method Lifecycle](#method-lifecycle)
3. [Base Classes](#base-classes)
4. [Creating a Simple Method](#creating-a-simple-method)
5. [Input/Output Specifications](#inputoutput-specifications)
6. [Environment Requirements](#environment-requirements)
7. [Resource Specifications](#resource-specifications)
8. [Multi-Step Methods](#multi-step-methods)
9. [Parallelizable Methods](#parallelizable-methods)
10. [Testing Your Method](#testing-your-method)
11. [Registering Your Method](#registering-your-method)

## Overview

A **method** in Rapier represents a screening algorithm or computational workflow. Methods process molecular data through a well-defined interface that specifies:

- **Inputs**: What files and data the method needs
- **Outputs**: What files and data the method produces
- **Environment**: What software dependencies are required
- **Resources**: What computational resources (CPU, memory, time) are needed

The platform handles execution scheduling, dependency management, and result tracking—you just implement the algorithm logic.

## Method Lifecycle

Every method follows this lifecycle:

1. **Instantiation**: Method is created from configuration
2. **Input Validation**: Check that input files exist and are valid
3. **Environment Validation**: Check that required software is available
4. **Preparation**: Set up working directories and temporary files
5. **Execution**: Run the algorithm on SLURM
6. **Collection**: Gather and validate output files
7. **Cleanup**: Remove temporary files (optional)

Your method must implement hooks for steps 2, 3, and 5. The platform handles the rest.

## Base Classes

Rapier provides several base classes for different method patterns:

### SingleStepMethod

For methods that execute in one step (most common):

```python
from rapier.methods.base_implementations import SingleStepMethod

class MyMethod(SingleStepMethod):
    register_name = "my_method"
    
    def execute(self, task: Task) -> TaskStatus:
        # Your algorithm here
        pass
```

**Use for**: Filtering, scoring, property calculation, similarity search

### MultiStepMethod

For methods that require multiple sequential steps:

```python
from rapier.methods.base_implementations import MultiStepMethod, MethodStep

class MyMultiStepMethod(MultiStepMethod):
    register_name = "my_multistep"
    
    def __init__(self, name, version, parameters=None):
        super().__init__(name, version, parameters)
        
        # Define steps
        self.steps = [
            MethodStep('PreProcess', 'Clean input data'),
            MethodStep('Compute', 'Run main algorithm'),
            MethodStep('PostProcess', 'Format results')
        ]
    
    def execute_step(self, task: Task, step_index: int) -> TaskStatus:
        if step_index == 0:
            return self._preprocess(task)
        elif step_index == 1:
            return self._compute(task)
        elif step_index == 2:
            return self._postprocess(task)
```

**Use for**: Docking (prep + dock), workflows with dependencies between stages

### ParallelizableMethod (Mixin)

For methods that can process data in parallel chunks:

```python
from rapier.methods.base_implementations import SingleStepMethod, ParallelizableMethod

class MyParallelMethod(SingleStepMethod, ParallelizableMethod):
    register_name = "my_parallel_method"
    
    def split_inputs(self, inputs, num_chunks):
        # Split ligands across chunks
        pass
    
    def merge_outputs(self, chunk_outputs):
        # Combine results from chunks
        pass
```

**Use for**: Docking large ligand libraries, embarrassingly parallel computations

## Creating a Simple Method

Here's a complete example of a molecular weight filter:

```python
from typing import Dict, Any, List, Tuple
from pathlib import Path

from rapier.core.task import Task
from rapier.core.enums import TaskStatus
from rapier.core.specs import InputSpec, OutputSpec, ResourceSpec
from rapier.methods.base_implementations import SingleStepMethod


class MolecularWeightFilter(SingleStepMethod):
    """Filter molecules by molecular weight."""
    
    register_name = "mw_filter"
    
    def __init__(self, name="mw_filter", version="1.0", parameters=None):
        super().__init__(name, version, parameters)
        
        # Get filter parameters
        self.mw_min = parameters.get('mw_min', 0)
        self.mw_max = parameters.get('mw_max', 500)
        
        # Define specifications
        self.input_spec = InputSpec(
            required_files={
                'molecules': {
                    'description': 'Molecules to filter',
                    'format': 'sdf'
                }
            }
        )
        
        self.output_spec = OutputSpec(
            files={
                'filtered': {
                    'description': 'Molecules passing filter',
                    'format': 'sdf',
                    'path': 'filtered.sdf'
                }
            }
        )
        
        self.env_requirements = {
            'software': ['python', 'rdkit'],
            'modules': [],
            'conda_env': None
        }
        
        self.resource_spec = ResourceSpec(
            cpus=1,
            memory_gb=2,
            time_minutes=30
        )
    
    def validate_inputs(self, inputs: Dict[str, Any]) -> Tuple[bool, List[str]]:
        errors = []
        
        if 'molecules' not in inputs:
            errors.append("Missing 'molecules' input")
            return False, errors
        
        mol_path = Path(inputs['molecules'])
        if not mol_path.exists():
            errors.append(f"File not found: {mol_path}")
        
        return len(errors) == 0, errors
    
    def validate_environment(self) -> Tuple[bool, List[str]]:
        errors = []
        
        try:
            from rdkit import Chem
        except ImportError:
            errors.append("RDKit not available")
        
        return len(errors) == 0, errors
    
    def execute(self, task: Task) -> TaskStatus:
        try:
            from rdkit import Chem
            from rdkit.Chem import Descriptors
            
            # Load molecules
            mol_path = Path(task.inputs['molecules'])
            suppl = Chem.SDMolSupplier(str(mol_path))
            
            # Filter by molecular weight
            passed = []
            for mol in suppl:
                if mol is None:
                    continue
                
                mw = Descriptors.MolWt(mol)
                if self.mw_min <= mw <= self.mw_max:
                    passed.append(mol)
            
            # Write output
            output_dir = Path(task.work_dir) / 'outputs'
            output_dir.mkdir(parents=True, exist_ok=True)
            
            output_path = output_dir / 'filtered.sdf'
            writer = Chem.SDWriter(str(output_path))
            for mol in passed:
                writer.write(mol)
            writer.close()
            
            task.outputs = {'filtered': str(output_path)}
            
            return TaskStatus.COMPLETED
            
        except Exception as e:
            task.error_message = str(e)
            return TaskStatus.FAILED
```

## Normalized Input/Output Specifications

New methods should set `contract_version = 2`. A v2 method receives
`Artifact` objects through named ports and declares method settings separately
with a strict `ConfigSpec`:

```python
from rapier import Artifact
from rapier.core.specs import ConfigField, ConfigSpec, InputSpec, OutputSpec, PortSpec

class MolecularWeightFilter(SingleStepMethod):
    contract_version = 2

    def __init__(self, name="mw_filter", version="1.0", parameters=None):
        super().__init__(name, version, parameters)
        self.config_spec = ConfigSpec(fields={
            "mw_max": ConfigField((int, float), default=500, minimum=0),
        })
        self.config = self.normalize_config(parameters)
        self.input_spec = InputSpec(ports={
            "molecules": PortSpec("molecule.collection", ("smiles", "sdf")),
        })
        self.output_spec = OutputSpec(ports={
            "filtered": PortSpec("molecule.collection", "sdf"),
        })
```

File-backed artifacts implement the path protocol, so domain libraries can use
`Path(task.inputs["molecules"])`. Emit an `Artifact` for every declared output;
Rapier validates the type, format, cardinality, file existence, and provenance
before exposing it downstream.

The legacy file-oriented constructors below remain available only for existing
contract-v1 methods.

### InputSpec

Defines what files your method needs:

```python
self.input_spec = InputSpec(
    required_files={
        'receptor': {
            'description': 'Protein structure',
            'format': 'pdb',  # or ['pdb', 'pdbqt'] for multiple formats
            'example': 'protein.pdb'
        },
        'ligands': {
            'description': 'Molecules to dock',
            'format': 'sdf',
            'example': 'ligands.sdf'
        }
    },
    optional_files={
        'reference': {
            'description': 'Reference ligand',
            'format': 'sdf',
            'example': 'ref_ligand.sdf'
        }
    }
)
```

### OutputSpec

Defines what files your method produces:

```python
self.output_spec = OutputSpec(
    files={
        'results': {
            'description': 'Docked poses',
            'format': 'sdf',
            'path': 'docked_poses.sdf'  # Relative to outputs/ directory
        },
        'scores': {
            'description': 'Docking scores',
            'format': 'csv',
            'path': 'scores.csv'
        }
    }
)
```

**Important**: Always write outputs to `task.work_dir / 'outputs'` and update `task.outputs` with the paths.

## Environment Requirements

Specify software dependencies:

```python
self.env_requirements = {
    # Executables that must be in PATH
    'software': ['vina', 'mk_prepare_receptor.py', 'mk_prepare_ligand.py', 'mk_export.py', 'python'],
    
    # SLURM modules to load
    'modules': ['conda/4.10', 'cuda/11.2'],
    
    # Conda environment name
    'conda_env': 'screening-env'
}
```

Check these in `validate_environment()`:

```python
def validate_environment(self) -> Tuple[bool, List[str]]:
    from rapier.methods.utils import check_executable
    
    errors = []
    
    if not check_executable('vina'):
        errors.append("Vina not found in PATH")
    
    try:
        import rdkit
    except ImportError:
        errors.append("RDKit not installed")
    
    return len(errors) == 0, errors
```

## Resource Specifications

Tell SLURM what resources you need:

```python
self.resource_spec = ResourceSpec(
    cpus=4,              # Number of CPU cores
    memory_gb=8,         # RAM in GB
    time_minutes=120,    # Wall time limit
    partition='cpu',     # SLURM partition
    gpus=0              # Number of GPUs
)
```

**Guidelines**:
- Be conservative—request what you'll actually use
- Time limits prevent runaway jobs
- Consider parallel execution for large datasets

## Multi-Step Methods

Some methods naturally split into stages. For example, docking:

1. **Prepare Receptor**: Convert PDB to PDBQT (once per receptor)
2. **Dock Ligands**: Dock all ligands (parallelizable)

```python
from rapier.methods.base_implementations import MultiStepMethod, MethodStep

class DockingMethod(MultiStepMethod):
    register_name = "my_docking"
    
    def __init__(self, name, version, parameters=None):
        super().__init__(name, version, parameters)
        
        self.steps = [
            MethodStep(
                name='PrepareReceptor',
                description='Convert receptor to PDBQT',
                resource_spec={'cpus': 1, 'memory_gb': 2, 'time_minutes': 10}
            ),
            MethodStep(
                name='DockLigands',
                description='Dock ligands',
                resource_spec={'cpus': 4, 'memory_gb': 8, 'time_minutes': 120}
            )
        ]
    
    def execute_step(self, task: Task, step_index: int) -> TaskStatus:
        if step_index == 0:
            # Prepare receptor
            receptor_pdbqt = self._convert_to_pdbqt(task.inputs['receptor'])
            
            # Save for next step
            task.intermediate_outputs = {'receptor_pdbqt': receptor_pdbqt}
            return TaskStatus.COMPLETED
            
        elif step_index == 1:
            # Get receptor from step 1
            receptor_pdbqt = task.intermediate_outputs['receptor_pdbqt']
            
            # Dock ligands
            results = self._dock(receptor_pdbqt, task.inputs['ligands'])
            task.outputs = results
            return TaskStatus.COMPLETED
```

**Key Points**:
- Each step gets its own SLURM job
- Store intermediate results in `task.intermediate_outputs`
- Step N+1 can access outputs from step N

## Parallelizable Methods

For methods that process items independently:

```python
from rapier.methods.base_implementations import SingleStepMethod, ParallelizableMethod

class ParallelDocking(SingleStepMethod, ParallelizableMethod):
    register_name = "parallel_docking"
    
    def split_inputs(self, inputs: Dict, num_chunks: int) -> List[Dict]:
        """Split ligands across chunks."""
        from rdkit import Chem
        
        ligands_path = Path(inputs['ligands'])
        suppl = Chem.SDMolSupplier(str(ligands_path))
        all_mols = [m for m in suppl if m is not None]
        
        chunk_size = len(all_mols) // num_chunks
        chunks = []
        
        for i in range(num_chunks):
            start = i * chunk_size
            end = start + chunk_size if i < num_chunks - 1 else len(all_mols)
            chunk_mols = all_mols[start:end]
            
            # Write chunk to temporary file
            chunk_path = f"chunk_{i}.sdf"
            writer = Chem.SDWriter(chunk_path)
            for mol in chunk_mols:
                writer.write(mol)
            writer.close()
            
            chunks.append({
                'receptor': inputs['receptor'],
                'ligands': chunk_path
            })
        
        return chunks
    
    def merge_outputs(self, chunk_outputs: List[Dict]) -> Dict:
        """Combine results from all chunks."""
        from rdkit import Chem
        
        # Combine docked molecules
        combined_sdf = 'combined_docked.sdf'
        writer = Chem.SDWriter(combined_sdf)
        
        for chunk_output in chunk_outputs:
            if 'docked_poses' in chunk_output:
                suppl = Chem.SDMolSupplier(chunk_output['docked_poses'])
                for mol in suppl:
                    if mol:
                        writer.write(mol)
        
        writer.close()
        
        return {'docked_poses': combined_sdf}
```

The platform will automatically:
1. Split inputs into chunks
2. Submit SLURM array jobs for parallel execution
3. Merge outputs when all chunks complete

## Testing Your Method

### Unit Testing

Create tests for your method logic:

```python
# tests/methods/test_my_method.py
import pytest
from rapier.methods.my_module import MyMethod
from rapier.core.task import Task

def test_validation():
    method = MyMethod(parameters={'param': 'value'})
    
    # Test input validation
    is_valid, errors = method.validate_inputs({})
    assert not is_valid
    assert len(errors) > 0

def test_execution():
    method = MyMethod()
    
    task = Task(
        task_id='test',
        method=method,
        inputs={'molecules': 'test_data/molecules.sdf'}
    )
    
    status = method.execute(task)
    assert status == TaskStatus.COMPLETED
    assert 'results' in task.outputs
```

### Integration Testing

Test with real data:

```bash
# Create test inputs
mkdir -p test_data
# Add test SDF files, etc.

# Run verification script
python3 -c "
from rapier.methods.my_module import MyMethod
from rapier.core.task import Task

method = MyMethod(parameters={'mw_max': 500})

# Check environment
is_valid, errors = method.validate_environment()
print(f'Environment valid: {is_valid}')
if not is_valid:
    print(f'Errors: {errors}')

# Create and run task
task = Task(
    task_id='integration_test',
    method=method,
    inputs={'molecules': 'test_data/molecules.sdf'}
)

status = method.execute(task)
print(f'Status: {status}')
print(f'Outputs: {task.outputs}')
"
```

## Registering Your Method

### Auto-Discovery

Add `register_name` to your class:

```python
class MyMethod(SingleStepMethod):
    register_name = "my_method"  # Used in config files
```

The registry will auto-discover it at runtime.

### Manual Registration

Or register explicitly:

```python
from rapier.methods import MethodRegistry

registry = MethodRegistry()
registry.register('my_method', MyMethod, metadata={
    'description': 'My custom screening method',
    'author': 'Your Name',
    'category': 'filtering'
})
```

### Using in Pipelines

Once registered, use in pipeline configs:

```yaml
methods:
  - name: filter_step
    type: my_method  # Your register_name
    config:
      mw_max: 500
      logp_max: 5
    inputs:
      molecules: ${inputs.ligands}
```

## Best Practices

1. **Validate Early**: Check inputs and environment before heavy computation
2. **Use Temp Directories**: Write intermediate files to `work_dir/temp`
3. **Handle Errors Gracefully**: Set `task.error_message` and return FAILED
4. **Log Progress**: Use print statements or logging—they appear in SLURM logs
5. **Clean Output**: Only write final results to `outputs/` directory
6. **Document Parameters**: Explain what each parameter does and valid ranges
7. **Test with Real Data**: Don't just test with toy examples
8. **Check File Sizes**: Validate outputs aren't empty before returning success

## Common Patterns

### Running External Tools

```python
from rapier.methods.utils import run_command

returncode, stdout, stderr = run_command(
    ['vina', '--receptor', receptor, '--ligand', ligand],
    cwd=work_dir
)

if returncode != 0:
    task.error_message = f"Vina failed: {stderr}"
    return TaskStatus.FAILED
```

### Processing with RDKit

```python
from rdkit import Chem

# Read molecules
suppl = Chem.SDMolSupplier(str(input_path))

# Process
results = []
for mol in suppl:
    if mol is None:
        continue
    # Process mol
    results.append(processed_mol)

# Write
writer = Chem.SDWriter(str(output_path))
for mol in results:
    writer.write(mol)
writer.close()
```

### Logging Results

```python
import json

log_data = {
    'total_molecules': total,
    'successful': success_count,
    'failed': fail_count,
    'parameters': self.parameters
}

log_path = output_dir / 'log.json'
with open(log_path, 'w') as f:
    json.dump(log_data, f, indent=2)
```

## Need Help?

- Check existing methods in `rapier/methods/` for examples
- Use the template: `examples/methods/custom_method_template.py`
- See API documentation for specs and base classes
- Test incrementally—validate, then execute, then integrate

---

Happy method development! 🚀
