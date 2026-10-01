# Method Reference

Documentation for all built-in methods. Each method has a registered `type`
(used in pipeline configs), input/output specifications, environment
requirements, and parameters.

To list available methods programmatically:

```python
from rapier.methods import MethodRegistry
registry = MethodRegistry()
registry.auto_discover()
print(registry.list_available())
```

## Summary

| Type | Class | Steps | Parallelizable | Purpose |
|------|-------|-------|----------------|---------|
| `ligand_filter` | `LigandFilter` | 1 | No | Filter molecules by properties |
| `molecule_filter` | `MoleculeFilter` | 1 | No | Gate candidates between rounds by scores and binding position |
| `similarity_search` | `SimilaritySearch` | 1 | No | Fingerprint similarity search |
| `ligand_prep` | `LigandPreparation` | 1 | No | 3D conformer generation, protonation |
| `protein_prep` | `ProteinPreparation` | 1 | No | Receptor preparation, PDBQT conversion |
| `vina_docking` | `VinaDocking` | 2 | Yes | AutoDock Vina docking |
| `smina_docking` | `SminaDocking` | 1 | Yes | Smina docking with custom scoring |
| `boltz2` | `Boltz2Prediction` | 1 | No | Boltz-2 structure and affinity prediction |

---

## ligand_filter

