# API Reference

Developer reference for the main public classes. Signatures reflect the current
implementation; see the source for full docstrings.

> For a conceptual overview, read [Architecture](architecture.md) first.

The top-level package re-exports the most common names:

```python
from rapier import (Method, MethodError, RunContext, Task, Pipeline, ExecutionGraph,
                    PortSpec, InputSpec, OutputSpec, ConfigField, ConfigSpec,
                    EnvironmentSpec, ResourceSpec,
                    Artifact, ArtifactProvenance, ArtifactStorage, ArtifactType,
                    ArtifactRegistry, ArtifactConverter, ConverterRegistry,
                    # data types (rapier.data)
                    Sequence, ProteinSequence, DnaSequence, RnaSequence,
                    Molecule, Molecules, Poses, Structure, ProteinStructure,
                    NucleicAcidStructure, ComplexStructure, BindingSite,
                    Table, Scores, Report, File)
```

## rapier.core

### `Method`

Base class of every method (`rapier.core.method`).

```python
Method(name: Optional[str] = None, config: Optional[Dict[str, Any]] = None, *,
       version: Optional[str] = None,
       parameters: Optional[Dict[str, Any]] = None)   # alias of config
```

Class attributes: `register_name` (opts into auto-discovery), `display_name`,
`category`, `version` (default `"1.0"`), `inputs` / `outputs`
(`{port: PortSpec}`), `config_fields` (`{name: ConfigField}`), `resources`
(`ResourceSpec`), `environment` (`EnvironmentSpec`), `ui_hints`.

Instance attributes: `name`, `version`, `config` (normalized), `input_spec`,
`output_spec`, `config_spec`, `resource_spec`, `env_requirements`. The
constructor raises `ValueError` for an invalid config.

Hooks to override: `run(inputs, ctx) -> Dict[str, Any]` (required),
`migrate_config(config) -> dict`, `check_config(config) -> List[str]`,
`validate_bindings(bound_ports) -> List[str]`,
`check_inputs(inputs) -> List[str]`.

Other methods:

- `execute(task) -> TaskStatus`: `format_inputs` → `validate_inputs` → `run`
  → `format_outputs` into `task.outputs`; any exception fails the task
- `format_inputs(raw, input_dir=None) -> dict`: wrap, validate, convert and
  (with `input_dir`) stage raw values
- `coerce_inputs(raw) -> (dict, List[str])`: wrap without writing files
- `validate_inputs(inputs) -> (bool, List[str])`: port contract, then
  `validate_bindings`, then `check_inputs`
- `validate_environment() -> (bool, List[str])`: `env_requirements.validate()`
- `format_outputs(raw, ctx=None) -> dict`: wrap, validate, add provenance
- `get_command(task) -> str`: default writes `task.json` and returns the
  `rapier.methods.runner` command
- `collect_outputs(task) -> dict`: default reads `outputs/outputs.json`

`RunContext(task, work_dir, input_dir, output_dir, resources={})`, built with
`RunContext.for_task(task)`. `MethodError(RuntimeError)` fails a task with its
message.

### `Task`

```python
Task(task_id: str, method: Optional[Method],
     inputs: Dict[str, Any], metadata: Optional[Dict[str, Any]] = None)
```

Key attributes: `outputs`, `intermediate_outputs`, `status`, `pipeline`,
`work_dir`, `input_dir`, `output_dir`, `log_dir`, `failure_policy`,
`max_retries`, `retry_count`, `error_message`, `slurm_job_id`.

Methods: `to_dict()`, classmethod `from_dict(data, method=None)`,
`update_from_slurm_job(job)`, property `duration`.

### `Pipeline`

```python
Pipeline(name: str, description: str = "")
```

Attributes: `tasks`, `connections`, `pooling_rules`, `metadata`, `artifacts`
(top-level `Artifact`s), `output_bindings`.

Methods:

