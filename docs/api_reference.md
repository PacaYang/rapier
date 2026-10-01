# API Reference

Developer reference for the main public classes. Signatures reflect the current
implementation; see the source for full docstrings.

> For a conceptual overview, read [Architecture](architecture.md) first.

## rapier.core

### `BaseMethod`

Abstract base for all methods.

```python
BaseMethod(name: str, version: str,
           parameters: Optional[Dict[str, Any]] = None,
           method_type: MethodType = MethodType.SINGLE_STEP)
```

Properties: `input_spec`, `output_spec`, `config_spec`, `env_spec`, `resource_spec`
(each lazily created and settable).

Lifecycle hooks to implement:

- `validate_inputs(inputs) -> (bool, List[str])`
- `validate_environment() -> (bool, List[str])`
- `execute(task) -> TaskStatus`

### `Task`

```python
Task(task_id: str, method: Optional[BaseMethod],
     inputs: Dict[str, Any], metadata: Optional[Dict[str, Any]] = None)
```

Key attributes: `outputs`, `intermediate_outputs`, `status`, `work_dir`,
`failure_policy`, `max_retries`, `retry_count`, `slurm_job_id`.

Methods: `to_dict()`, `from_dict(data, method=None)`, `update_from_slurm_job(job)`,
property `duration`.

### `Pipeline`

```python
Pipeline(name: str, description: str = "")
```

Methods:

- `add_task(task)`
- `add_connection(from_task_id, to_task_id)`
- `add_pooling_rule(task_id, rule)`
- `get_upstream_tasks(task_id) -> List[str]`
- `get_downstream_tasks(task_id) -> List[str]`
- `validate() -> (bool, List[str])`
- `to_execution_graph() -> ExecutionGraph`
- classmethod `from_config(config) -> Pipeline`

Property: `pooling_nodes`.

### `ExecutionGraph`

```python
ExecutionGraph()
```

Methods: `add_node(task)`, `add_edge(from_id, to_id)`, `get_ready_tasks()`,
`mark_complete(task_id, status)`, `get_upstream_tasks(id)`,
`get_downstream_tasks(id)`, `get_all_downstream_tasks(id)`,
`get_topological_order()`, `is_complete()`, classmethod `from_pipeline(pipeline)`.

### Specs

```python
PortSpec(types, formats=(), required=True, cardinality="one")
InputSpec(ports={...})
OutputSpec(ports={...})
ConfigSpec(fields={...}, strict=True)
ConfigField(types, required=False, default=..., choices=None,
            minimum=None, maximum=None)
EnvironmentSpec(executables=[], modules=[], conda_env=None,
                python_packages=[], env_variables={})
ResourceSpec(cpus=1, memory="4GB", gpus=0, gpu_type=None,
             time="1:00:00", partition=None, nodes=1)
```

Each spec has a `validate(...)` method; `ResourceSpec.to_slurm_directives()`
returns `#SBATCH` lines.

Legacy `required_files` and `files` constructors remain supported for v1
methods.

## rapier.artifacts

`Artifact(type, format, value, storage="file", metadata={}, provenance=...)`
is the normalized value exchanged between v2 methods. File artifacts implement
the path protocol, and `to_dict()`/`from_dict()` preserve their type, storage,
metadata, and provenance through task state and execution results.

`ArtifactRegistry` manages extensible semantic types. `ConverterRegistry`
registers deterministic format adapters; the default registry activates RDKit,
Gemmi, and Open Babel providers only when they are installed.

### Enums

- `TaskStatus`: `PENDING`, `RUNNING`, `COMPLETED`, `FAILED`, `CANCELLED`, `SKIPPED`
- `MethodType`: `SINGLE_STEP`, `MULTI_STEP`
- `FailurePolicy`: `STOP`, `CONTINUE`, `RETRY`

### Exceptions

`RapierError` (base), `ValidationError`, `ExecutionError`, `DependencyError`,
`ConfigurationError`, `SlurmError`.

## rapier.config

### `ConfigParser`

```python
ConfigParser(project_dir: Optional[str] = None)
parser.parse_file(path) -> PipelineConfig
parser.parse_string(content) -> PipelineConfig
parser.parse_dict(data) -> PipelineConfig
parser.set_variable(name, value)
```

