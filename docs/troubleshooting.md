# Troubleshooting

Common problems and their solutions, organized by category.

## Installation issues

### `ModuleNotFoundError: No module named 'rapier'`

The package isn't installed or isn't on your path. From the repo root:

```bash
pip install -e .
```

Or run scripts/tests from the repo root so `rapier/` is importable.

### `ModuleNotFoundError: No module named 'networkx'` / `yaml` / `jinja2`

Core dependencies are missing:

```bash
pip install -e .            # installs networkx, pyyaml, jinja2
```

### `ModuleNotFoundError: No module named 'flask'`

The workflow builder needs the `ui` extra:

```bash
pip install -e ".[ui]"
python -m rapier.workflow_builder
```

### RDKit import fails

RDKit is best installed via conda:

```bash
conda install -c conda-forge rdkit
```

Methods that need RDKit (`ligand_filter`, `similarity_search`, `ligand_prep`)
declare it in their `EnvironmentSpec`, so the task fails before submission with
"Environment validation failed: Required Python package not found: rdkit".

## Configuration errors

### "Pipeline must have at least one method"

Your `methods:` list is empty or missing. Every pipeline needs at least one
method.

### "Connection N: source/target method '...' does not exist"

A connection references a method `id` that isn't defined. Check for typos and
ensure every `from`/`to` matches a method `id`.

### "Circular dependency detected: a -> b -> a"

Your connections form a cycle. Pipelines must be acyclic (a DAG). Remove the
back-edge that closes the loop.

### "Pooling rule for method '...' but it has 1 upstream connection(s)"

Pooling only makes sense when a method has multiple inputs. Either add more
upstream connections or remove the pooling rule.

### "Method '...': ID must start with a letter"

Method ids must begin with a letter (`^[a-zA-Z][a-zA-Z0-9_-]*$`). Rename ids
that start with a digit or symbol.

### Validate before running

Catch all of the above at once:

```bash
python scripts/validate_config.py my_pipeline.yaml --build
```

## SLURM problems

### "sbatch command not found. Is SLURM installed?"

You're trying to submit real jobs on a machine without SLURM. For development,
use dry-run mode:

```python
engine = ExecutionEngine(dry_run=True)
```

### Jobs stay PENDING forever

Usually a resource request that no partition can satisfy, or a wrong partition
name. Check:

- `partition` matches a real partition (`sinfo`)
- `memory`, `cpus`, `gpus`, and `time_limit` are within partition limits
- dependencies aren't waiting on a job that failed

### Jobs fail immediately

Inspect the generated script and the job's `.err` file in the task's log
directory. Common causes: missing modules, wrong `conda_env`, or an executable
not on `PATH` on the compute node.

### Dependency string looks wrong

Use `DependencyBuilder.validate_dependency_string()` to check syntax. The
format is `<type>:<jobid>:<jobid>...`, e.g. `afterok:12345:12346`.

## Environment issues

### "AutoDock Vina (vina) not found in PATH" / "Meeko ... not found in PATH"

The `vina_docking` environment validation failed. Install AutoDock Vina and
Meeko (see [Installation](installation.md)) and ensure `vina`,
`mk_prepare_receptor.py`, `mk_prepare_ligand.py`, and `mk_export.py` are on
`PATH`. A "ProDy Python package not found" error means `pip install prody`. The `vina` Python bindings do not replace the executable. On clusters,
load the appropriate module or activate the conda environment in the method's
`modules`/`conda_env` settings.

### Method runs locally but fails on the cluster

The compute node environment differs from the login node. Declare the needed
`modules` and/or `conda_env` so they're set up inside the SLURM script.

Most methods run as `<python> -m rapier.methods.runner --task <work_dir>/task.json`,
where `<python>` is the absolute path of the interpreter that submitted the
job. The compute node needs that interpreter, Rapier, and the method's module:
the job imports the method class by module path, so a method defined in
`__main__` or a notebook can't be rebuilt there. The job's own error is in
`<work_dir>/outputs/outputs.json` and the `.err` log.

### "Failed to collect outputs: No outputs.json for task '...'; did the job run?"

The job never wrote `<work_dir>/outputs/outputs.json`: it was killed, failed
before the runner started (check the `.err` file), or never ran. If
`outputs.json` exists with `"status": "failed"`, its `error` is reported
instead.

## File and permission errors

### "Artifact file not found" / "Input file not found"

Relative `source` paths are resolved against the directory containing the YAML
file (or against `ConfigParser(project_dir=...)` if given); absolute paths are
used as-is. `${PROJECT_DIR}` and environment variables are substituted. Confirm
the file exists.

### `molecule_filter` reports `poses_without_scores` or ranks in the wrong direction

Scores join to poses by sanitized molecule `id`. Docking methods now write
`id`/`pose` columns (not `ligand`/`mode`), and `id_column` defaults to `id`;
older tables with `ligand`, `name`, `molecule`, `mol_id`, `compound` or
`title` columns still join. A config that sets `id_column: ligand` against new
Vina output still works through the fallback. If ranking goes the wrong way,
check `gate.order_source` in the report: with `order: auto` the direction
comes from the score table's declared metadata, then from well-known column
names, else ascending. Set `order: ascending|descending` explicitly for custom
columns, or bind a `table.scores` input that declares the direction.

### Outputs not appearing in results/

Only files marked important (or matching the method's declared outputs) are
promoted to `results/`. Check the task's `outputs/` directory directly
(methods write there, not to `output/`), including `outputs/outputs.json`,
and verify the method populated `task.outputs`.

### Permission denied writing to work directory

Ensure the project root is writable by your user, and that you're not pointing
the project at a read-only filesystem.

## Debugging techniques

### Turn on verbose logging

```python
from rapier.logging import initialize_logging
initialize_logging(log_directory="logs", config={"level": "DEBUG"})
```

### Run a single task's method directly

```python
status = task.method.execute(task)
print(status, task.error_message)
```

### Inspect the generated SLURM script

After `TaskRunner.submit_task`, the script is written to the task's working
directory (`<task_id>.sh`). Read it to verify directives and commands.

### Use dry-run end-to-end

```python
engine = ExecutionEngine(dry_run=True, poll_interval=0)
result = engine.execute(pipeline)
print(result.generate_report())
```

## Still stuck?

- Check the [FAQ](faq.md)
- Review the [Architecture](architecture.md) to understand component
  responsibilities
- Look at working [example pipelines](../examples/pipelines/)
