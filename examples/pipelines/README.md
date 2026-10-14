# Example Pipelines

This directory contains ready-to-run example pipeline configurations that
demonstrate common virtual screening workflows. Each `.yaml` file is a complete,
valid Rapier pipeline.

## Validating an example

Before running, validate (and optionally build) any configuration:

```bash
python scripts/validate_config.py examples/pipelines/01_ligand_filtering.yaml --build
```

## Opening an example in the workflow builder

Every example can be viewed and edited as a node graph:

```bash
pip install -e ".[ui]"
python -m rapier.workflow_builder
```

Pick a file from the *Example* menu (or use *Import YAML*), edit it on the
canvas, and *Export YAML* to save the result. Relative `source:` paths are
resolved against this directory during validation, exactly as
`validate_config.py` does. See the [main README](../../README.md#workflow-builder)
for details.

## The examples

| File | Topology | Demonstrates |
|------|----------|--------------|
| `01_ligand_filtering.yaml` | single method | The simplest pipeline — property filtering with `ligand_filter` |
| `02_similarity_screening.yaml` | `filter → similarity` | A two-step serial pipeline; fingerprint similarity search |
| `03_vina_docking.yaml` | `prep → dock → filter` | A full docking workflow with a multi-step Vina method |
| `04_parallel_docking.yaml` | `prep → [vina, smina] → pool` | Parallel branches with result pooling (consensus docking) |
| `05_failure_handling.yaml` | serial with mixed policies | Per-method `stop`, `continue`, and `retry` failure policies |
| `06_boltz2_prediction.yaml` | `protein + ligand → Boltz-2` | Complex structure and affinity prediction |
| `07_boltz2_vina.yaml` | `Boltz-2 → ligand prep → Vina` | Derive a receptor and ligand-centered box from a predicted complex, then dock |
| `08_docking_funnel.yaml` | `fast dock → gate → careful dock → gate` | Two-round screening funnel gated by `molecule_filter` (top 1%, then top 10) |
| `simple_serial.yaml` | `A → B → C` | Legacy v1 style: placeholder method types, paths in `config` |
| `parallel_pooling.yaml` | `[A, B] → Pool → C` | Legacy v1 style: parallel branches with a `best` pooling rule |
| `multi_step_method.yaml` | five-step DAG | Legacy v1 style: a multi-step Vina workflow sketch |

Examples 01–03 and 06–08 use the normalized form: top-level `inputs`, port
bindings, and `outputs`. Bindings create the dependencies, so 08 needs no
`connections`. 04 and 05 use registered methods but rely only on `connections`
and v1 `config` fields such as `box_center`. The three unnumbered files use
method types that aren't registered (`filtering`, `vina_dock`, …). They pass
validation because they declare no port bindings, but they only show topology
and won't run real methods. Every file validates and builds with
`validate_config.py --build`.

### 01 — Ligand Filtering
A single `ligand_filter` method applying Lipinski's Rule of Five. The best
starting point for understanding the configuration format.

### 02 — Similarity Screening
Filters a library, then runs a Morgan-fingerprint Tanimoto similarity search
against query molecules. Shows how outputs flow from one method to the next.

### 03 — Vina Docking
Prepares ligands, docks them with AutoDock Vina (a two-step method that also
prepares the receptor internally), and filters the hits. Requires the Vina
executable, Meeko's preparation tools, and RDKit to actually execute.

### 04 — Parallel Docking
Runs Vina and Smina in parallel after ligand prep, and declares a `best`
pooling rule (lowest binding energy) on `consensus`. This is the canonical
`[A, B] → Pool → C` topology. The engine validates and records pooling rules,
but doesn't apply them yet. See
[Pooling rules](../../docs/pipeline_configuration.md#pooling-rules).

### 05 — Failure Handling
A serial pipeline. The docking step is allowed to fail (`continue`), which
skips everything downstream of it instead of stopping the run. The scoring
step retries up to 3 times (`retry`). The policies are set per method, since
the top-level `failure_policy` isn't inherited.

### 06 — Boltz-2 Prediction
Predicts a protein-ligand complex and optional affinity from normalized sequence
and molecule artifacts.

### 07 — Boltz-2 to Vina
Predicts a complex, selects its highest-confidence sample, strips the predicted
ligands to create a receptor, and derives one shared ligand-centered search box.
The original SMILES are then converted to deterministic 3D conformers before
Vina docks them using that receptor and box. Requires
Boltz-2, Gemmi, RDKit, AutoDock Vina, Meeko, and network access when the remote
MSA server is enabled.

### 08 — Docking Funnel
Round 1 docks the whole library cheaply (`exhaustiveness: 4`). The first
`molecule_filter` gate joins `docking_scores` onto the poses. It keeps poses
scoring ≤ -6 kcal/mol that have at least 80% of their heavy atoms inside the
box, ranks molecules by their best pose, and admits the top 1% of the
molecules that entered, one pose each. `fail_if_empty` stops the run if nothing
survives. Round 2 re-docks the survivors at `exhaustiveness: 32`, and a second
gate keeps the 10 best molecules whose centroid lies in the box. The outputs
are the hits, their score table, and a JSON report from each gate. It needs
the same tools as example 03. The filter itself is pure Python.

### Legacy examples
`simple_serial.yaml`, `parallel_pooling.yaml`, and `multi_step_method.yaml`
date from before the artifact contracts. They show connection, pooling, and
failure-policy topology with placeholder method types, and use `${PROJECT_DIR}`
paths inside `config`. Use the numbered examples as templates for real runs.

## Writing your own

Copy any example as a starting point, build one visually in the workflow
builder (`python -m rapier.workflow_builder`), or see:

- [Pipeline Configuration Reference](../../docs/pipeline_configuration.md)
- [Method Reference](../../docs/method_reference.md)
- [Getting Started](../../docs/getting_started.md)