### `ConfigValidator`

```python
ConfigValidator().validate(config) -> (bool, List[str])
```

### `PipelineConfig`

Dataclass with `name`, `methods`, `description`, `version`, `connections`,
`pooling_rules`, `failure_policy`, `environment`, `inputs`, and `outputs`.
Methods: `get_method(id)`,
`get_method_ids()`, `get_upstream_methods(id)`, `get_downstream_methods(id)`,
`to_pipeline()`, classmethod `from_dict(data)`.

## rapier.engine

### `ExecutionEngine`

```python
ExecutionEngine(max_parallel: int = 4, poll_interval: float = 10.0,
                dry_run: bool = False, state_dir: Optional[Path] = None)
engine.execute(pipeline, resume=False, save_state=True) -> ExecutionResult
engine.pause(); engine.resume(); engine.cancel()
```

### `Scheduler`

```python
Scheduler(graph: ExecutionGraph)
scheduler.get_ready_tasks() -> List[Task]
scheduler.mark_running(task)
scheduler.mark_completed(task, status)
scheduler.mark_downstream_skipped(task) -> List[str]
scheduler.get_progress() -> dict
scheduler.is_finished() -> bool
```

### `ResultPooler`

```python
ResultPooler()
pooler.pool_results(tasks, rule, metric=None, minimize=False)
pooler.register_custom_function(name, func)
pooler.merge_outputs(tasks, merge_strategy='concat') -> dict
```

Rules: `any`, `all`, `majority`, `best`/`best[metric]`, `first`, `custom[func]`.

### `FailureHandler`

```python
FailureHandler().handle_failure(task, error) -> FailureDecision
```

`FailureDecision` fields: `action`, `should_retry`, `should_stop_pipeline`,
`mark_downstream_skipped`, `reason`.

### Result and state

```python
ExecutionResult(pipeline_name, success, start_time, ...)
  .generate_report() -> str
  .to_dict() / from_dict(data) / save(path) / load(path)

ExecutionState.create_initial(pipeline_id, pipeline_name, task_ids)
  .update_task_status(task_id, status, metadata=None)
  .get_progress() -> float
  .save(path) / load(path)
```

## rapier.slurm

### `SlurmClient`

```python
SlurmClient(dry_run: bool = False)
client.submit_job(script_path, dependencies=None, dependency_type='afterok') -> str
client.cancel_job(job_id) -> bool
client.get_job_status(job_id) -> Optional[str]
client.wait_for_jobs(job_ids, poll_interval=30, timeout=None) -> dict
```

### `ScriptGenerator`

```python
ScriptGenerator(template_dir: Optional[Path] = None)
gen.generate_script(job, command, work_dir=None, modules=None,
                    conda_env=None, env_vars=None, array_spec=None, ...) -> str
gen.write_script(content, output_path) -> Path
gen.generate_array_job_script(job, command_template, array_size, ...) -> str
```

### `DependencyBuilder`, `ResourceParser`, `SlurmJob`

See [Architecture](architecture.md) and source docstrings. `SlurmJob` is a
dataclass with status properties (`succeeded`, `failed`, `is_finished`) and
`to_dict()`/`from_dict()`.

## rapier.methods

### `MethodRegistry`

```python
registry = MethodRegistry()           # singleton
registry.auto_discover()
registry.register(name, method_class)
registry.get(name) -> Type[BaseMethod]
registry.has(name) -> bool
registry.list_available() -> List[str]
registry.get_info(name) -> dict
```

### Method base classes

```python
SingleStepMethod(...)        # implement execute()
MultiStepMethod(...)         # define self.steps (list of MethodStep)
ParallelizableMethod         # mixin: split_inputs() / merge_outputs()
CheckpointableMethod         # mixin: checkpoint support
```

## rapier.storage

`ProjectStructure`, `PathManager`, `FileOrganizer`, `FileTracker`,
`CleanupManager`, `Workspace`. See [Architecture](architecture.md#storage-rapierstorage).

## rapier.logging

`LogManager`, `get_log_manager()`, `initialize_logging(log_directory, config)`,
plus `PipelineLogger`, `TaskLogger`, `SlurmLogger`, and formatters. See the
[logging guide](logging_guide.md).
