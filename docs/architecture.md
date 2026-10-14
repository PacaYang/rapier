# Architecture

This document explains how Rapier is structured and how its components fit
together.

## Overview

Rapier is a modular, SLURM-based virtual screening platform. A user describes a
**pipeline** in YAML (by hand or with the visual workflow builder); the
platform validates typed port bindings, builds an execution graph, schedules
tasks respecting dependencies, resolves and converts input artifacts, submits
jobs to SLURM, monitors progress, handles failures, and organizes the
resulting files.

```
 Workflow builder ──► YAML config
 (optional, Flask)        │
                          ▼
 ┌──────────────┐  PipelineConfig  ┌─────────────────┐  from_config  ┌──────────┐
 │ ConfigParser │ ───────────────► │ ConfigValidator │ ────────────► │ Pipeline │
 └──────────────┘                  └─────────────────┘               └──────────┘
                                                                          │ to_execution_graph
                                                                          ▼
                                   ┌───────────┐   ready tasks    ┌────────────────┐
                                   │ Scheduler │ ◄─────────────── │ ExecutionGraph │
                                   └───────────┘                  └────────────────┘
                                         │
                                         ▼
 ┌───────────────┐  stage inputs  ┌────────────┐  submit  ┌─────────────┐
 │ Method        │ ◄───────────── │ TaskRunner │ ───────► │ SlurmClient │
 │ format_inputs │                └────────────┘          └─────────────┘
 └───────────────┘                  │ collect_outputs           │ status
                                    ▼                           ▼
                         ┌──────────────────┐          ┌────────────────┐
                         │   task.outputs   │          │ FailureHandler │
                         └──────────────────┘          └────────────────┘
                                    │
                                    ▼
                            ┌──────────────┐
                            │ ResultPooler │  (join points)
                            └──────────────┘
```

## Package layout

```
rapier/
├── core/              # Method base class, Task, Pipeline, ExecutionGraph, specs
├── data/              # Typed data classes: Molecules, Poses, Scores, ProteinStructure, ...
├── artifacts/         # Artifact envelope, type registry, port/config contracts, converters, input resolution
├── config/            # YAML parsing, validation, templates
├── slurm/             # SLURM integration (scripts, submission, monitoring)
├── engine/            # Execution orchestration
├── logging/           # Multi-level logging and monitoring
├── storage/           # Project structure and file lifecycle management
├── methods/           # Screening method library and registry
└── workflow_builder/  # Visual node-based pipeline editor (optional `ui` extra)
```

## Components

### Core (`rapier.core`)

The foundational runtime types:

- **`Method`** (`rapier.core.method`) — the one base class of all methods.
  Subclasses declare their contract as class attributes (`inputs`/`outputs`
  ports over the `rapier.data` types, `config_fields`, `resources`,
  `environment`, `ui_hints`) and implement `run(inputs, ctx)`. The base class
  formats inputs (wrap raw values as the port's data class, validate, convert,
  stage) and outputs (wrap returned paths, rows and dicts, validate, add
  provenance), and provides a default `get_command`/`collect_outputs` through
  the generic `rapier.methods.runner`. `RunContext` and `MethodError` live
  alongside it.
- **`Task`** — a stateful runtime instance of a method execution. Holds inputs,
  outputs, status, directories, timing, retry state, and SLURM job info.
- **`Pipeline`** — a workflow definition: tasks, connections, pooling rules,
  top-level pipeline artifacts, and output bindings.
- **`ExecutionGraph`** — a NetworkX-backed DAG used for topological ordering,
  cycle detection, and ready-task computation.
- **Specs** — `EnvironmentSpec` and `ResourceSpec` live in `rapier.core.specs`;
  it also re-exports the port and config contracts from `rapier.artifacts`.
- **Enums and exceptions** — `TaskStatus`, `MethodType` (unused since the
  `Method` redesign), `FailurePolicy`, and the `RapierError` hierarchy.

### Data types (`rapier.data`)

One class per semantic type: `Sequence` (+ protein/DNA/RNA), `Molecule`,
`Molecules`, `Poses`, `Structure` (+ protein/nucleic-acid/complex),
`BindingSite`, `Table`, `Scores`, `Report`, `File`. Each class sets its type
string (`Poses.type_name == "molecule.poses"`), accepted formats,
inline-value validation and raw-value coercion, and offers typed readers such
as `Molecules.records()` and `Scores.records()`/`directions()`. Subclassing
makes a subtype (`Poses` of `Molecules`, `Scores` of `Table`) that is accepted
wherever its parent is. `molecule_id()` is the shared join key: molecule files
and score tables are matched on the sanitized `id` (plus `pose`).

### Artifacts (`rapier.artifacts`)