- `add_task(task)`
- `add_connection(from_task_id, to_task_id)`
- `add_pooling_rule(task_id, rule)`
- `get_upstream_tasks(task_id) -> List[str]`
- `get_downstream_tasks(task_id) -> List[str]`
- `validate() -> (bool, List[str])`
- `to_execution_graph() -> ExecutionGraph`
- classmethod `from_config(config) -> Pipeline` (runs `ConfigValidator`;
  raises `ConfigurationError` on errors)

Property: `pooling_nodes`.

### `ExecutionGraph`

```python
ExecutionGraph()
```

Methods: `add_node(task)`, `add_edge(from_task_id, to_task_id)`,
`get_ready_tasks()`, `mark_complete(task_id, status)`,
`mark_task_status(task_id, status)`, `get_upstream_tasks(id)`,
`get_downstream_tasks(id)`, `get_all_downstream_tasks(id)`,
`get_topological_order()`, `is_complete()`, classmethod
`from_pipeline(pipeline)`. `get_upstream`/`get_downstream` are aliases.

### Specs

Port and config contracts are defined in `rapier.artifacts.contracts` and
re-exported from `rapier.core.specs`:

```python
PortSpec(types, formats=None, required=True, cardinality="one",  # or "many"
         description="")      # types: data class(es) or type string(s)
InputSpec(ports={...})        # a plain {name: PortSpec} map
OutputSpec(ports={...})
ConfigSpec(fields={...}, strict=True, validator=None)
ConfigField(types, required=False, default=..., choices=None,
            minimum=None, maximum=None, validator=None, description="")
```

`rapier.core.specs` also defines:

```python
EnvironmentSpec(executables=[], modules=[], conda_env=None,
                python_packages=[], env_variables={})
ResourceSpec(cpus=1, memory="4GB", gpus=0, gpu_type=None,
             time="1:00:00", partition=None, nodes=1)
```

`PortSpec.accepts(artifact)`, `InputSpec.validate(inputs)`,
`OutputSpec.validate(output)`, `ConfigSpec.normalize(config)` /
`validate(config)`, and `EnvironmentSpec.validate()` check values;
`ResourceSpec.to_slurm_directives()` returns `#SBATCH` lines. `PortSpec` also
has `data_class` (the first type's class), `coerce(value, directory=None,
stem="data")` (wrap a raw value as that class) and `placeholder()` (a
representative artifact for static checks). `InputSpec`/`OutputSpec` expose
`ports`, `required` and `optional`.

### Enums (`rapier.core.enums`)

- `TaskStatus`: `PENDING`, `RUNNING`, `COMPLETED`, `FAILED`, `CANCELLED`, `SKIPPED`
- `MethodType`: `SINGLE_STEP`, `MULTI_STEP` (no longer used by `Method`)
- `FailurePolicy`: `STOP`, `CONTINUE`, `RETRY`

### Exceptions (`rapier.core.exceptions`)

`RapierError` (base), `ValidationError`, `ExecutionError`, `DependencyError`,
`ConfigurationError`, `SlurmError`.

## rapier.artifacts

### `Artifact`

```python
Artifact(type: str, format: str, value: Any,
         storage: ArtifactStorage = ArtifactStorage.FILE,  # or INLINE
         artifact_id: str = <uuid4>, metadata: dict = {},
         provenance: ArtifactProvenance = ArtifactProvenance())
