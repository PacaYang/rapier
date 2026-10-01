# Architecture

This document explains how Rapier is structured and how its components fit
together.

## Overview

Rapier is a modular, SLURM-based virtual screening platform. A user describes a
**pipeline** in YAML; the platform builds an execution graph, schedules tasks
respecting dependencies, submits them to SLURM, monitors progress, handles
failures, and organizes the resulting files.

```
   YAML config
        │
        ▼
 ┌──────────────┐   build    ┌──────────────┐  to graph  ┌────────────────┐
 │ ConfigParser │ ─────────► │   Pipeline   │ ─────────► │ ExecutionGraph │
 └──────────────┘            └──────────────┘            └────────────────┘
                                                                 │
                                                                 ▼
                                                          ┌────────────┐
                                                          │ Scheduler  │
                                                          └────────────┘
                                                                 │ ready tasks
                                                                 ▼
                            ┌──────────────┐  submit   ┌──────────────────┐
                            │ TaskRunner   │ ────────► │   SlurmClient    │
                            └──────────────┘           └──────────────────┘
                                   │ results                  │ status
                                   ▼                          ▼
                            ┌──────────────┐           ┌──────────────────┐
                            │ ResultPooler │           │ FailureHandler   │
                            └──────────────┘           └──────────────────┘
```

## Package layout

```
rapier/
├── core/        # Foundational data models
├── config/      # YAML parsing, validation, templates
├── slurm/       # SLURM integration (scripts, submission, monitoring)
├── engine/      # Execution orchestration
├── logging/     # Multi-level logging and monitoring
├── storage/     # Project structure and file lifecycle management
└── methods/     # Screening method library
```

## Components

### Core (`rapier.core`)

The foundational types, with no dependencies on other subsystems:

- **`BaseMethod`** — abstract base for all methods. Declares input/output/
  environment/resource specs and the `validate_inputs`, `validate_environment`,
  and `execute` lifecycle hooks.
- **`Task`** — a stateful runtime instance of a method execution. Holds inputs,
  outputs, status, timing, and SLURM job info.
- **`Pipeline`** — a workflow definition: tasks, connections, and pooling rules.
- **`ExecutionGraph`** — a NetworkX-backed DAG used for topological ordering,
  cycle detection, and ready-task computation.
- **Specs** (`InputSpec`, `OutputSpec`, `EnvironmentSpec`, `ResourceSpec`) —
  declarative contracts that drive validation and SLURM directive generation.

### Configuration (`rapier.config`)

- **`ConfigParser`** — parses YAML/JSON, substitutes `${VAR}` references, and
  processes includes, producing a `PipelineConfig`.
- **`PipelineConfig` / `MethodConfig` / `ConnectionConfig` / `PoolingRuleConfig`**
  — typed dataclasses representing a parsed configuration.
- **`ConfigValidator`** — structural validation, connection checks, circular
  dependency detection, and pooling-rule validation.
- **templates** — helpers that programmatically build common topologies
  (serial, parallel, pooling, diamond).

### SLURM Integration (`rapier.slurm`)

- **`ScriptGenerator`** — renders SLURM batch scripts from Jinja2 templates,
  translating `ResourceSpec`/resource dicts into `#SBATCH` directives.
- **`SlurmClient`** — submits, cancels, and polls jobs via `sbatch`/`squeue`/
  `sacct`/`scancel`. Supports a **dry-run** mode for testing without a cluster.
- **`SlurmJob`** — represents a job with its status, timing, and exit code.
- **`DependencyBuilder`** — converts task dependencies into SLURM dependency
  strings (`afterok:123:124`, etc.).
- **`ResourceParser`** — parses memory/time/CPU/GPU specifications.

### Execution Engine (`rapier.engine`)

- **`ExecutionEngine`** — the orchestrator. Runs the main loop: get ready tasks,
  submit them (bounded by `max_parallel`), poll for completion, handle results
  and failures, persist state.
- **`Scheduler`** — tracks task states and computes which tasks are ready (all
  upstream dependencies completed).
- **`TaskRunner`** — the per-task lifecycle: validate, prepare directories,
  generate and submit a SLURM script, monitor, collect outputs.
- **`FailureHandler`** — applies failure policies (stop/continue/retry) and
  decides downstream consequences.
- **`ResultPooler`** — combines results from parallel branches by strategy
  (any/all/majority/best/first/custom).
- **`ExecutionState` / `ExecutionResult`** — serializable state for resumption
  and a final report.

### Logging (`rapier.logging`)

A multi-level logging system: a central `LogManager` configures console and
file handlers with either human-readable or structured (JSON) formatters, and
component loggers (`PipelineLogger`, `TaskLogger`, `SlurmLogger`) write to
dedicated files. Aggregation, alerts, and a CLI/web dashboard provide
monitoring.

### Storage (`rapier.storage`)

- **`ProjectStructure`** — the standard directory layout (`config/`, `inputs/`,
  `work/`, `results/`, `archive/`).
- **`PathManager`** — resolves all task/run paths consistently.
- **`FileOrganizer`** — stages inputs (symlink for large/immutable files, copy
  otherwise) and collects outputs.
- **`FileTracker`** — records every created file with category and source task.
- **`CleanupManager`** — applies cleanup policies (minimal/standard/aggressive)
  to reclaim space while preserving important outputs; can archive runs.

### Methods (`rapier.methods`)

A registry-backed library of concrete screening methods built on
`SingleStepMethod` / `MultiStepMethod` base classes, with mixins for
parallelization and checkpointing. See the [Method Reference](method_reference.md).

## Data flow

1. **Parse**: `ConfigParser` reads YAML, typed top-level artifacts, port bindings, and method configs into `PipelineConfig`.
2. **Validate**: `ConfigValidator` checks artifact declarations, strict method configs, port compatibility, available adapter paths, and graph structure.
3. **Build**: `Pipeline.from_config` instantiates registered methods and creates control plus data-reference connections.
4. **Graph**: `Pipeline.to_execution_graph` yields an `ExecutionGraph`.
5. **Schedule**: the `Scheduler` repeatedly yields ready tasks.
6. **Run**: `TaskRunner` resolves normalized inputs, converts and stages accepted representations, then generates and submits the SLURM script.
7. **Monitor**: the engine polls job status and collects outputs on completion.
8. **Pool**: at join points, `ResultPooler` combines branch results.
9. **Handle failures**: `FailureHandler` enforces the configured policy.
10. **Finalize**: required outputs are normalized and provenance-tagged, configured pipeline outputs are exported, state is saved, and a report is generated.

## Design decisions

- **Specification-based contracts** make method requirements explicit and
  self-documenting, and let the platform validate before spending compute.
- **DAG-based scheduling** (NetworkX) gives correct ordering and cycle
  detection for arbitrary topologies, including parallel branches and pooling.
- **Dry-run everywhere** allows the full pipeline to be exercised without a
  cluster, which is what makes the test suite fast and hermetic.
- **Separation of definition (Method) and runtime (Task)** keeps methods
  stateless and reusable while tasks carry execution state.
- **Registry + auto-discovery** lets methods opt in with a `register_name`
  attribute and be loaded dynamically from configuration.

## See also

- [Pipeline Configuration Reference](pipeline_configuration.md)
- [Method Development Guide](method_development_guide.md)
- [API Reference](api_reference.md)
