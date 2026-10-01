# Stage 7: Method Library & Examples

## Goal
Create the base method classes for common virtual screening algorithms and implement concrete examples. Start with Vina docking (receptor preparation + docking steps), demonstrating multi-step method handling. Each method should specify its input format, output format, environment requirements, and implement validation logic.

## Files to Create

### `rapier/methods/__init__.py`
- Methods module initialization
- Export base classes and method registry
- Export concrete method implementations

### `rapier/methods/registry.py`
- `MethodRegistry` class - central method repository
- Methods:
  - `register(name: str, method_class: Type[BaseMethod])`
  - `get(name: str) -> Type[BaseMethod]`
  - `list_available() -> List[str]`
  - `get_info(name: str) -> Dict` - method metadata
- Singleton pattern for global registry
- Auto-discovery of methods in methods/ directory

### `rapier/methods/docking/__init__.py`
- Docking methods submodule
- Export docking method classes

### `rapier/methods/docking/vina.py`
- `VinaDocking` class - AutoDock Vina molecular docking
- Multi-step method:
  1. **PrepareReceptor**: prepare protein receptor (pdbqt conversion)
  2. **DockLigands**: dock ligands against prepared receptor
- Properties:
  - `input_spec`: receptor PDB, ligand SDF/MOL2, box coordinates
  - `output_spec`: docked poses SDF, scores CSV
  - `env_requirements`: AutoDock Vina, Open Babel, Python rdkit
  - `resource_spec`: 4 CPUs, 8GB RAM per docking job
- Methods:
  - `validate_inputs()`: check receptor/ligand files exist and are valid
  - `validate_environment()`: check Vina executable, Open Babel
  - `prepare_receptor()`: convert PDB to PDBQT
  - `dock()`: run Vina docking for all ligands
  - `collect_results()`: parse output, extract scores
- Support for exhaustiveness parameter, box size/center

### `rapier/methods/docking/smina.py`
- `SminaDocking` class - Smina docking (Vina derivative)
- Similar structure to Vina but with Smina-specific scoring
- Single-step method (no separate receptor preparation)
- Custom scoring function support

### `rapier/methods/screening/__init__.py`
- Screening methods submodule

### `rapier/methods/screening/ligand_filter.py`
- `LigandFilter` class - filter molecules by properties
- Single-step method
- Properties:
  - `input_spec`: ligand SDF file
  - `output_spec`: filtered SDF file, statistics JSON
  - `env_requirements`: RDKit