```

Constructing (or `from_dict`-ing) an `Artifact` with a registered type string
returns an instance of that type's data class (`Artifact("table.scores", ...)`
is a `Scores`); unknown dotted names use their nearest registered prefix.

Properties: `is_file`, `path`. Methods: `with_value(value, storage=None,
format=None)`, `to_dict()`, classmethod `from_dict(data)`. Classmethods on
every data class: `from_file(path, format=None, metadata=None,
type_name=None)` (format from the suffix), `inline(value, format=None,
metadata=None, type_name=None)`, `coerce(value, *, type_name=None,
formats=(), directory=None, stem="data")` (wrap a raw path/payload) and
`check_value(artifact)` (inline-payload errors). File artifacts implement
`__fspath__`. `artifact_to_data(value)` / `artifact_from_data(value)` convert
nested structures for serialization; `data_class_for(type_name)` and
`format_from_suffix(path)` are helpers.

`ArtifactProvenance(source_artifact_ids=[], producing_task=None,
output_port=None, conversions=[])` records origin and conversion history.

### `ArtifactRegistry`

```python
ArtifactType(name, formats, parents=(), validator=None, description="")
registry = get_artifact_registry()       # shared default registry
registry.register(artifact_type, replace=False)
registry.get(name) / has(name) / list_types()
registry.is_compatible(actual, expected) -> bool   # follows parent types
registry.validate(artifact, check_file=True) -> (bool, List[str])
```

The default registry is built from the data classes: each class's
`type_name`, `formats`, nearest typed base class (parent), `check_value`
(validator) and first docstring line (description). Data classes defined later
are added on the next `get_artifact_registry()` call. Built-in types:
`sequence` (+ `.protein`, `.dna`, `.rna`), `molecule`, `molecule.collection`
(+ `.poses`), `structure` (+ `.protein`, `.nucleic_acid`, `.complex`),
`binding_site`, `table` (+ `.scores`), `report`, `file`.

### `ConverterRegistry`

```python
ArtifactConverter(name, source_type, source_format, target_type, target_format,
                  convert, priority=0, available=lambda: True, provider="core")
registry = get_converter_registry()      # shared default registry
registry.register(converter, replace=False)
registry.list_converters(available_only=False) -> List[str]
registry.find_path(source_type, source_format, target_types, target_formats)
    -> Optional[List[ArtifactConverter]]
registry.convert_for_port(artifact, port, work_dir) -> Artifact
materialize_inline(artifact, work_dir, name) -> Artifact
```

The default registry has core table (csv ↔ tsv ↔ json) and inline-sequence →
FASTA converters, plus RDKit (SMILES ↔ SDF), Gemmi (CIF ↔ PDB), and Open Babel
(CIF/PDBQT → PDB) providers that are active only when installed. A converter
for a parent type also applies to its subtypes; the result keeps the subtype
when the subtype supports the target format.

### `InputResolver` / `prepare_input`

```python
InputResolver()
  .resolve_task(task) -> Dict[str, Any]
  .resolve_reference(pipeline, reference)        # staticmethod
prepare_input(artifact, port, input_dir, name,
              artifact_registry=None, converter_registry=None) -> Artifact
```

`resolve_task` only looks up `task.metadata["input_bindings"]` references
(`pipeline.<name>` or `<task_id>.<port>`) and returns the raw values.
`Method.format_inputs` then calls `prepare_input` per artifact, which
validates it, writes inline values, converts to a format the port accepts and
stages it under `input_dir` (without `input_dir` it only validates). Output
checks happen in `Method.format_outputs`; there is no separate output
normalizer.

## rapier.data

The data classes listed in [Method Reference → Artifact types](method_reference.md#artifact-types),
plus helpers:

```python
Molecules.records(name_property="") -> List[MoleculeRecord]   # sdf or smiles, no RDKit
Molecules.ids() -> List[str]
Molecules.from_smiles(smiles, names=None, metadata=None)
Molecules.write_sdf(blocks, path, metadata=None)              # also Molecule, Poses
MoleculeRecord(id, name, index, pose=1, smiles=None, block=None, properties={}, atoms=[])
Table.rows() -> List[dict]
Table.from_rows(rows, path=None, format=None, columns=None, metadata=None)
Scores.records(id_column="") -> List[dict]       # adds sanitized "id" and int "pose"
Scores.directions(include_known=True) -> Dict[str, str]   # column -> "lower"/"higher"
Scores.from_records(records, path=None, directions=None, units=None, format=None, metadata=None)
Sequence.sequence; Sequence.from_string(text, metadata=None)
BindingSite.box(); BindingSite.from_box(center, size, metadata=None)
Report.load()
molecule_id(name, fallback="") -> str            # the shared, filename-safe join key
```

Also exported: `split_sdf`, `parse_sdf_record`, `parse_smiles_lines`,
`sdf_terminated`, `load_box`, `load_table`, `write_rows` and
`KNOWN_DIRECTIONS` (direction of well-known score columns).

## rapier.config

### `ConfigParser`

```python
ConfigParser(project_dir: Optional[str] = None)
parser.parse_file(path) -> PipelineConfig
parser.parse_string(content) -> PipelineConfig
parser.parse_dict(data) -> PipelineConfig
parser.set_variable(name, value)
parser.set_project_dir(project_dir)
```

### `ConfigValidator`

```python
ConfigValidator().validate(config) -> (bool, List[str])
```

Individual checks: `validate_structure`, `validate_connections`,
`validate_methods`, `validate_contracts`, `validate_resources(resources)`.

### `PipelineConfig`

Dataclass with `name`, `methods`, `description`, `version`, `connections`,
`pooling_rules`, `failure_policy`, `environment`, `inputs`
(`Dict[str, ArtifactConfig]`), and `outputs` (`Dict[str, str]` of references).
Methods: `get_method(id)`, `get_method_ids()`, `has_method(id)`,
`get_upstream_methods(id)`, `get_downstream_methods(id)`, `to_dict()`,
`to_pipeline()`, classmethod `from_dict(data)`.

### Other config dataclasses

```python
ArtifactConfig(type, format, source=None, value=..., metadata={})  # exactly one of source/value
  .to_artifact() -> Artifact