The envelope, registry and conversion services behind the data types:

- **`Artifact`** — a typed, serializable value (`type`, `format`, `value`,
  `storage` file/inline, `metadata`, `provenance`). Constructing one with a
  registered type string returns that type's data class. File artifacts
  implement `__fspath__`, so `Path(artifact)` works.
- **`ArtifactRegistry` / `ArtifactType`** — the semantic type catalog with
  parent types (e.g. `structure.protein` → `structure`) and validators, built
  from the data classes.
- **Contracts** — `PortSpec`, `InputSpec`, `OutputSpec`, `ConfigField`,
  `ConfigSpec`. Ports declare accepted data classes (or type strings), formats
  and cardinality; config specs give strict, defaulted method settings.
- **`ConverterRegistry` / `ArtifactConverter`** — finds the shortest
  conversion path between representations; a converter for a parent type also
  applies to its subtypes. Core table converters are always available; RDKit,
  Gemmi, and Open Babel converters activate only when those tools are
  installed.
- **`InputResolver`** — resolves `pipeline.<name>` and `<task>.<port>`
  references to the raw upstream values.
- **`prepare_input`** — validates one artifact, converts it to what the port
  accepts, and stages it in the task's input directory. `Method.format_inputs`
  calls it for every input.

### Configuration (`rapier.config`)

- **`ConfigParser`** — parses YAML/JSON, substitutes `${VAR}` references, and
  processes includes, producing a `PipelineConfig`.
- **`PipelineConfig` / `ArtifactConfig` / `MethodConfig` / `ConnectionConfig` /
  `PoolingRuleConfig`** — typed dataclasses representing a parsed configuration.
- **`ConfigValidator`** — structural validation, connection checks, circular
  dependency detection, pooling-rule validation, and contract checks (port
  compatibility, conversion paths, strict configs, and any method-level
  `validate_bindings` hook).
- **templates** — helpers that programmatically build common topologies
  (serial, parallel, pooling, diamond).

### SLURM Integration (`rapier.slurm`)

- **`ScriptGenerator`** — renders batch scripts from Jinja2 templates
  (`templates/job_template.sh.j2`), translating resource dicts into `#SBATCH`
  directives.
- **`SlurmClient`** — submits, cancels, and polls jobs via `sbatch`/`squeue`/
  `sacct`/`scancel`. Supports a **dry-run** mode for testing without a cluster.
- **`SlurmJob` / `SlurmJobStatus`** — a job with its status, timing, and exit
  code.
- **`DependencyBuilder`** — converts task dependencies into SLURM dependency
  strings (`afterok:123:124`, etc.).
- **`JobMonitor`** — background polling with start/complete/fail callbacks.
- **`SlurmConfig` / `PartitionInfo`** — cluster and partition discovery.
- **`ResourceParser`** — parses memory/time/CPU/GPU specifications.

### Execution Engine (`rapier.engine`)

- **`ExecutionEngine`** — the orchestrator. Runs the main loop: get ready tasks,
  submit them (bounded by `max_parallel`), poll for completion, handle results
  and failures, persist state, and resolve pipeline outputs.
- **`Scheduler`** — tracks task states and computes which tasks are ready (all
  upstream dependencies completed).
- **`TaskRunner`** — the per-task lifecycle: create directories, resolve
  bindings, have the method format and validate its inputs and environment,
  generate and submit a SLURM script running `method.get_command(task)`,
  monitor, and collect the method's typed outputs (skipped in dry runs).
- **`FailureHandler`** — applies failure policies (stop/continue/retry) and
  decides downstream consequences.
- **`ResultPooler`** — combines results from parallel branches by strategy
  (any/all/majority/best/first/custom).
- **`ParallelExecutor` / `TaskIsolation`** — thread pool for coordination work
  and per-task workspaces.
- **`ExecutionState` / `ExecutionResult`** — serializable state for resumption
  and a final report.

### Logging (`rapier.logging`)

A multi-level logging system: a central `LogManager` configures console and
file handlers with either human-readable or structured (JSON) formatters, and
component loggers (`PipelineLogger`, `TaskLogger`, `SlurmLogger`) write to
dedicated files. `FileMonitor`, `LogAggregator`, `AlertManager`, `CLIMonitor`,
and `Dashboard` provide monitoring. See the [logging guide](logging_guide.md).

### Storage (`rapier.storage`)

- **`ProjectStructure`** — the standard directory layout (`config/`, `inputs/`,
  `work/`, `results/`, `archive/`).
- **`PathManager`** — resolves all task/run paths consistently.
- **`FileOrganizer`** — stages inputs (symlink for large/immutable files, copy
  otherwise) and collects outputs.