Filter molecules by drug-likeness (Lipinski's Rule of Five and related).

- **Inputs**: `ligands` — an SDF file of molecules
- **Outputs**: `filtered_ligands` (SDF), `statistics` (JSON)
- **Environment**: Python, RDKit
- **Resources (default)**: 1 CPU, 4 GB, 1 h

### Parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| `mw_min` / `mw_max` | 0 / 500 | Molecular weight bounds (Da) |
| `logp_min` / `logp_max` | -5 / 5 | LogP bounds |
| `hba_max` | 10 | Max hydrogen-bond acceptors |
| `hbd_max` | 5 | Max hydrogen-bond donors |
| `tpsa_max` | 140 | Max topological polar surface area (Å²) |
| `rotatable_bonds_max` | 10 | Max rotatable bonds |

### Example

```yaml
- id: filter
  type: ligand_filter
  config:
    mw_max: 500
    logp_max: 5
```

---

## molecule_filter

Filters docked or predicted poses using upstream results rather than
physicochemical properties. Pure Python; no RDKit required.

- **Inputs:** `molecules` (molecule.collection/sdf, required), `scores`
  (table csv/tsv/json, optional, one or more), `binding_site` (box, optional),
  `reference_ligand` (sdf, optional)
- **Outputs:** `filtered_molecules` (sdf, ranked, annotated with scores and
  metrics), `filtered_scores` (csv), `report` (json: counts and rejection reasons)

Score rows are joined to poses by molecule name (`id_column`, default `ligand`;
names are compared after Vina-style sanitizing). With several rows per name
(e.g. Vina modes) the k-th pose pairs with the k-th row, ordered by `mode`; a
single row applies to every pose of that molecule.

### Criteria

`criteria` is a list of `{column, op, value}` rules combined with `combine:
all|any`. `op` is one of `<`, `<=`, `>`, `>=`, `==`, `!=`, `between` (`[low,
high]`), `in`, `not_in` (lists) or `exists`. `column` may be a score column, an
SDF data property, or a computed metric:

| Metric | Needs |
|--------|-------|
| `heavy_atoms`, `pose_rank` | — |
| `centroid_distance`, `fraction_in_box`, `centroid_in_box` | `binding_site` |
| `reference_distance` | `reference_ligand` |

The validator reports an error when a rule or `position_mode` needs an input
port that is not connected. Poses missing a rule's column are rejected unless
`on_missing: pass`.

### Parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| `position_mode` | `none` | `centroid_in_box`, `all_atoms_in_box`, or `fraction_in_box` |
| `min_fraction_in_box` | 0.8 | Threshold for `fraction_in_box` |
| `box_padding` | 0.0 | Å added to each box half-width |
| `rank_by` / `ascending` | `""` / true | Ranking column (lower is better by default); required for a gate |
| `top_percent` | 0 | Admit at most this % of candidates (0 = no cap) |
| `percent_of` | `input` | `input`: % of candidates that entered; `passed`: % of those passing the criteria |
| `top_n` | 0 | Admit at most N candidates (0 = no cap); with `top_percent`, the tighter cap wins |
| `limit_by` | `molecules` | Count candidates as unique molecules (ranked by their best pose) or as poses |
| `best_pose_only` | true | Pass on only each admitted molecule's best pose |
| `annotate` | true | Write scores and metrics into the output SDF |
| `fail_if_empty` | false | Fail when nothing passes |

### Gating between screening rounds

The filter is designed to sit between rounds of a screening funnel. Criteria
remove poses first; survivors are then ranked by `rank_by` and capped. A cap of
`top_percent: 1` admits `ceil(1% × entered)` molecules — 100 of a 10,000-molecule
library — regardless of how many survived the criteria (use `percent_of:
passed` for a share of the survivors instead). Candidates are counted per
molecule, so nine Vina modes of one ligand use one slot, and only its best pose
is passed on so the next round does not re-process duplicates. The report's
`gate` section records `entered`, `limit`, `admitted` and `admitted_percent`.
Set `fail_if_empty: true` to stop the pipeline rather than hand the next round
no molecules.

See [`examples/pipelines/08_docking_funnel.yaml`](../examples/pipelines/08_docking_funnel.yaml):
fast docking → top 1% gate → careful re-docking → top-10 gate.

---

## similarity_search

Find molecules in a database similar to query structures using fingerprints
and Tanimoto similarity.

- **Inputs**: `queries` (SMILES or SDF), `database` (SDF)
- **Outputs**: `similar_molecules` (SDF), `similarity_scores` (CSV)
- **Environment**: Python, RDKit
- **Resources (default)**: 4 CPUs, 8 GB, 2 h

### Parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| `fingerprint_type` | `morgan` | `morgan`, `maccs`, or `rdkit` |
| `radius` | 2 | Morgan fingerprint radius |
| `n_bits` | 2048 | Fingerprint bit length |
| `similarity_threshold` | 0.7 | Minimum Tanimoto similarity (0–1) |
| `max_results` | 100 | Max results per query |

---

## ligand_prep

Prepare ligands for docking: generate 3D conformers, add hydrogens, minimize.

- **Inputs**: `ligands` (SMILES or 2D SDF)
- **Outputs**: `prepared_ligands` (3D SDF), `preparation_log` (JSON)
- **Environment**: Python, RDKit, Open Babel
- **Resources (default)**: 2 CPUs, 4 GB, 2 h

### Parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| `ph` | 7.4 | Protonation pH |
| `num_conformers` | 1 | Conformers to generate |
| `energy_minimize` | true | Whether to minimize |
| `force_field` | `UFF` | `UFF` or `MMFF` |

---

## protein_prep

Prepare protein receptors: clean structure, add hydrogens, assign charges,
convert to PDBQT.

- **Inputs**: `receptor` (PDB)
- **Outputs**: `prepared_receptor` (PDB/PDBQT), `preparation_log` (JSON)
- **Environment**: Python, Open Babel, PDB2PQR
- **Resources (default)**: 1 CPU, 4 GB, 30 m

### Parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| `ph` | 7.4 | Protonation pH |
| `remove_waters` | true | Strip water molecules |
| `remove_hetero` | false | Strip heteroatoms |
| `add_charges` | true | Add Gasteiger charges |
| `output_format` | `pdbqt` | `pdb` or `pdbqt` |

---

## vina_docking

Molecular docking with [AutoDock Vina](https://github.com/ccsb-scripps/AutoDock-Vina)
1.2.x. The scheduler runs one
deterministic task workflow: a PDB receptor is prepared with Meeko, each SDF
record is converted to PDBQT with Meeko, Vina docks each ligand, and Meeko
exports the PDBQT poses back to SDF. A PDBQT receptor is accepted directly.
The workflow is parallelizable across ligand chunks.

- **Inputs**: `receptor` (PDB, CIF, or PDBQT), `ligands` (SDF), optional
  normalized `binding_site` (`box` with `center` and `size`)
- **Outputs**: `docked_poses` (SDF), `docking_scores` (CSV)
- **Environment**: `vina`, `mk_prepare_receptor.py`,
  `mk_prepare_ligand.py`, `mk_export.py`, and Python. CIF receptors also need
  the `gemmi` Python package; Meeko receptor preparation also needs ProDy.
- **Resources (default)**: 4 CPUs, 8 GB, 2 h

### Parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| `box_center` | `[0, 0, 0]` | Docking box center `[x, y, z]` (Å) |
| `box_size` | `[20, 20, 20]` | Docking box size `[dx, dy, dz]` (Å) |
| `exhaustiveness` | 8 | Search exhaustiveness (1–32) |
| `num_modes` | 9 | Binding modes to generate |
| `energy_range` | 3 | Max energy difference between modes (kcal/mol) |
| `scoring` | `vina` | Vina scoring function (`vina` or `vinardo`) |
| `cpu` | allocated CPUs | Vina worker count; omitted uses the task allocation |
| `seed` | unset | Random seed for reproducible docking |
| `min_rmsd` | unset | Minimum RMSD between reported modes |
| `verbosity` | 1 | Vina log verbosity (`0`–`2`) |
| `allow_partial_failures` | `true` | Keep successful ligand results when another ligand fails |

The legacy `box_center` and `box_size` configuration fields remain available
for v1 callers. In normalized pipelines, `binding_site` takes precedence.
Each score row contains the ligand name, mode, affinity (kcal/mol), lower and
upper RMSD, scoring function, and exported pose file. Failed ligand names and
their error messages are retained in `outputs/vina_run.json` and in output
artifact metadata.

### Example

```yaml
- id: dock
  type: vina_docking
  inputs:
    receptor: pipeline.receptor
    ligands: pipeline.ligands
    binding_site: pipeline.binding_site
  config:
    exhaustiveness: 8
    num_modes: 9
    seed: 12345
```

---

## smina_docking

Docking with Smina (a Vina fork with extra scoring functions). **Single-step**
— handles PDB receptors directly. Parallelizable across ligands.

- **Inputs**: `receptor` (PDB or PDBQT), `ligands` (SDF)
- **Outputs**: `docked_poses` (SDF), `docking_scores` (CSV)
- **Environment**: Smina, Open Babel, Python
- **Resources (default)**: 4 CPUs, 8 GB, 2 h

### Parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| `box_center` | `[0, 0, 0]` | Docking box center `[x, y, z]` (Å) |
| `box_size` | `[20, 20, 20]` | Docking box size `[dx, dy, dz]` (Å) |
| `exhaustiveness` | 8 | Search exhaustiveness |
| `num_modes` | 9 | Binding modes |
| `scoring` | `vina` | `vina`, `vinardo`, or `ad4` |
| `minimize` | true | Energy-minimize poses |

---

## boltz2

Biomolecular complex prediction with [Boltz-2](https://github.com/jwohlwend/boltz).
The method creates the official Boltz YAML schema, runs `boltz predict`, and
normalizes the predicted structures and confidence/affinity JSON files.

- **Inputs**:
  - `proteins`: zero or more `sequence.protein` artifacts (`inline` or FASTA)
  - `dna`: zero or more `sequence.dna` artifacts
  - `rna`: zero or more `sequence.rna` artifacts
  - `ligands`: zero or more molecule artifacts (`smiles` or SDF); CCD ligands
    can be supplied with artifact metadata `ccd`
  - `templates`: optional PDB/CIF structure artifacts
  - `msas`: optional `.a3m` or CSV file artifacts, matched by `chain_id`
  - `constraints`: optional JSON artifact containing Boltz `bond`, `pocket`, or
    `contact` constraint entries

Entity IDs are read from artifact metadata `chain_id`. Use `chain_ids` for a
single sequence that represents multiple identical chains. Polymer metadata
may also contain Boltz `modifications`, `cyclic`, and `msa` fields.
- **Outputs**:
  - `predicted_structures`: one normalized `structure.complex` artifact per
    diffusion sample
  - `confidence_scores`: Boltz confidence JSON artifacts
  - `affinity_scores`: optional Boltz affinity JSON artifacts
  - `prediction_arrays`: optional PAE/PDE/pLDDT `.npz` artifacts
  - `run_summary`: Rapier execution summary JSON
  - `vina_receptor`: optional receptor-only `structure.protein/pdb` artifact
    generated when `emit_docking_inputs` is enabled
  - `docking_box`: optional inline `binding_site/box` covering all predicted
    ligand coordinates when `emit_docking_inputs` is enabled
- **Environment**: `boltz` and Python. Docking handoff generation also requires
  Gemmi. Install `boltz[cuda]` for NVIDIA GPUs.
- **Resources (default)**: 1 GPU, 8 CPUs, 32 GB, 12 h

### Parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| `accelerator` | `gpu` | `gpu`, `cpu`, or `tpu` |
| `devices` | 1 | Number of devices |
| `use_msa_server` | `true` | Generate MSA through the configured server |
| `single_sequence_mode` | `false` | Use `msa: empty` when no MSA is available |
| `use_potentials` | `false` | Enable inference-time physical potentials |
| `recycling_steps` | 3 | Recycling iterations |
| `sampling_steps` | 200 | Diffusion sampling steps |
| `diffusion_samples` | 1 | Number of structure samples |
| `output_format` | `mmcif` | `mmcif` or `pdb` |
| `affinity_binder` | unset | Ligand chain ID for Boltz affinity prediction |
| `override` | `true` | Recompute existing predictions in the output directory |
| `emit_docking_inputs` | `false` | Select the highest-confidence complex and emit a receptor plus Vina box |
| `docking_box_padding` | 8.0 | Ångström padding added around the union of predicted ligand atom bounds |

### Example

```yaml
inputs:
  protein:
    type: sequence.protein
    format: inline
    value: MKTAYIAKQRQISFVKSHFSRQ
    metadata:
      chain_id: A
  ligand:
    type: molecule
    format: smiles
    value: CCO
    metadata:
      chain_id: B

methods:
  - id: predict
    type: boltz2
    inputs:
      proteins: pipeline.protein
      ligands: pipeline.ligand
    config:
      accelerator: gpu
      use_msa_server: true
      affinity_binder: B
      diffusion_samples: 2
```

For a complete normalized Boltz-2 → Vina workflow, see
`examples/pipelines/07_boltz2_vina.yaml`. The example prepares the original
SMILES into 3D SDF ligands, then Vina consumes `vina_receptor` and `docking_box`
directly while retaining its own independent docking config.

---

## Writing your own method

See the [Method Development Guide](method_development_guide.md) and the
template at [`examples/methods/custom_method_template.py`](../examples/methods/custom_method_template.py).