MethodConfig(id, type, description=None, inputs={}, config={},
             parameters=None,  # deprecated alias of config
             resources=None, failure_policy="stop", retry_count=1)
ConnectionConfig(from_methods, to_method, connection_type="serial")
PoolingRuleConfig(strategy, metric=None, minimize=False, function=None)
```

### Templates

`create_simple_pipeline`, `create_parallel_pipeline`,
`create_pooling_pipeline`, `create_diamond_pipeline` build `PipelineConfig`s
for common topologies.

## rapier.engine

### `ExecutionEngine`

```python
ExecutionEngine(max_parallel: int = 4, poll_interval: float = 10.0,
                dry_run: bool = False, state_dir: Optional[Path] = None)
engine.execute(pipeline, resume=False, save_state=True) -> ExecutionResult
engine.start(); engine.step()
engine.pause(); engine.resume(); engine.cancel()
engine.wait_for_completion(timeout=None) -> bool
```

`state_dir` defaults to `./.rapier_state`.

### `TaskRunner`

```python
TaskRunner(slurm_client=None, script_generator=None, dry_run=False)
runner.run_task(task) -> TaskStatus
runner.prepare_task(task) -> bool
runner.submit_task(task) -> Optional[str]
runner.monitor_task(task)
runner.collect_task_results(task)
runner.get_task_status(task) -> TaskStatus
runner.cancel_task(task) -> bool
```

### `Scheduler`

```python
Scheduler(graph: ExecutionGraph)
scheduler.get_ready_tasks() -> List[Task]
scheduler.mark_running(task)
scheduler.mark_completed(task, status)
scheduler.mark_downstream_skipped(task) -> List[str]
scheduler.can_continue(task) -> bool
scheduler.get_progress() -> Dict[str, int]
scheduler.is_finished() -> bool
scheduler.has_failures() -> bool
scheduler.get_topological_order() -> List[str]
scheduler.reset()
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

Also: `should_retry(task)`, `should_continue(task)`,
`should_stop_pipeline(task)`, `mark_downstream_tasks(...)`,
`record_failure(task, error)`.

`FailureDecision` fields: `action` (`FailureAction`: `RETRY`, `CONTINUE`,
`STOP`, `SKIP_DOWNSTREAM`), `should_retry`, `should_stop_pipeline`,
`mark_downstream_skipped`, `reason`.

### `ParallelExecutor` / `TaskIsolation`

```python
ParallelExecutor(max_workers=None)
  .submit_parallel(tasks, task_fn) -> List[Future]
  .wait_any(futures, timeout=None) / .wait_all(futures, ...)
  .get_results(futures) / .cancel_all(futures) / .shutdown(wait=True)
TaskIsolation.create_task_workspace(task, base_dir) -> str
TaskIsolation.cleanup_workspace(task)
```

### Result and state

