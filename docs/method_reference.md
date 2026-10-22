# Method Reference

Documentation for all built-in methods. Each method has a registered `type`
(used in pipeline configs), typed input/output ports, environment
requirements, and a config schema.

To list available methods programmatically:

```python
from rapier.methods import MethodRegistry
registry = MethodRegistry()
registry.auto_discover()
print(registry.list_available())
print(registry.get_info("vina_docking"))  # description, class, module
```

Every built-in method is a `rapier.Method` subclass: ports exchange typed
`Artifact` objects (the data classes in `rapier.data`) and `config` is
validated strictly, so an unknown key or a value of the wrong type fails
pipeline validation. Ports are written below as `type/format`; see
[Artifact types](#artifact-types) for what each type accepts. Molecules are
identified by a sanitized `id` (the SDF title or SMILES name, with characters
outside `A-Za-z0-9_.-` replaced by `_`), and every score table is keyed by
`id`, plus `pose` for per-pose rows, so outputs of different methods join.

## Summary

| Type | Class | Parallelizable | Purpose |
|------|-------|----------------|---------|
| `ligand_filter` | `LigandFilter` | No | Filter molecules by physicochemical properties |
| `molecule_filter` | `MoleculeFilter` | No | Gate candidates between rounds by scores and binding position |
| `similarity_search` | `SimilaritySearch` | No | Fingerprint similarity search |
| `ligand_prep` | `LigandPreparation` | No | 3D conformer generation, protonation |
| `protein_prep` | `ProteinPreparation` | No | Receptor preparation, PDBQT conversion |
| `vina_docking` | `VinaDocking` | Yes | AutoDock Vina docking |
| `smina_docking` | `SminaDocking` | Yes | Smina docking with custom scoring |
| `boltz2` | `Boltz2Prediction` | No | Boltz-2 structure and affinity prediction |
| `boltz2_prediction` | `Boltz2PredictionMethod` | No | Alias of `boltz2` |
| `af3` | `AF3Prediction` | No | AlphaFold 3 structures and confidence for each ligand |
| `drug_lamp` | `DrugLAMP` | No | Drug-target interaction probabilities |
| `nesso` | `NessoPrediction` | No | Nesso-1 binding affinity and optional pair representations |

See the [AF3, DrugLAMP, and Nesso guide](predictors.md) for their input checks,
dependencies, configuration, validated output contracts, and example pipelines.

Every method runs on SLURM through `python -m rapier.methods.runner` (see
[Running on SLURM](method_development_guide.md#running-on-slurm)). Vina and Boltz2
invoke their specialized manifest helpers inside `run`.

Every registered method implements `check_dependencies(inputs, ctx)` before
execution and `validate_outputs(inputs, outputs, ctx)` after execution. The
first checks execution-host prerequisites alongside `validate_environment`;
the second receives all normalized outputs once and aggregates missing,
malformed, duplicate, unexpected and inconsistent results across ports.
Structural output errors do not prevent independent semantic checks. Only
validated output is published; scheduler receipts include `validation_version:
1`, and legacy receipts require a rerun. DAG dependencies still control task
ordering. Empty and partial results follow each method's contract, with explicit
per-input outcome metadata and occurrence keys for duplicate IDs.

Default resources below come from each method's `ResourceSpec`. They are
informational (shown in the workflow builder); SLURM jobs use the `resources`
block of the method entry in the pipeline YAML.

---

## Artifact types

Ports name one or more semantic types and, optionally, accepted formats. A
port accepts a subtype of a listed type (`structure.protein` satisfies
`structure`), and a port typed `file` accepts any file-backed artifact. When
the format does not match, Rapier looks for a converter path at validation and
staging time.

| Type | Class (`rapier.data`) | Formats | Parent |
|------|-----------------------|---------|--------|
| `sequence` | `Sequence` | `inline`, `fasta` | — |
| `sequence.protein` / `.dna` / `.rna` | `ProteinSequence` / `DnaSequence` / `RnaSequence` | `inline`, `fasta` | `sequence` |
| `molecule` | `Molecule` | `smiles`, `sdf`, `mol2`, `pdbqt`, `ccd` | — |
| `molecule.collection` | `Molecules` | `smiles`, `sdf`, `mol2`, `pdbqt`, `ccd` | — |
| `molecule.poses` | `Poses` | `sdf` | `molecule.collection` |
| `structure` | `Structure` | `pdb`, `cif`, `pdbqt` | — |
| `structure.protein` | `ProteinStructure` | `pdb`, `cif`, `pdbqt` | `structure` |
| `structure.nucleic_acid`, `structure.complex` | `NucleicAcidStructure`, `ComplexStructure` | `pdb`, `cif` | `structure` |
| `binding_site` | `BindingSite` | `box`, `residues`, `reference`, `json` | — |
| `table` | `Table` | `csv`, `tsv`, `json` | — |
| `table.scores` | `Scores` | `csv`, `tsv`, `json` | `table` |
| `report` | `Report` | `json`, `text`, `html`, `csv` | — |
| `file` | `File` | any | — |

`Poses` are docked or predicted 3D poses: records sharing an id are ranked
poses of one molecule. `Scores` are tables keyed by `id` (plus `pose`) whose
artifact metadata can declare each column's direction,
`metadata["columns"][col]["better"]` = `lower` or `higher`.

Built-in converters (used only when their tool is installed). A converter for
a parent type also applies to its subtypes: `table.scores` converts like
`table`, and `molecule.poses` sdf converts to smiles for a
`molecule.collection` port.

| Conversion | Provider |
|------------|----------|
| `table` csv ↔ tsv ↔ json | core |
| `sequence.*` inline → fasta | core |
| `molecule` / `molecule.collection` smiles ↔ sdf | RDKit |
| `structure.protein` / `.nucleic_acid` / `.complex` cif ↔ pdb | Gemmi (Open Babel fallback for cif → pdb) |
| `structure.protein` pdbqt → pdb | Open Babel |

Inline artifacts (SMILES, sequences, JSON) are written to the task's
`input/normalized/` directory before the method runs. `binding_site` artifacts
stay inline.

---

## ligand_filter

Filter molecules by drug-likeness (Lipinski's Rule of Five and related) with
RDKit descriptors.

- **Inputs**: `ligands` — `molecule.collection`, `sdf` or `smiles`
- **Outputs**:
  - `filtered_ligands` (`molecule.collection/sdf`, `outputs/filtered.sdf`):
    molecules that pass every bound. Existing SDF coordinates and properties
    are preserved, with an `input_occurrence` property added; SMILES input is
    written through RDKit without 3D coordinates.
  - `properties` (`table.scores/csv`, `outputs/properties.csv`): one row per
    input molecule with `id`, `input_occurrence`, `mw`, `logp`, `hba`, `hbd`, `tpsa`,
    `rotatable_bonds`, `passed` and `reasons` (`;`-separated failed checks, or
    `unparsable`). Bind it to a `molecule_filter` `scores` port to rank or gate
    on the properties.
  - `statistics` (`report/json`, `outputs/statistics.json`): totals and
    `failed_by_reason`
- **Environment**: Python, RDKit
- **Resources (default)**: 1 CPU, 4 GB, 1 h

Pooled validation compares every input occurrence with the property rows and
the passing `(id, input_occurrence)` inventory, checks finite typed properties
and consistent rejection reasons, and reconciles the statistics. Empty
filtered output is valid; invalid molecules remain explicit `unparsable` rows.

### Parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| `mw_min` / `mw_max` | 0 / 500 | Molecular weight bounds (Da); `mw_min > mw_max` is rejected |
| `logp_min` / `logp_max` | -5 / 5 | LogP bounds; `logp_min > logp_max` is rejected |
| `hba_max` | 10 | Max hydrogen-bond acceptors (N + O count) |
| `hbd_max` | 5 | Max hydrogen-bond donors (NH + OH count) |
| `tpsa_max` | 140 | Max topological polar surface area (Å²) |
| `rotatable_bonds_max` | 10 | Max rotatable bonds |

### Example

```yaml
- id: filter
  type: ligand_filter
  inputs:
    ligands: pipeline.ligands
  config:
    mw_max: 500
    logp_max: 5
```

---

## molecule_filter

Filters docked or predicted poses using upstream results rather than
physicochemical properties. Pure Python; no RDKit required.

- **Inputs**:
  - `molecules`: `molecule.collection/sdf`, including `molecule.poses` (required)
  - `scores`: `table` in `csv`, `tsv` or `json` (optional, one or more).
    `table.scores` inputs also contribute their declared column directions.
  - `binding_site`: `binding_site` as `box` or a `json` file holding `{center, size}` (optional)
  - `reference_ligand`: `molecule` or `molecule.collection`, `sdf` (optional;
    the first record's heavy-atom centroid is used)
- **Outputs**:
  - `filtered_molecules`: `molecule.collection/sdf` (`outputs/filtered.sdf`),
    ranked and annotated with scores and metrics. It keeps the input's class,
    so `Poses` in give `Poses` out.
  - `filtered_scores`: `table.scores/csv` (`outputs/filtered_scores.csv`), one
    row per kept pose with `id`, `pose`, `name`, `filter_rank`, scores and
    metrics. It carries the known score directions forward, so a chained gate
    can rank with `order: auto`.
  - `report`: `report/json` (`outputs/report.json`): counts, rejection
    reasons, unknown columns and the `gate` summary
- **Environment**: Python only
- **Resources (default)**: 1 CPU, 2 GB, 30 m

Score rows are joined to poses by molecule id. The key column is `id_column`
(default `id`), falling back to `id`, `ligand`, `name`, `molecule`, `mol_id`,
`compound` or `title`, so tables written with the older `ligand`/`mode`
columns still join. Both sides are compared after id sanitizing. With several
rows per id (e.g. Vina poses) the k-th pose of that molecule in the SDF pairs
with the k-th row, ordered by `pose` (or `mode`, `pose_rank`, `model`). A
single row applies to every pose of that molecule. A table with no key column
is joined row-by-row, and only when its row count matches the pose count. Pose
names come from the SDF title, or from the SDF property named in
`name_property`.

### Criteria

`criteria` is a list of `{column, op, value}` rules combined with `combine:
all|any`. `op` is one of `<`, `<=`, `>`, `>=`, `==`, `!=`, `between` (`[low,
high]`), `in`, `not_in` (lists) or `exists`. `column` may be a score column, an
SDF data property, or a computed metric (looked up in that order: metrics,
scores, properties; exact match first, then case-insensitive):

| Metric | Needs |
|--------|-------|
| `heavy_atoms`, `pose_rank` | — |
| `centroid_distance`, `fraction_in_box`, `centroid_in_box` | `binding_site` |
| `reference_distance` | `reference_ligand` |

`position_mode` always applies, whatever `combine` says. The method's
`validate_bindings` hook makes the pipeline validator report an error when a
rule, `rank_by` or `position_mode` needs an input port that is not connected.
Poses missing a rule's column are rejected unless `on_missing: pass`.

### Parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| `criteria` | `[]` | List of `{column, op, value}` rules |
| `combine` | `all` | `all`: every rule must hold; `any`: at least one |
| `on_missing` | `reject` | `reject` or `pass` a pose lacking a rule's column |
| `position_mode` | `none` | `none`, `centroid_in_box`, `all_atoms_in_box`, or `fraction_in_box` |
| `min_fraction_in_box` | 0.8 | Threshold for `fraction_in_box` |
| `box_padding` | 0.0 | Å added to each box half-width |
| `rank_by` | `""` | Ranking column; required for a gate |
| `order` | `auto` | `auto`, `ascending` (lower first) or `descending`. `auto` uses the direction the score tables declare for `rank_by`, else the well-known direction of common names (`affinity_kcal_mol` lower, `similarity`/`confidence_score`/`CNNscore` higher, ...), else ascending. The old `ascending: true/false` is still accepted and converted |
| `top_percent` | 0 | Admit at most this % of candidates (0 = no cap) |
| `percent_of` | `input` | `input`: % of candidates that entered; `passed`: % of those passing the criteria |
| `top_n` | 0 | Admit at most N candidates (0 = no cap); with `top_percent`, the tighter cap wins |
| `limit_by` | `molecules` | Count candidates as unique molecules (ranked by their best pose) or as poses |
| `best_pose_only` | true | Pass on only each admitted molecule's best pose |
| `id_column` | `id` | Score-table column holding molecule ids |
| `name_property` | `""` | SDF property to use as the pose name instead of the title |
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
`gate` section records `rank_by`, `order`, `order_source` (`config`, e.g.
`lower is better`, or the ascending default), `unit`, `entered`,
`passed_criteria`, `limit`, `admitted`, `admitted_percent`, `dropped_by_limit`
and a one-line `summary`. Set `fail_if_empty: true` to stop the pipeline
rather than hand the next round no molecules.

Two gates can be chained directly (`filtered_molecules` → `molecules`,
`filtered_scores` → `scores`). In `filtered_scores`, `pose` numbers each
molecule's poses in the order they appear in the output SDF, so the next gate
pairs every row with the right pose; the rank a pose had on input stays in
`pose_rank`.

### Example

```yaml
- id: gate
  type: molecule_filter
  inputs:
    molecules: dock.docked_poses
    scores: dock.docking_scores
    binding_site: pipeline.binding_site
  config:
    criteria:
      - {column: affinity_kcal_mol, op: "<=", value: -7.0}
    position_mode: centroid_in_box
    rank_by: affinity_kcal_mol
    top_percent: 1
```

See [`examples/pipelines/08_docking_funnel.yaml`](../examples/pipelines/08_docking_funnel.yaml):
fast docking → top 1% gate → careful re-docking → top-10 gate.

---

## similarity_search

Find molecules in a database similar to query structures using fingerprints
and Tanimoto similarity.

- **Inputs**: `queries` (`molecule.collection`, `smiles` or `sdf`),
  `database` (`molecule.collection`, `sdf` or `smiles`)
- **Outputs**: `similar_molecules` (`molecule.collection/sdf`,
  `outputs/similar.sdf`): each matching database molecule once, best match
  first, with `query_id` and `similarity` properties of its best match;
  `similarity_scores` (`table.scores/csv`, columns `id`, `query_id`,
  `similarity`; `similarity` is declared higher-is-better), one row per
  `(id, query_id)` pair at or above the threshold
- **Environment**: Python, RDKit
- **Resources (default)**: 4 CPUs, 8 GB, 2 h

Both outputs carry `metadata["search_inventory"]` with per-query and
per-database outcomes (`matched`, `unmatched`, `truncated` or `invalid`) and
candidate/returned/truncated pair counts. Duplicate input IDs receive
deterministic collision-free occurrence suffixes; unique IDs stay unchanged.
Pooled validation checks score identities, pair uniqueness, finite similarity
values, ordering, limits, returned molecule identities and complete outcome
accounting. At least one query must parse successfully; no matches is valid.

### Parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| `fingerprint_type` | `morgan` | `morgan`, `maccs`, or `rdkit` |
| `radius` | 2 | Morgan fingerprint radius |
| `n_bits` | 2048 | Fingerprint bit length (`morgan`, `rdkit`) |
| `similarity_threshold` | 0.7 | Minimum Tanimoto similarity (0–1) |
| `max_results` | 100 | Max `(molecule, query)` pairs in total, after sorting by similarity across all queries |

---

## ligand_prep

Prepare ligands for docking: generate 3D conformers, add hydrogens, minimize.

- **Inputs**: `ligands` (`molecule.collection`, `smiles` or `sdf`)
- **Outputs**: `prepared_ligands` (`molecule.collection/sdf`,
  `outputs/prepared_ligands.sdf`; molecule names are kept, so ids still join
  with upstream scores), `preparation_log` (`report/json`,
  `outputs/preparation_log.json`)
- **Environment**: Python, RDKit
- **Resources (default)**: 2 CPUs, 4 GB, 2 h

Dependency checks require readable molecule input and the execution environment
requires RDKit. The preparation log counts every input, including unparsable
records: `total = successful + failed`, with `successful_molecules` and
`failed_molecules` identifying `(id, occurrence)` and explaining each failure.
Prepared SDF records preserve names and carry `rapier_input_occurrence`, so
duplicate IDs and skipped occurrences remain unambiguous. Pooled validation
checks this partition, output identities, heavy atoms and finite 3D coordinates.
Partial preparation is accepted with complete failure accounting; all-invalid
input fails the task.

### Parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| `ph` | 7.4 | Protonation pH (0–14) |
| `num_conformers` | 1 | Conformers to generate |
| `energy_minimize` | true | Whether to minimize |
| `force_field` | `UFF` | `UFF` or `MMFF` |

---

## protein_prep

Prepare protein receptors: clean structure, add hydrogens, assign charges,
convert to PDBQT.

- **Inputs**: `receptor` (`structure.protein/pdb`; a CIF input is converted to
  PDB when Gemmi or Open Babel is available)
- **Outputs**: `prepared_receptor` (`structure.protein`, `pdb` or `pdbqt`),
  `preparation_log` (`report/json`)
- **Environment**: Open Babel (`obabel`)
- **Resources (default)**: 1 CPU, 4 GB, 30 m

Dependency checks require a readable, nonempty PDB receptor with usable atom
coordinates; the execution environment requires `obabel`. Pooled validation
checks the configured output format, nonempty atom records and finite
coordinates alongside the preparation log's `steps` and `warnings` lists.

### Parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| `ph` | 7.4 | Protonation pH (0–14) |
| `remove_waters` | true | Strip water molecules |
| `remove_hetero` | false | Strip heteroatoms |
| `add_charges` | true | Add Gasteiger charges |
| `output_format` | `pdbqt` | `pdb` or `pdbqt` |

---

## vina_docking

Molecular docking with [AutoDock Vina](https://github.com/ccsb-scripps/AutoDock-Vina)
1.2.x. Local and SLURM execution both use the generic method lifecycle and call
the deterministic `vina_runner.run_manifest` workflow from `run`:
a PDB receptor is prepared with Meeko, each SDF record is converted to PDBQT
with Meeko, Vina docks each ligand separately (one failure does not stop the
rest), and Meeko exports the PDBQT poses back to SDF. A PDBQT receptor is
accepted directly. The method is marked parallelizable across ligand chunks.

- **Inputs**: `receptor` (`structure.protein`, `pdb`, `cif` or `pdbqt`),
  `ligands` (`molecule.collection/sdf`), optional `binding_site`
  (inline `binding_site/box` with `center` and `size`)
- **Outputs**: `docked_poses` (`molecule.poses/sdf`, `outputs/docked_poses.sdf`;
  every pose is titled with its ligand id), `docking_scores`
  (`table.scores/csv`, `outputs/docking_scores.csv`; `affinity_kcal_mol` is
  declared lower-is-better, in kcal/mol). Both carry `failed_ligands` and
  `successful_ligands` in their artifact metadata.
- **Environment**: `vina`, `mk_prepare_receptor.py`,
  `mk_prepare_ligand.py`, `mk_export.py`, Python and the ProDy package (used by
  Meeko receptor preparation). CIF receptors also need the `gemmi` package.
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
| `cpu` | allocated CPUs | Vina worker count; omitted uses `resources.cpus`, else the default 4 |
| `seed` | unset | Random seed for reproducible docking |
| `min_rmsd` | unset | Minimum RMSD between reported modes |
| `verbosity` | 1 | Vina log verbosity (`0`–`2`) |
| `allow_partial_failures` | `true` | Keep successful ligand results when another ligand fails |

`box_center` and `box_size` are deprecated: they are used only when no
`binding_site` input is bound. Each score row contains `id`, `pose`,
`affinity_kcal_mol`, `rmsd_lb`, `rmsd_ub`, `scoring_function` and `pose_file`
(previously `ligand`, `mode`, ...). Ligand ids come from the SDF titles;
duplicate titles get a `-2`, `-3`, ... suffix. Failed ligand names and their
error messages are retained in `outputs/vina_run.json` and in output artifact
metadata.

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

Docking with Smina (a Vina fork with extra scoring functions). Reads PDB
receptors and SDF ligands directly and docks each ligand separately; ligands
that fail are skipped. Parallelizable across ligands.

- **Inputs**: `receptor` (`structure.protein`, `pdb` or `pdbqt`), `ligands`
  (`molecule.collection/sdf`), optional `binding_site` (`binding_site/box`,
  overrides the deprecated `box_center`/`box_size`)
- **Outputs**: the same shape as `vina_docking`: `docked_poses`
  (`molecule.poses/sdf`, titled by ligand id) and `docking_scores`
  (`table.scores/csv`) with `id`, `pose`, `affinity_kcal_mol` (lower is
  better; formerly `score`), `rmsd_lb`, `rmsd_ub` and `scoring_function`.
  There is no `pose_file` column.
- **Environment**: Smina (`smina`)
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

Both outputs carry the same per-input `ligand_outcomes`,
`successful_ligands` and `failed_ligands` metadata. Partial results require an
explicit error for each failed ligand. Duplicate names receive deterministic
suffixes that preserve unique input IDs. Pooled validation checks the complete
outcome partition, reconciles metadata across both ports, and compares the
`(id, pose)` inventories, readable atom coordinates and finite score columns.

---

## boltz2

Biomolecular complex prediction with [Boltz-2](https://github.com/jwohlwend/boltz).
The method creates the official Boltz YAML schema, runs `boltz predict`
through the `boltz2_runner.run_manifest` helper inside the generic lifecycle,
and normalizes the predicted structures and confidence/affinity JSON files.
`boltz2_prediction` is a registered alias with the same contract.

- **Inputs** (all optional, but at least one of `proteins`, `dna`, `rna`,
  `ligands` is required):
  - `proteins`: `sequence.protein`, `inline` or `fasta` (many)
  - `dna`: `sequence.dna`, `inline` or `fasta` (many)
  - `rna`: `sequence.rna`, `inline` or `fasta` (many)
  - `ligands`: `molecule` or `molecule.collection`, `smiles`, `sdf`, `mol2`
    or `ccd` (many). SDF ligands need RDKit. CCD ligands can also be supplied
    with artifact metadata `ccd`.
  - `templates`: `structure`, `pdb` or `cif` (many)
  - `msas`: `file`, `a3m` or `csv` (many), matched by `chain_id`
  - `constraints`: one `file/json` artifact containing a list of Boltz `bond`,
    `pocket`, or `contact` entries (or `{constraints: [...]}`); it replaces the
    `constraints` config field

  Entity IDs are read from artifact metadata `chain_id`. Use `chain_ids` for a
  single sequence that represents multiple identical chains. Polymer metadata
  may also contain Boltz `modifications`, `cyclic`, and `msa` fields.
- **Outputs**:
  - `predicted_structures`: one `structure.complex` (`cif` or `pdb`) artifact
    per diffusion sample
  - `confidence_scores`: Boltz confidence JSON artifacts (`report/json`)
  - `affinity_scores`: optional Boltz affinity JSON artifacts (`report/json`)
  - `scores`: `table.scores/csv` (`outputs/boltz2_scores.csv`), one row per
    predicted model: `id` (the model file stem), `confidence_score` (higher is
    better) and the numeric `affinity*` values of the affinity report, such as
    `affinity_pred_value` (lower is better) and `affinity_probability_binary`
    (higher is better)
  - `prediction_arrays`: optional PAE/PDE/pLDDT `.npz` artifacts (`file/npz`)
  - `run_summary`: Rapier execution summary (`report/json`)
  - `vina_receptor`: optional receptor-only `structure.protein/pdb` artifact
    generated when `emit_docking_inputs` is enabled
  - `docking_box`: optional inline `binding_site/box` covering all predicted
    ligand coordinates when `emit_docking_inputs` is enabled
- **Environment**: `boltz` and Python. `emit_docking_inputs` also requires
  Gemmi and at least one protein and one ligand input. Install `boltz[cuda]`
  for NVIDIA GPUs.
- **Resources (default)**: 1 GPU, 8 CPUs, 32 GB, 12 h

### Parameters

Most fields map one-to-one onto `boltz predict` options; unset (`null`) values
are not passed.

| Parameter | Default | Description |
|-----------|---------|-------------|
| `accelerator` | `gpu` | `gpu`, `cpu`, or `tpu` |
| `devices` | 1 | Number of devices |
| `cache` / `checkpoint` | unset | Boltz cache directory / model checkpoint |
| `use_msa_server` | `true` | Generate MSA through the configured server |
| `msa_server_url` | `https://api.colabfold.com` | MSA server (used with `use_msa_server`) |
| `msa_pairing_strategy` | `greedy` | `greedy` or `complete` |
| `single_sequence_mode` | `false` | Use `msa: empty` when no MSA is available |
| `subsample_msa` | `false` | Subsample the MSA |
| `max_msa_seqs` / `num_subsampled_msa` | 8192 / 1024 | MSA size limits |
| `use_potentials` | `false` | Enable inference-time physical potentials |
| `recycling_steps` | 3 | Recycling iterations |
| `sampling_steps` | 200 | Diffusion sampling steps |
| `diffusion_samples` | 1 | Number of structure samples |
| `max_parallel_samples` | 5 | Samples predicted in parallel |
| `step_scale` | 1.5 | Diffusion step scale |
| `method` | unset | Boltz conditioning method |
| `num_workers` | 2 | Data-loader workers |
| `preprocessing_threads` | unset | Preprocessing threads (≥ 1 when set) |
| `output_format` | `mmcif` | `mmcif` or `pdb` |
| `write_full_pae` / `write_full_pde` | `false` | Write full PAE/PDE arrays |
| `no_kernels` | `false` | Disable custom kernels |
| `properties` | unset | Extra Boltz `properties` entries |
| `constraints` | unset | Boltz constraint entries (overridden by the `constraints` input) |
| `affinity_binder` | unset | Ligand chain ID for Boltz affinity prediction; needs a ligand input |
| `sampling_steps_affinity` / `diffusion_samples_affinity` | 200 / 5 | Affinity-module sampling |
| `affinity_mw_correction` | `false` | Apply the molecular-weight affinity correction |
| `affinity_checkpoint` | unset | Affinity model checkpoint |
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
