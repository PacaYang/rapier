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
pip install -e .            # installs networkx, pyyaml
pip install jinja2          # needed for SLURM script generation
```

### RDKit import fails

RDKit is best installed via conda:

```bash
conda install -c conda-forge rdkit
```

Methods that need RDKit (`ligand_filter`, `similarity_search`, `ligand_prep`)
report this clearly in `validate_environment()`.

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

### "Pooling rule for method '...' but it has 1 upstream connection"

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

### "AutoDock Vina (vina) or Meeko tool not found"

The `vina_docking` environment validation failed. Install AutoDock Vina and
Meeko (see [Installation](installation.md)) and ensure `vina`,
`mk_prepare_receptor.py`, `mk_prepare_ligand.py`, and `mk_export.py` are on
`PATH`. The `vina` Python bindings do not replace the executable. On clusters,
load the appropriate module or activate the conda environment in the method's
`modules`/`conda_env` settings.

### Method runs locally but fails on the cluster

The compute node environment differs from the login node. Declare the needed
`modules` and/or `conda_env` so they're set up inside the SLURM script.

## File and permission errors

### "Input file not found"

Paths in the config are resolved relative to the project's `inputs/` directory
(or used as-is if absolute). Use `${PROJECT_DIR}/...` for project-relative
paths, and confirm the file exists.

### Outputs not appearing in results/

Only files marked important (or matching the method's declared outputs) are
promoted to `results/`. Check the task's `output/` directory directly, and
verify the method populated `task.outputs`.

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
