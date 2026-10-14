# Configuration Internals

How Rapier turns a pipeline file into a running DAG: the parser, the validator,
the Python config API, and execution semantics. For the YAML keys themselves,
see the [Pipeline Configuration Reference](pipeline_configuration.md).

## Processing order

`ConfigParser.parse_file(path)` → `ConfigValidator.validate(config)` →
`Pipeline.from_config(config)` → `ExecutionEngine.execute(pipeline)`

1. **Read.** If no `project_dir` was given, it becomes the YAML file's directory.
2. **Substitute.** `${VAR}` placeholders in the raw text are replaced, in this
   order: `PROJECT_DIR`, then parser variables (`set_variable()` or an earlier
   parse's `environment.variables`), then `os.environ`. Unknown placeholders
   are left as they are.
3. **Load.** The text goes through `yaml.safe_load` and must produce a mapping.
4. **Record variables.** `environment.variables` is stored on the parser, so it
   only affects later parses. `environment.project_dir` is set to the resolved
   directory.
5. **Merge includes.** `include` files are merged in. See
   [Includes](pipeline_configuration.md#includes).
6. **Build dataclasses.** `PipelineConfig.from_dict` creates them. Relative
   `inputs.*.source` paths are joined to `project_dir`. Unrecognized top-level
   keys, including `ui`, are dropped.

`Pipeline.from_config` runs the full validator again, and raises
`ConfigurationError` if it finds any errors. So `--build` never builds an
invalid pipeline.

## Python API

```python
from rapier.config import ConfigParser, ConfigValidator

parser = ConfigParser()                    # or ConfigParser(project_dir="/data/proj")
parser.set_variable("TARGET", "kinase")    # available to ${TARGET}
config = parser.parse_file("pipeline.yaml")
# also: parser.parse_string(text), parser.parse_dict(data)

is_valid, errors = ConfigValidator().validate(config)
pipeline = config.to_pipeline()            # same as Pipeline.from_config(config)
```

| Class | Construction-time checks (raise `ValueError`) |
|-------|-----------------------------------------------|
| `PipelineConfig` | non-empty `name`, at least one method, valid `failure_policy`, `outputs` are non-empty strings |
| `MethodConfig` | non-empty `id`/`type`, valid `failure_policy`, `retry_count >= 1`, not both `config` and `parameters`, `inputs` are non-empty strings |
| `ArtifactConfig` | exactly one of `source` / `value` |
| `ConnectionConfig` | `from` is a string or list, `to` non-empty, `type` is `serial`/`parallel` |
| `PoolingRuleConfig` | known strategy, `metric` for `best`, `function` for `custom` |

`to_dict()` on each class serializes it back. `MethodConfig.to_dict()` always
writes `config` and never the deprecated `parameters`.

### Templates

`rapier.config.templates` builds `PipelineConfig` skeletons with methods and
connections but without bindings or config:

| Function | Topology | Generated ids |
|----------|----------|---------------|
| `create_simple_pipeline(name, method_types)` | `A → B → C` | `step_1`, `step_2`, … |
| `create_parallel_pipeline(name, initial_method, parallel_methods, final_method=None)` | `A → [B, C] → D` | `initial`, `parallel_N`, `final` |
| `create_pooling_pipeline(name, initial_methods, pooling_method, pooling_strategy="all", final_method=None, metric=None)` | `[A, B] → Pool → C` | `initial_N`, `pooling`, `final` |
| `create_diamond_pipeline(name, preparation_method, parallel_methods, pooling_method, final_method, pooling_strategy="all", metric=None)` | `A → [B, C] → Pool → D` | `preparation`, `parallel_N`, `pooling`, `final` |

### JSON Schema

`rapier.config.schema.PIPELINE_SCHEMA` is a draft-07 description of the file
format, for editors and external tools. Rapier never validates against it
itself; `ConfigValidator` enforces the rules. The schema is stricter in a few
places: `version` must match `X.Y.Z`, method `id` must match
`^[a-zA-Z][a-zA-Z0-9_-]*$`, `memory`/`time_limit` must be digits plus unit, and
`retry_count` must be ≤10. It doesn't describe `include` or `ui`.

## Validation rules

`ConfigValidator.validate` collects every error rather than stopping at the
first, and returns `(is_valid, errors)`.

**Structure**
- Pipeline name is non-blank.
- At least one method, no duplicate method ids.
- Global and per-method `failure_policy` are `stop`, `continue`, or `retry`.

**Methods**
- `id` starts with a letter.
- `resources.memory` ends in `MB`/`GB`/`TB` and `resources.time_limit` ends in
  `s`/`m`/`h`/`d`.

**Graph**
- Every connection `from`/`to` names an existing method.
- No cycles over the combined connection and binding edges. The error shows
  the cycle path.

**Pooling**
- The target method exists and has more than one upstream method (connections
  plus bindings).
- The strategy is known, and `best` has a `metric`.

**Contracts** (`validate_contracts`, which runs method auto-discovery first)
- Every pipeline `inputs` entry passes artifact validation: known type, a
  format valid for the type, the `source` file exists, and type-specific checks
  such as sequence alphabets or box shape.
- Each method of a registered type is instantiated as
  `method_class(name=<id>, config=<config>)`, which runs `migrate_config` and
  validates the config strictly (known keys, types, choices, min/max, and the
  method's `check_config` cross-field rules).
- A method with `inputs` must have a registered `type`.
- Each method's `validate_bindings(bound_port_names)` runs, and each returned
  message becomes an error. The default returns none.
- In a normalized pipeline, every required input port of every registered
  method is bound.
- Every bound port exists. A `pipeline.<x>` reference must name a declared
  input, and `<method>.<port>` must name a real output port.
- The source type/format is accepted by the port, either directly or through a
  converter path that is available on this machine.
- Every `outputs` entry has the form `<method>.<port>` and names an existing
  output port.

Checks done at run time, in `TaskRunner.prepare_task`: `InputResolver`
confirms the upstream output actually exists, then the method's
`format_inputs` wraps each value as the port's data class, checks that a
`one`-cardinality port receives exactly one artifact, applies converters, and
symlinks (or copies) files into `<work_dir>/input/normalized/`.
`validate_inputs` and `validate_environment` follow. When the method runs,
`format_outputs` checks the outputs against the output ports, adds
provenance, and fails the task if it returns an undeclared port.

### `validate_bindings` hook

Methods whose required inputs depend on config can declare:

```python
def validate_bindings(self, bound_ports: Iterable[str]) -> List[str]:
    """Return one message per problem; empty list if the bindings are fine."""
```

The method instance already holds its normalized config. The validator
prefixes each message with `Method '<id>': `. `Method.validate_inputs` calls
it too, with the ports that received a value, so pipelines built in Python get
the same check. `molecule_filter` uses it. See the
[Method Development Guide](method_development_guide.md).

## Execution semantics

**DAG.** Edges come from `connections` (each `from` → `to`) and from every
`<method>.<port>` binding. Duplicate edges collapse into one. The connection
`type` isn't used here.

**Scheduling.** A task becomes ready once all of its upstream tasks are
`COMPLETED`. On each poll (`poll_interval`, default 10 s), the engine submits
up to `max_parallel` tasks (default 4) as SLURM jobs, then checks the running
jobs. Independent branches therefore run in parallel automatically.

**Jobs.** Each job runs `method.get_command(task)`. For most methods that is
the generic runner, `python -m rapier.methods.runner --task
<work_dir>/task.json`, which executes the method on the compute node and
writes `<work_dir>/outputs/outputs.json`; `vina_docking` and `boltz2` run
their own manifest runners. After the job completes, `collect_outputs` reads
the results into `task.outputs`. A collection error is recorded in
`task.error_message`.

**Dry runs.** With `dry_run=True` the SLURM client accepts every job and
reports it `COMPLETED` without running it, and outputs are not collected. A
dry run therefore checks scheduling and the generated scripts, but a task
bound to another method's output fails with "Upstream output ... is not
available".

**Failure handling.** This is driven by each task's own `failure_policy`. The
pipeline-level value is never applied to tasks.

| Policy | Engine action |
|--------|---------------|
| `stop` | Cancels the main loop. Running jobs aren't killed, and no new tasks start |
| `continue` | Every transitive downstream task becomes `SKIPPED`. Unrelated branches go on |
| `retry` | `retry_count += 1` and the task goes back to `PENDING`, up to `max_retries` (the method's `retry_count`). After that, the run stops |

A submission error, such as an input contract failure in `prepare_task`, is
handled the same way as a job failure. `ExecutionResult.success` is true only
when no task failed and the run wasn't cancelled.

**Pooling.** Rules end up on `Pipeline.pooling_rules` as strings (`all`,
`best[metric]`, `best[metric]:minimize`, `custom[fn]`). `ResultPooler`
implements them, but `ExecutionEngine` doesn't call it yet. Use
`ResultPooler.pool_results(tasks, rule)` and `merge_outputs(tasks)` directly,
or have the downstream method bind each branch's output.

**State.** After each step, progress is written to
`<state_dir>/<pipeline name>_state.json` (default `./.rapier_state`).
`execute(pipeline, resume=True)` loads that file if it exists.

**Exports.** At the end of the run, `outputs` entries are resolved from the
completed tasks into `ExecutionResult.pipeline_outputs`.