```python
ExecutionResult(pipeline_name, success, start_time, end_time=None,
                completed_tasks=[], failed_tasks=[], skipped_tasks=[],
                task_results={}, pipeline_outputs={})
  .duration / .total_tasks
  .get_task_result(task_id) / .get_final_outputs()
  .generate_report() -> str
  .to_dict() / from_dict(data) / save(path) / load(path)

ExecutionState.create_initial(pipeline_id, pipeline_name, task_ids)
  .update_task_status(task_id, status, metadata=None)
  .get_progress() -> float
  .is_finished / .is_running / .total_tasks
  .to_dict() / from_dict(data) / save(path) / load(path)
```

`ExecutionPhase`: `INITIALIZING`, `RUNNING`, `PAUSED`, `COMPLETED`, `FAILED`,
`CANCELLED`.

## rapier.slurm

### `SlurmClient`

```python
SlurmClient(dry_run: bool = False)
client.submit_job(script_path, dependencies=None, dependency_type='afterok') -> str
client.cancel_job(job_id) -> bool
client.get_job_status(job_id) -> Optional[str]
client.get_job_info(job_id) -> Optional[dict]
client.update_job_from_slurm(job) -> bool
client.wait_for_jobs(job_ids, poll_interval=30, timeout=None) -> Dict[str, str]
client.get_queue_info() -> List[dict]
```

### `ScriptGenerator`

```python
ScriptGenerator(template_dir: Optional[Path] = None)  # default: rapier/slurm/templates
gen.generate_script(job, command, work_dir=None, modules=None,
                    conda_env=None, virtualenv=None, env_vars=None,
                    pre_commands=None, post_commands=None, array_spec=None,
                    account=None, qos=None,
                    template_name='job_template.sh.j2') -> str
gen.generate_from_task(task, command, output_dir=None, **kwargs) -> str
gen.write_script(script_content, output_path) -> Path
gen.generate_and_write(job, command, output_path, **kwargs) -> Path
gen.generate_array_job_script(job, command_template, array_size,
                              work_dir=None, **kwargs) -> str
```

### `SlurmJob`

```python
SlurmJob(job_name, script_path=None, job_id=None,
         status=SlurmJobStatus.PENDING, ..., work_dir=None,
         stdout_path=None, stderr_path=None, exit_code=None,
         resources={}, dependencies=[], dependency_type="afterok", metadata={})
```

Properties: `is_finished`, `is_running`, `is_pending`, `succeeded`, `failed`,
`duration`. Methods: `update_status(status_str, exit_code=None)`,
`get_dependency_string()`, `get_output()`, `get_errors()`, `to_dict()`,
`from_dict(data)`.

`SlurmJobStatus`: `PENDING`, `RUNNING`, `COMPLETED`, `FAILED`, `CANCELLED`,
`TIMEOUT`, `OUT_OF_MEMORY`, `NODE_FAIL`, `UNKNOWN`.

### Other

- `DependencyBuilder()` — `build_dependency_string(upstream_jobs,
  policy='afterok')`, `build_from_failure_policy(...)`,
  `build_complex_dependency(...)`, `build_singleton_dependency(job_name)`,
  `parse_dependency_string(s)`, `validate_dependency_string(s)`,
  `get_upstream_job_ids(...)`.
- `JobMonitor(client=None, poll_interval=30)` — `add_job`, `remove_job`,
  callbacks `on_start`/`on_complete`/`on_fail`/`on_status_change`,
  `start_monitoring(job_ids=None)`, `stop_monitoring()`,
  `wait_for_completion(timeout=None)`, count helpers.
- `SlurmConfig` / `PartitionInfo` — `is_slurm_available()`,
  `read_cluster_info()`, `get_partitions()`, `get_partition_info(p)`,
  `validate_partition(p)`, `get_partition_limits(p)`,
  `get_default_partition()`.
- `ResourceParser()` — `parse_memory(spec)`, `parse_time(spec)`,
  `parse_cpus(spec)`, `parse_gpus(spec)`, `format_time_for_slurm(seconds)`,
  `validate_resources(...)`.