- **`FileTracker` / `FileMetadata`** — records every created file with category
  and source task.
- **`CleanupManager` / `CleanupPolicy`** — applies cleanup policies
  (minimal/standard/aggressive) while preserving important outputs; can
  archive runs.
- **`Workspace`** — a facade combining the above for one run.
- **`validators`** — file-format checks (SDF, PDB, CSV, JSON, ...).

The engine does not use `rapier.storage` directly: `TaskRunner` creates
`runs/<task_id>/{input,output,logs}` under the working directory unless a task
already has directories set. Methods write their results to
`runs/<task_id>/outputs/` (`RunContext.output_dir`), not to `output/`.

### Methods (`rapier.methods`)

A registry-backed library of concrete `Method` subclasses, with mixins for
parallelization and checkpointing. `MethodRegistry.auto_discover()` registers
every class with a `register_name`. `rapier.methods.runner` is the generic
scheduler entry point that every method uses by default; `vina_docking` and
`boltz2` submit their own manifest runners instead. See the
[Method Reference](method_reference.md).

### Workflow builder (`rapier.workflow_builder`)

A Flask app (`python -m rapier.workflow_builder`, needs the `ui` extra) with a
vanilla-JS canvas in `static/`. It has no type logic of its own:

- **`catalog.build_catalog()`** — describes every registered method's ports and
  config fields, plus every data type (described by its class docstring), and
  precomputes socket compatibility with the same artifact and converter rules
  the validator uses. Methods may add `ui_hints` to shape the inspector. A
  subclass that only renames `register_name`/`display_name` is listed as an
  alias of its parent.
- **`graph`** — converts between the canvas graph JSON and pipeline dicts/YAML
  (`graph_to_pipeline_dict`, `pipeline_dict_to_graph`, `graph_to_yaml`,
  `yaml_to_graph`). Canvas positions round-trip through a top-level `ui.layout`
  key. `validate_graph` runs `ConfigValidator` and maps errors back to nodes.
- **`demo_methods`** — mock methods for exploring the UI. They live outside
  `rapier.methods`, so auto-discovery never registers them; the server adds them
  unless started with `--no-demo`.
- **`server.create_app()`** — JSON endpoints `/api/catalog`, `/api/validate`,
  `/api/export`, `/api/import`, and `/api/examples`. It binds to localhost by
  default and has no authentication.

## Data flow

1. **Parse**: `ConfigParser` reads YAML, typed top-level artifacts, port bindings, and method configs into `PipelineConfig`.
2. **Validate**: `ConfigValidator` checks artifact declarations, strict method configs, port compatibility, available converter paths, and graph structure.
3. **Build**: `Pipeline.from_config` instantiates each registered method as `method_class(name=<id>, config=<config>)` and creates control plus data-reference connections.
4. **Graph**: `Pipeline.to_execution_graph` yields an `ExecutionGraph`.
5. **Schedule**: the `Scheduler` repeatedly yields ready tasks.
6. **Run**: `TaskRunner` uses `InputResolver` to resolve references, lets the method format its inputs (wrap, validate, convert, stage under `input/normalized/`) and validate inputs and environment, then generates and submits the SLURM script. The job runs `execute` (`format_inputs` → `validate_inputs` → `run` → `format_outputs`) through the generic runner or the method's own runner.
7. **Monitor**: the engine polls job status and, on completion, takes the method's `collect_outputs` (typed, validated, provenance-tagged artifacts) as `task.outputs`.
8. **Pool**: at join points, `ResultPooler` combines branch results.
9. **Handle failures**: `FailureHandler` enforces the configured policy.
10. **Finalize**: configured pipeline outputs are resolved into `ExecutionResult.pipeline_outputs`, state is saved, and a report is generated.

## Design decisions

- **Specification-based contracts** make method requirements explicit and
  self-documenting, and let the platform validate before spending compute.
- **Typed artifacts with converters** let methods declare what they accept
  rather than which upstream method they follow; the platform bridges formats.
- **DAG-based scheduling** (NetworkX) gives correct ordering and cycle
  detection for arbitrary topologies, including parallel branches and pooling.
- **Dry-run everywhere** allows the full pipeline to be exercised without a
  cluster, which is what makes the test suite fast and hermetic.
- **Separation of definition (Method) and runtime (Task)** keeps methods
  stateless and reusable while tasks carry execution state.
- **Registry + auto-discovery** lets methods opt in with a `register_name`
  attribute and be loaded dynamically from configuration.
- **Server-side compatibility for the UI** keeps one source of truth for type
  rules.

## See also

- [Pipeline Configuration Reference](pipeline_configuration.md)
- [Method Development Guide](method_development_guide.md)
- [API Reference](api_reference.md)
