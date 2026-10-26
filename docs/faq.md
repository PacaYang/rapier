# Frequently Asked Questions

## General

### What is Rapier?

Rapier is a modular, SLURM-based virtual screening platform. It runs
customizable molecule-discovery pipelines (filtering, docking, scoring, etc.)
on an HPC cluster, with dependency management, failure handling, monitoring, and
file organization built in.

### Do I need a SLURM cluster to use it?

No, not for development. Rapier has a **dry-run** mode that simulates job
submission, so you can build, validate, and exercise pipelines locally. A dry
run executes no methods, so tasks bound to an upstream output fail there; run
a method locally with `method.execute(task)` instead. You only need SLURM to
run real compute jobs.

### What molecule formats are supported?

The built-in methods work with SDF and SMILES (molecules), PDB/PDBQT
(receptors), and CSV score tables. Boltz-2 also takes protein sequences and
writes PDB/mmCIF. Format conversion is handled by registered converters: core
table converters (CSV/TSV/JSON) plus RDKit, Gemmi, and (as a fallback) Open
Babel, when they are installed.

## Pipelines

### How do I run a pipeline?

1. Write a YAML configuration (see [Getting Started](getting_started.md)).
2. Validate it: `python scripts/validate_config.py my_pipeline.yaml --build`.
3. Build and execute it with the `ExecutionEngine`.

There is no `rapier` command-line runner; execution goes through Python.

### Can I design pipelines visually?

Yes. `pip install -e ".[ui]"`, then `python -m rapier.workflow_builder` and
open http://127.0.0.1:8765. It validates and exports YAML; it does not run
pipelines.

### How do I run two methods in parallel?

Give a downstream method multiple `from` sources in a connection, or connect one
upstream method to several downstream methods. Independent branches run
concurrently (up to `max_parallel`).

```yaml
connections:
  - from: prep
    to: vina
  - from: prep
    to: smina
```

Here `vina` and `smina` run in parallel after `prep`.

### How do I combine results from parallel branches?

Use a **pooling rule** on the joining method. See
[Pipeline Configuration → Pooling Rules](pipeline_configuration.md#pooling-rules).

### What's the difference between `serial` and `parallel` connection types?

The `type` is mostly descriptive — execution order is determined by the graph
structure, not the label. `parallel` is conventionally used when multiple
sources feed one target (a join), and `serial` for one-to-one edges.

### Can a method fail without killing the whole pipeline?

Yes. Set `failure_policy: continue` on that method. The failed task and its
downstream tasks are skipped, but independent branches keep running. Use
`retry` (with `retry_count`) to re-attempt transient failures.

## Methods

### What methods are built in?

`ligand_filter`, `molecule_filter`, `similarity_search`, `ligand_prep`,
`protein_prep`, `colabfold_msa`, `vina_docking`, `smina_docking`, `boltz2`,
`af3`, `drug_lamp`, and `nesso` (with `boltz2_prediction` as a Boltz alias).
See the [Method Reference](method_reference.md).

### How do I write my own method?

Subclass `rapier.Method`, declare typed `inputs`/`outputs`, `config_fields`
and a `register_name`, then implement `check_dependencies(inputs, ctx)`,
`run(inputs, ctx)` and `validate_outputs(inputs, outputs, ctx)` after examining
the input/output contract. The base class checks prerequisites on the execution
host and passes all normalized outputs to the validator once. Registered
methods must provide concrete hooks, including through inheritance. The generic
runner applies this lifecycle locally and on SLURM. See
the [Method Development Guide](method_development_guide.md) and the
[template](../examples/methods/custom_method_template.py).

### How does a multi-stage method like `vina_docking` work?

It runs every stage (receptor preparation, ligand conversion, docking, pose
export) inside one `run` call, in a single SLURM job. There is no separate
multi-step base class; a method that needs several stages runs them in order
itself.

### How do methods connect to each other?

Through typed ports. Every port carries a `rapier.data` type, and a subtype
fits wherever its parent does: Vina's `docked_poses` (`molecule.poses`) feed
any `molecule.collection` port, and every score table (`table.scores`) is
keyed by molecule `id` (and `pose`), so `molecule_filter` can join scores from
docking, `ligand_filter` properties or similarity search onto the poses.

### What does "parallelizable" mean for a method?

A method that mixes in `ParallelizableMethod` declares how its input could be
split into chunks (`split_inputs`) and the results merged (`merge_outputs`).
The engine does not split work automatically yet; run several method entries
side by side in the pipeline instead.

## Execution and SLURM

### How do I limit how many jobs run at once?

Set `max_parallel` on the `ExecutionEngine`.

### How does Rapier know when a job finishes?

The `SlurmClient` polls `squeue`/`sacct`. The engine checks status each loop
iteration (`poll_interval` seconds apart) and collects outputs on completion.

### Can I resume an interrupted pipeline?

Yes. Execution state is persisted (`ExecutionState`). Call
`engine.execute(pipeline, resume=True)` to pick up where it left off.

### Where do my SLURM scripts come from?

They're generated by `ScriptGenerator` from Jinja2 templates, using the
`resources` block of the method entry. The command is `method.get_command(task)`:
for most methods, `python -m rapier.methods.runner --task <work_dir>/task.json`,
which runs the method on the compute node and writes `outputs/outputs.json`.
The generated script (`<task_id>.sh`) is written into each task's working
directory.

## Files and outputs

### Where do outputs go?

Each task writes to its own working directory under `work/<run>/tasks/<task>/`.
Important final outputs are promoted to `results/<run>/`.

### How do I keep intermediate files from filling my disk?

The `CleanupManager` applies cleanup policies (`minimal`, `standard`,
`aggressive`). The `standard` policy keeps inputs, outputs, and logs but removes
intermediates and scripts. Runs can also be archived to compressed tarballs.

### Why is an input symlinked instead of copied?

Large files and immutable files under `inputs/` are symlinked to save space and
time; smaller files are copied. You can force copying via the `FileOrganizer`.

## Testing and development

### How do I run the tests?

```bash
scripts/run_tests.sh            # all tests
scripts/run_tests.sh unit       # just unit tests
scripts/run_tests.sh all --cov  # with coverage
```

### Why are some tests skipped?

Tests that need a real SLURM installation or external tools are skipped when
those aren't available. They carry the markers `slurm`, `rdkit`, `vina`, and
`boltz2_vina`, so you can select or exclude them:

```bash
python -m pytest -m "not slurm and not rdkit and not vina and not boltz2_vina"
```

The `boltz2_vina` test also needs `RAPIER_RUN_BOLTZ2_VINA_E2E=1` and network
access.

### How do I add a new example pipeline?

Drop a `.yaml` into `examples/pipelines/`. The test suite automatically
validates every example, so it must parse and build correctly.
