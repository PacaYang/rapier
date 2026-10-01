# Getting Started

This tutorial walks you through your first Rapier pipeline. By the end you'll
understand how to write a configuration, validate it, and read the outputs.

## Prerequisites

Make sure Rapier is installed (see [Installation](installation.md)):

```bash
python3 -c "import rapier; print(rapier.__version__)"
```

## 1. What is a pipeline?

A Rapier pipeline is a directed graph of **methods**. Each method is a single
processing step (filter molecules, dock ligands, score results). Connections
between methods define the order of execution and how data flows.

Pipelines are described in YAML. Here is the smallest possible one:

```yaml
name: my_first_pipeline
description: Filter a molecule library
version: 1.0.0

inputs:
  ligands:
    type: molecule.collection
    format: sdf
    source: ligands.sdf

methods:
  - id: filter
    type: ligand_filter
    inputs:
      ligands: pipeline.ligands
    config:
      mw_max: 500
    resources:
      cpus: 1
      memory: 2GB
      time_limit: 30m
```

This pipeline has one method: a `ligand_filter` that keeps molecules with a
molecular weight at or below 500.

## 2. Create your first pipeline

Save the YAML above as `my_pipeline.yaml`. Or start from a shipped example:

```bash
cp examples/pipelines/01_ligand_filtering.yaml my_pipeline.yaml
```

## 3. Validate it

Always validate before running. The validator checks structure, connections,
method references, and detects cycles:

```bash
python3 scripts/validate_config.py my_pipeline.yaml --build
```

You should see:

```
✓ Configuration is valid: my_first_pipeline
    Methods: 1
    Connections: 0
    Built pipeline: Pipeline(name=my_first_pipeline, tasks=1, connections=0)
```

## 4. Add a second step

Real workflows chain methods. Let's filter, then run a similarity search.
Methods are connected by referencing their `id`s:

```yaml
name: filter_then_search
version: 1.0.0

inputs:
  ligands:
    type: molecule.collection
    format: sdf
    source: ligands.sdf
  queries:
    type: molecule.collection
    format: smiles
    source: queries.smi

methods:
  - id: filter
    type: ligand_filter
    inputs:
      ligands: pipeline.ligands
    config:
      mw_max: 500

  - id: search
    type: similarity_search
    inputs:
      queries: pipeline.queries
      database: filter.filtered_ligands
    config:
      fingerprint_type: morgan
      similarity_threshold: 0.7

outputs:
  hits: search.similar_molecules
```

The `filter.filtered_ligands` binding routes one named output into the search
method and automatically creates the execution dependency. Use `connections`
only when you need an additional control dependency that carries no artifact.

## 5. Run a pipeline programmatically

You can build and inspect a pipeline from Python:

```python
from rapier.config.parser import ConfigParser
from rapier.core.pipeline import Pipeline

config = ConfigParser().parse_file("my_pipeline.yaml")
pipeline = Pipeline.from_config(config)

print(pipeline)                         # Pipeline(name=..., tasks=..., ...)
print(pipeline.get_downstream_tasks("filter"))
```

To execute on a cluster, use the `ExecutionEngine`:

```python
from rapier.engine.executor import ExecutionEngine

engine = ExecutionEngine(max_parallel=4, dry_run=True)   # dry_run simulates SLURM
result = engine.execute(pipeline)
print(result.generate_report())
```

Set `dry_run=False` on a SLURM-equipped machine to submit real jobs.

## 6. Understand the outputs

After execution, Rapier produces:

- **Per-task working directories** containing inputs, outputs, logs, and the
  generated SLURM script
- **A results directory** with the important final outputs promoted out of the
  working tree
- **An execution report** summarizing which tasks completed, failed, or were
  skipped

The `ExecutionResult` object gives you programmatic access:

```python
print(result.success)            # True/False
print(result.completed_tasks)    # ['filter', 'search']
print(result.get_task_result("filter").outputs)
```

## Next steps

- [Pipeline Configuration Reference](pipeline_configuration.md) — every option explained
- [Method Reference](method_reference.md) — all built-in methods
- [Method Development Guide](method_development_guide.md) — write your own methods
- [Architecture](architecture.md) — how the platform works under the hood
