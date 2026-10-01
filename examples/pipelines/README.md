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
| `08_vina_dock_and_filter.yaml` | `Vina → molecule filter` | Filter docked poses by score and binding position, keep the best per ligand |

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
Runs Vina and Smina in parallel on the same prepared ligands and pools the
results with the `best` strategy (lowest binding energy wins). This is the
canonical `[A, B] → Pool → C` topology.

### 05 — Failure Handling
A serial pipeline where the docking step is allowed to fail (`continue`) without
stopping the run, and the scoring step retries up to 3 times (`retry`).

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

### 08 — Vina Docking and Filtering
Docks a library with Vina, then `molecule_filter` joins `docking_scores` onto
the poses and keeps those scoring ≤ -7 kcal/mol with `rmsd_lb` ≤ 2 Å and at
least 80% of heavy atoms inside the docking box. It keeps the best pose per
ligand and the top 20 overall. Outputs the ranked SDF (annotated with scores),
a score table, and a JSON report of rejection reasons. Requires the same tools
as example 03; the filter itself is pure Python.

## Writing your own

Copy any example as a starting point, build one visually in the workflow
builder (`python -m rapier.workflow_builder`), or see:

- [Pipeline Configuration Reference](../../docs/pipeline_configuration.md)
- [Method Reference](../../docs/method_reference.md)
- [Getting Started](../../docs/getting_started.md)
