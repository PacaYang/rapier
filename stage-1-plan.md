# Stage 1: Core Architecture & Data Models

## Goal
Design and implement the foundational class structure for the platform. This includes base classes for Methods, Tasks, Pipelines, and the execution graph. Define the interfaces and contracts that all components will follow.

## Files to Create

### `rapier/__init__.py`
- Package initialization file
- Export main public classes
- Version information

### `rapier/core/__init__.py`
- Core module initialization
- Export base classes

### `rapier/core/base_method.py`
- `BaseMethod` abstract class
- Lifecycle hooks: `validate_inputs()`, `validate_environment()`, `prepare()`, `execute()`, `collect_results()`
- Properties: `name`, `version`, `input_spec`, `output_spec`, `env_requirements`
- Support for multi-step methods (list of steps)
- Status tracking (pending, running, completed, failed)

### `rapier/core/task.py`
- `Task` class representing a single method execution instance
- Properties: `task_id`, `method`, `inputs`, `outputs`, `status`, `metadata`
- Execution context (working directory, log file, SLURM job ID)
- Serialization methods for persistence

### `rapier/core/pipeline.py`
- `Pipeline` class for workflow representation
- Properties: `name`, `description`, `tasks`, `connections`
- Methods: `add_task()`, `add_connection()`, `validate()`
- Support for task metadata and pooling rules

### `rapier/core/execution_graph.py`
- `ExecutionGraph` class for DAG representation
- Build graph from Pipeline definition
- Methods: `add_node()`, `add_edge()`, `get_ready_tasks()`, `mark_complete()`, `get_downstream_tasks()`
- Topological sorting and dependency resolution
- Cycle detection

### `rapier/core/specs.py`
- Data models for specifications
- `InputSpec`: defines required inputs (files, parameters)
- `OutputSpec`: defines expected outputs (files, formats)
- `EnvironmentSpec`: defines environment requirements (software, modules, resources)
- `ResourceSpec`: SLURM resources (cpus, memory, gpus, time, partition)
- Validation methods for each spec type

### `rapier/core/enums.py`
- Enumerations for status values
- `TaskStatus`: PENDING, RUNNING, COMPLETED, FAILED, CANCELLED, SKIPPED
- `MethodType`: SINGLE_STEP, MULTI_STEP
- `FailurePolicy`: STOP, CONTINUE, RETRY

### `rapier/core/exceptions.py`
- Custom exception classes
- `ValidationError`: for input/environment validation failures
- `ExecutionError`: for method execution failures
- `DependencyError`: for dependency resolution issues
- `ConfigurationError`: for configuration parsing issues

## Implementation Order

1. Create package structure (`__init__.py` files)
2. Implement enums (`enums.py`) - used by other classes
3. Implement exceptions (`exceptions.py`) - used by other classes
4. Implement specs (`specs.py`) - used by methods and tasks
5. Implement base method (`base_method.py`) - foundation for all algorithms
6. Implement task (`task.py`) - uses method and specs
7. Implement pipeline (`pipeline.py`) - uses tasks
8. Implement execution graph (`execution_graph.py`) - uses pipeline and tasks

## Verification Steps

```bash
# Create test directory structure
mkdir -p tests/core

# Run Python interpreter to verify imports
python3 -c "from rapier.core import BaseMethod, Task, Pipeline, ExecutionGraph"
python3 -c "from rapier.core.specs import InputSpec, OutputSpec, EnvironmentSpec, ResourceSpec"
python3 -c "from rapier.core.enums import TaskStatus, MethodType, FailurePolicy"

# Run basic instantiation tests
python3 -c "
from rapier.core import Pipeline
p = Pipeline(name='test_pipeline', description='Test')
print(f'Created pipeline: {p.name}')
"

# Verify exception hierarchy
python3 -c "
from rapier.core.exceptions import ValidationError, ExecutionError
try:
    raise ValidationError('Test validation error')
except ValidationError as e:
    print(f'Caught: {e}')
"
```

## Key Decisions

**1. Abstract Base Class for Methods**
- Use Python's `abc.ABC` to enforce interface contracts
- Subclasses must implement: `validate_inputs()`, `validate_environment()`, `execute()`
- Provides default implementations for common functionality

**2. Task as Execution Instance**
- Separate Task (runtime instance) from Method (algorithm definition)
- Tasks are stateful, Methods are stateless definitions
- Allows multiple concurrent executions of the same Method

**3. Execution Graph using NetworkX**
- Leverage NetworkX for DAG operations (topological sort, cycle detection)
- Store tasks as nodes, dependencies as directed edges
- Enables efficient dependency resolution and parallel execution planning

**4. Spec Classes for Type Safety**
- Use dataclasses for Input/Output/Environment specifications
- Enables validation before execution
- Clear contracts between methods

**5. Multi-Step Method Support**
- Methods can define multiple steps (e.g., Vina: prep + dock)
- Each step has its own inputs, outputs, and SLURM job
- Steps execute sequentially, with output of step N becoming input to step N+1