- Filter criteria: MW, LogP, HBA/HBD, TPSA, rotatable bonds (Lipinski's Rule of Five)
- Methods:
  - `validate_inputs()`: check SDF format
  - `execute()`: apply filters, write passing molecules
  - `collect_results()`: summary statistics

### `rapier/methods/screening/similarity_search.py`
- `SimilaritySearch` class - find similar molecules
- Properties:
  - `input_spec`: query molecule(s) SMILES/SDF, database SDF
  - `output_spec`: similar molecules SDF, similarity scores CSV
  - `env_requirements`: RDKit
- Fingerprint-based similarity (Morgan, MACCS, etc.)
- Tanimoto coefficient threshold
- Methods:
  - `compute_fingerprints()`
  - `calculate_similarity()`
  - `rank_and_filter()`

### `rapier/methods/preparation/__init__.py`
- Preparation methods submodule

### `rapier/methods/preparation/ligand_prep.py`
- `LigandPreparation` class - prepare ligands for docking
- Steps: 3D conformation generation, protonation, energy minimization
- Properties:
  - `input_spec`: SMILES or 2D SDF
  - `output_spec`: 3D SDF with prepared structures
  - `env_requirements`: RDKit, OpenBabel
- Methods:
  - `generate_3d_conformers()`
  - `add_hydrogens()`
  - `minimize_energy()`

### `rapier/methods/preparation/protein_prep.py`
- `ProteinPreparation` class - prepare protein receptors
- Steps: add missing atoms, add hydrogens, assign charges
- Properties:
  - `input_spec`: protein PDB
  - `output_spec`: prepared PDB/PDBQT
  - `env_requirements`: PDB2PQR, OpenBabel, MGLTools
- Methods:
  - `fix_structure()`
  - `add_hydrogens()`
  - `assign_charges()`

### `rapier/methods/base_implementations.py`
- Base implementations for common method patterns
- `SingleStepMethod` - abstract class for simple methods
- `MultiStepMethod` - abstract class for complex workflows
- `ParallelizableMethod` - mixin for embarrassingly parallel tasks
- `CheckpointableMethod` - mixin for long-running methods with checkpoints

### `rapier/methods/utils.py`
- Utility functions for methods
- Functions:
  - `parse_box_spec(spec: str) -> Dict` - "center=0,0,0 size=20,20,20"
  - `check_executable(name: str) -> bool` - verify command exists
  - `load_molecules_from_sdf(path: Path) -> List[Molecule]`
  - `write_molecules_to_sdf(molecules: List, path: Path)`
  - `parse_vina_output(output: str) -> List[Pose]`

### `examples/methods/custom_method_template.py`
- Template for creating new methods
- Annotated example showing all required components
- Copy-paste starting point for users

### `docs/method_development_guide.md`
- Guide for developing new methods
- Sections:
  - Method lifecycle overview
  - Required methods to implement
  - Input/output specification format
  - Environment requirement specification
  - SLURM resource requests
  - Multi-step method patterns
  - Testing your method
  - Registering with the platform

### `tests/methods/test_vina.py`
- Unit tests for Vina docking method
- Mock SLURM execution
- Test validation logic
- Test multi-step execution flow

### `tests/methods/test_registry.py`
- Tests for method registry
- Registration, retrieval, discovery

## Files to Modify

### `rapier/core/base_method.py`
- Add helper methods used by concrete implementations
- Add `from_config()` class method for instantiation from configuration

## Implementation Order

1. Implement base implementations (`base_implementations.py`) - patterns for methods
2. Implement method utilities (`utils.py`) - shared functions
3. Implement method registry (`registry.py`) - discovery system
4. Implement ligand filter (`ligand_filter.py`) - simplest example
5. Implement similarity search (`similarity_search.py`)
6. Implement ligand preparation (`ligand_prep.py`)
7. Implement protein preparation (`protein_prep.py`)
8. Implement Vina docking (`vina.py`) - complex multi-step example
9. Implement Smina docking (`smina.py`)
10. Create custom method template (`custom_method_template.py`)
11. Write method development guide (`method_development_guide.md`)
12. Write tests

## Verification Steps

```bash
# Test method registry
python3 -c "
from rapier.methods import MethodRegistry
from rapier.methods.screening import LigandFilter

registry = MethodRegistry()
registry.register('ligand_filter', LigandFilter)

method_class = registry.get('ligand_filter')
assert method_class == LigandFilter, 'Registry retrieval failed'

available = registry.list_available()
assert 'ligand_filter' in available, f'Method not listed: {available}'
print('✓ Method registry works')
"

# Test ligand filter (without actual execution)
python3 -c "
from rapier.methods.screening import LigandFilter
from rapier.core.specs import InputSpec, OutputSpec, ResourceSpec

method = LigandFilter(
    name='filter',
    version='1.0',
    parameters={'mw_max': 500, 'logp_max': 5}
)

# Check specs are defined
assert method.input_spec is not None, 'input_spec not defined'
assert method.output_spec is not None, 'output_spec not defined'
assert method.env_requirements is not None, 'env_requirements not defined'

print(f'Method: {method.name}')
print(f'Input spec: {method.input_spec}')
print('✓ Ligand filter method works')
"

# Test Vina docking method structure
python3 -c "
from rapier.methods.docking import VinaDocking
from rapier.core import Task

method = VinaDocking(
    name='vina',
    version='1.2',
    parameters={
        'exhaustiveness': 8,
        'box_center': [0, 0, 0],
        'box_size': [20, 20, 20]
    }
)

# Check multi-step
assert hasattr(method, 'steps'), 'Multi-step method missing steps attribute'
assert len(method.steps) == 2, f'Expected 2 steps, got {len(method.steps)}'
print(f'Vina steps: {[s.name for s in method.steps]}')

# Test validation (without real files)
task = Task(task_id='test', method=method, inputs={
    'receptor': '/path/to/receptor.pdb',
    'ligands': '/path/to/ligands.sdf'
})
is_valid, errors = method.validate_inputs(task.inputs)
# Will fail because files don't exist, but should not crash
print(f'Validation result: {is_valid}, errors: {errors}')
print('✓ Vina docking method structure works')
"

# Test method instantiation from config
python3 -c "
from rapier.methods import MethodRegistry
from rapier.core import BaseMethod

registry = MethodRegistry()
# Assume registry has auto-discovered methods

config = {
    'type': 'ligand_filter',
    'parameters': {
        'mw_max': 500
    }
}

# This would be used by config parser
method_class = registry.get(config['type'])
method = method_class.from_config(config)
print(f'Instantiated: {method.name}')
print('✓ Method instantiation from config works')
"

# Test environment validation
python3 -c "
from rapier.methods.docking import VinaDocking

method = VinaDocking(name='vina', version='1.2', parameters={})

is_valid, errors = method.validate_environment()
# Will likely fail in test environment (Vina not installed)
if not is_valid:
    print(f'Environment validation failed (expected): {errors}')
    print('✓ Environment validation logic works')
else:
    print('✓ Environment validation passed (Vina installed)')
"

# Test utility functions
python3 -c "
from rapier.methods.utils import parse_box_spec, check_executable

box = parse_box_spec('center=1.5,2.0,3.0 size=20,20,25')
assert 'center' in box and 'size' in box, 'Box parsing failed'
assert box['center'] == [1.5, 2.0, 3.0], f'Unexpected center: {box[\"center\"]}'
print('✓ Box spec parsing works')

# Check for common executable (like 'python')
has_python = check_executable('python3')
assert has_python, 'python3 not found (unexpected)'
print('✓ Executable checking works')
"

# Test base implementations
python3 -c "
from rapier.methods.base_implementations import SingleStepMethod
from rapier.core import Task
from rapier.core.enums import TaskStatus

class DummyMethod(SingleStepMethod):
    def execute(self, task: Task) -> TaskStatus:
        return TaskStatus.COMPLETED

method = DummyMethod(name='dummy', version='1.0')
task = Task(task_id='test', method=method, inputs={})

status = method.execute(task)
assert status == TaskStatus.COMPLETED, f'Unexpected status: {status}'
print('✓ Base implementations work')
"
```

## Key Decisions

**1. Method Registry Pattern**
- Centralized registration of all available methods
- Auto-discovery of methods in rapier/methods/ directory
- Enables dynamic method loading from config
- Users can register custom methods

**2. Multi-Step Method Pattern**
- Methods can define multiple sequential steps
- Each step has its own SLURM job, validation, outputs
- Step N outputs become available for step N+1
- Example: Vina PrepareReceptor → DockLigands
- Enables checkpoint/resume for long workflows

**3. Specification-Based Validation**
- Input/output/environment specs defined declaratively
- Validation logic derived from specs
- Clear contracts between methods and platform
- Self-documenting method requirements

**4. Parameterization**
- Methods accept parameters dict for flexibility
- Parameters validated against method schema
- Enables easy configuration without code changes
- Example: Vina exhaustiveness, box size configurable

**5. Environment Abstraction**
- Methods declare requirements (executables, modules, libraries)
- Platform handles environment setup (module load, conda activate)
- Method code doesn't deal with environment management
- Portable across different HPC systems

**6. Parallelization Support**
- Methods declare if they're parallelizable (ParallelizableMethod mixin)
- Platform handles splitting input and distributing to SLURM array jobs
- Example: Docking 10000 ligands → 100 jobs × 100 ligands each
- Methods just process their assigned subset

**7. Checkpointing for Long Methods**
- CheckpointableMethod mixin provides checkpoint/resume
- Saves intermediate state periodically
- Enables resumption after failures or time limits
- Critical for methods taking hours/days

**8. Real-World Examples First**
- Start with actually-used methods (Vina, ligand filters)
- Ensures platform design is practical
- Provides templates for users
- Tests platform with realistic workloads