## rapier.methods

### `MethodRegistry`

```python
registry = get_registry()             # or MethodRegistry(); it is a singleton
registry.auto_discover(package_path=None) -> int
registry.register(name, method_class, metadata=None)
registry.unregister(name)
registry.get(name) -> Type[Method]
registry.has(name) -> bool
registry.list_available() -> List[str]
registry.get_info(name) -> dict              # metadata + class, module
registry.list_info() -> Dict[str, dict]
registry.clear()
```

### Mixins and runner

```python
ParallelizableMethod         # mixin: can_parallelize() / split_inputs() / merge_outputs()
CheckpointableMethod         # mixin: save_checkpoint() / load_checkpoint() / should_checkpoint()
```

Combine them with `Method` (`class SminaDocking(Method, ParallelizableMethod)`);
the engine calls neither yet. `rapier.methods.runner` is the generic scheduler
entry point: `runner_command(method, task)` writes `task.json`,
`run_task_file(path)` rebuilds and executes the method and writes
`outputs/outputs.json`, `read_outputs(task)` reads it back, and
`python -m rapier.methods.runner --task <path>` runs a task file.

Utilities in `rapier.methods.utils`: `parse_box_spec`, `check_executable`,
`check_executables`, `validate_pdb_file`, `validate_sdf_file`,
`parse_vina_output`, `run_command`, `rdkit_molecules`, `write_rdkit_sdf`,
`resolve_box`, `DOCKING_SCORE_COLUMNS`. Concrete methods are listed in the
[Method Reference](method_reference.md).

## rapier.storage

```python
ProjectStructure(root); ProjectStructure.create_project(root)
PathManager(project_root, run_id)
FileTracker()
FileOrganizer(path_manager, file_tracker=None)
CleanupManager(file_tracker, path_manager=None)
CleanupPolicy(name, keep_inputs=True, keep_outputs=True, keep_logs=True,
              keep_intermediates=False, ...)
POLICIES                      # 'minimal', 'standard', 'aggressive'
Workspace(); workspace.initialize(root, run_id)
FileMetadata                  # per-file size/checksum/category metadata
validators                    # validate_sdf_file, validate_pdb_file, validate_csv_file, ...
```

See [Architecture](architecture.md#storage-rapierstorage).

## rapier.logging

`LogManager`, `get_log_manager()`, `initialize_logging(log_directory=None, config=None)`, plus
`PipelineLogger`, `TaskLogger`, `SlurmLogger`, formatters, `FileMonitor`,
`LogAggregator`, `AlertManager`, `CLIMonitor`, and `Dashboard`. See the
[logging guide](logging_guide.md).

Note that `rapier.logging` exports its own `ExecutionResult`, `TaskResult`,
`ExecutionState`, and `SlurmJob` classes, which are distinct from the engine
and SLURM classes of the same name.

## rapier.workflow_builder

Requires the `ui` extra (`pip install -e ".[ui]"`).

```bash
python -m rapier.workflow_builder [--host 127.0.0.1] [--port 8765] [--no-demo] [--debug]
```

```python
from rapier.workflow_builder import (build_catalog, graph_to_pipeline_dict,
    pipeline_dict_to_graph, graph_to_yaml, yaml_to_graph, validate_graph,
    GraphError, register_demo_methods, demo_pipeline, DEMO_METHODS)
from rapier.workflow_builder.server import create_app

build_catalog(registry=None) -> dict          # methods, types, converters, compat
graph_to_pipeline_dict(graph) -> dict         # raises GraphError
pipeline_dict_to_graph(data) -> dict
graph_to_yaml(graph) -> str; yaml_to_graph(text) -> dict
validate_graph(graph) -> {"valid": bool, "errors": [...]}
register_demo_methods(registry=None) -> List[str]
create_app(registry=None, demo=True)          # Flask app
```

HTTP endpoints: `GET /api/catalog`, `POST /api/validate`, `POST /api/export`,
`POST /api/import`, `GET /api/examples`, `GET /api/examples/<name>`. There is
no authentication; keep the default localhost bind.
