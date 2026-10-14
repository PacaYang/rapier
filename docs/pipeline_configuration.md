# Pipeline Configuration Reference

Complete reference for Rapier pipeline files. Pipelines are written in YAML
(JSON also works, since YAML is a superset). Working files live in
[examples/pipelines/](../examples/pipelines/). For how the parser, validator,
and engine process these keys, see
[Configuration Internals](config_reference.md).

## Top-level structure

```yaml
name: my_pipeline            # required
description: ...             # optional
version: 1.0.0               # optional
failure_policy: stop         # optional: stop | continue | retry (default: stop)

inputs: {...}                # optional: named pipeline artifacts
methods: [...]               # required, at least one
outputs: {...}               # optional: exported method outputs
connections: [...]           # optional: control-only dependencies
pooling_rules: {...}         # optional
environment: {...}           # optional
include: [...]               # optional: other YAML files to merge in
ui: {...}                    # optional: workflow-builder canvas state
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `name` | string | yes | Pipeline name (non-empty). Also names the execution state file |
| `description` | string | no | Human-readable description |
| `version` | string | no | Free-form, conventionally `X.Y.Z`. Stored in `pipeline.metadata`; methods report their own class `version` |
| `failure_policy` | enum | no | `stop`, `continue`, or `retry`. See [Failure policies](#failure-policies) |
| `inputs` | map | no | Named artifacts, referenced as `pipeline.<name>` |
| `methods` | list | yes | Method definitions (at least one) |
| `outputs` | map | no | Export names mapped to `<method>.<output_port>` |
| `connections` | list | no | Extra ordering edges between methods |
| `pooling_rules` | map | no | How a multi-input method combines branch results |
| `environment` | map | no | `variables` and `project_dir` |
| `include` | string or list | no | Files merged under this one, relative to the project dir |
| `ui` | map | no | Ignored by the parser. Holds `ui.layout` from the workflow builder |

Other top-level keys are ignored without a warning, so check spelling.

## Inputs (pipeline artifacts)

`inputs` declares typed data independently of any method:

```yaml
inputs:
  receptor:
    type: structure.protein
    format: pdb
    source: data/receptor.pdb
  ligands:
    type: molecule.collection
    format: smiles
    value:
      - CCO ethanol
      - c1ccccc1 benzene
  site:
    type: binding_site
    format: box
    value:
      center: [10.0, 15.0, 20.0]
      size: [20.0, 20.0, 20.0]
```

| Field | Required | Description |
|-------|----------|-------------|
| `type` | yes | Semantic artifact type (table below) |
| `format` | yes | Representation. Must be valid for the type |
| `source` | one of | Local file path. Relative paths resolve against the project dir (the YAML file's directory) |
| `value` | one of | Inline JSON-safe payload |
| `metadata` | no | Free-form map passed along with the artifact (e.g. `chain_id` for Boltz-2) |

Set exactly one of `source` and `value`. `source` files must exist when the
pipeline is validated.

### Built-in artifact types

| Type | Formats | Notes |
|------|---------|-------|
| `sequence` | `inline`, `fasta` | Parent of the three sequence types |
| `sequence.protein` | `inline`, `fasta` | Inline values are checked against the amino-acid alphabet |
| `sequence.dna` | `inline`, `fasta` | Inline values checked against the DNA alphabet |
| `sequence.rna` | `inline`, `fasta` | Inline values checked against the RNA alphabet |
| `molecule` | `smiles`, `sdf`, `mol2`, `pdbqt`, `ccd` | Single molecule |
| `molecule.collection` | `smiles`, `sdf`, `mol2`, `pdbqt`, `ccd` | Library of molecules |
| `molecule.poses` | `sdf` | Docked or predicted poses (subtype of `molecule.collection`); records sharing an id are ranked poses of one molecule |
| `structure` | `pdb`, `cif`, `pdbqt` | Parent of the structure types |
| `structure.protein` | `pdb`, `cif`, `pdbqt` | |
| `structure.nucleic_acid` | `pdb`, `cif` | |
| `structure.complex` | `pdb`, `cif` | |
| `binding_site` | `box`, `residues`, `reference`, `json` | `box` needs `center` and `size` (3 numbers each, positive size). `residues` needs a non-empty list |
| `table` | `csv`, `tsv`, `json` | |
| `table.scores` | `csv`, `tsv`, `json` | Score table keyed by molecule `id` (plus `pose`); may declare column directions in its metadata (subtype of `table`) |
| `report` | `json`, `text`, `html`, `csv` | |
| `file` | any | Generic file. A port that accepts `file` accepts any file artifact |

A subtype satisfies a port that asks for its parent. For example,
`structure.protein` fits a `structure` port, `molecule.poses` a
`molecule.collection` port, and `table.scores` a `table` port. `molecule` and
`molecule.collection` are separate types. Each type is a data class in
`rapier.data`.

### Built-in converters

If an artifact's format doesn't match a port, Rapier looks for a converter
chain, both during validation and when the input is staged. Converters whose
tool isn't installed are skipped. A converter for a parent type also applies
to its subtypes, so `table.scores` converts like `table`.

| Conversion | Provider |
|------------|----------|
| `table` `csv` ↔ `tsv` ↔ `json` | core |
| `sequence.*` `inline` → `fasta` | core |
| `molecule`, `molecule.collection` `smiles` ↔ `sdf` | RDKit |
| `structure.protein`, `structure.nucleic_acid`, `structure.complex` `cif` ↔ `pdb` | Gemmi (Open Babel fallback for `cif` → `pdb`) |
| `structure.protein` `pdbqt` → `pdb` | Open Babel |

## Methods

```yaml
methods:
  - id: dock                     # required, unique
    type: vina_docking           # required, registered method type
    description: ...             # optional
    inputs:                      # port bindings
      receptor: pipeline.receptor
      ligands: prepare.prepared_ligands
      binding_site: pipeline.site
    config:                      # method settings
      exhaustiveness: 8
    resources:                   # SLURM request
      cpus: 8
      memory: 8GB
      time_limit: 2h
    failure_policy: retry        # optional, default stop
    retry_count: 3               # optional, default 1
```

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `id` | string | required | Unique, starts with a letter. Use letters, digits, `_`, `-` |
| `type` | string | required | Registered method type (see [Method Reference](method_reference.md)) |
| `description` | string | none | Description |
| `inputs` | map | `{}` | Input port name → `pipeline.<input>` or `<method>.<output_port>` |
| `config` | map | `{}` | Method settings, checked against the method's config schema |
| `parameters` | map | none | Deprecated alias for `config`. Emits a `DeprecationWarning`. Setting both is an error |
| `resources` | map | `{}` | See [Resources](#resources) |
| `failure_policy` | enum | `stop` | `stop`, `continue`, or `retry` |
| `retry_count` | int | `1` | Retries allowed under `retry` (at least 1) |

Registered types: `ligand_filter`, `ligand_prep`, `protein_prep`,
`similarity_search`, `molecule_filter`, `vina_docking`, `smina_docking`,
`boltz2` (alias `boltz2_prediction`).

A `type` that isn't registered is accepted as long as the method has no
`inputs` (its task then runs with no method instance). Several legacy examples
rely on this, for example `simple_serial.yaml`.

### Bindings and dependencies

Each key under `inputs` must be an input port of the method. Each value is
either:

- `pipeline.<name>`: an entry from top-level `inputs`
- `<method_id>.<port>`: an output port of another method

A `<method>.<port>` reference also makes the target depend on that method, so
pipelines that bind their data need no `connections`. Example 08 has none. The
validator checks each binding statically:

- The port exists on the method, and the referenced input or method output exists.
- The source type/format fits the port, directly or through a converter chain.
  For method outputs, the source port's first declared type and format are
  used.
- Every required port of every registered method is bound. This applies to
  *normalized* pipelines, meaning pipelines with top-level `inputs` or
  `outputs`, or at least one method with `inputs`. Pipelines wired only with
  `connections` can still have inputs injected at run time.
- Methods can add config-dependent rules through a `validate_bindings` hook.
  For example, `molecule_filter` requires `binding_site` when `position_mode`
  isn't `none`, or when a criterion or `rank_by` uses a box metric. It
  requires `reference_ligand` for `reference_distance`.

When a method's input port has cardinality `one`, it must resolve to exactly
one artifact.

### Config

For every registered method, `config` is strict. Unknown keys, wrong types,
and values outside the allowed choices or min/max range are validation errors.
Omitted keys take the defaults listed in the
[Method Reference](method_reference.md). A method may still accept a renamed
key and translate it first; `molecule_filter` turns the old `ascending: bool`
into `order`. Fields that hold paths are not resolved against the project dir.
Pass files through `inputs` instead.

### Resources

| Field | Type | Format | Example |
|-------|------|--------|---------|
| `cpus` | int | ≥1 | `8` |
| `memory` | string | ends in `MB`, `GB`, or `TB` | `8GB` |
| `time_limit` | string | ends in `s`, `m`, `h`, or `d` | `2h` |
| `gpus` | int | ≥0 | `1` |
| `partition` | string | | `gpu` |

The validator only checks the unit suffix on `memory` and `time_limit`.
Resources go to the generated SLURM job script.

## Outputs

```yaml
outputs:
  hits: final_gate.filtered_molecules
  final_report: final_gate.report
```

Each value must be `<method_id>.<output_port>` naming a real output port.
After a run, the exported artifacts appear in
`ExecutionResult.pipeline_outputs` under these names. Outputs from tasks that
didn't complete are left out.

## Connections

Connections add ordering edges without passing data. The `to` method waits for
every `from` method to complete.

```yaml
connections:
  - from: filter
    to: dock
  - from: [vina, smina]
    to: consensus
    type: parallel
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `from` | string or list | yes | Source method id(s) |
| `to` | string | yes | Target method id |
| `type` | enum | no | `serial` or `parallel` (default `serial`) |

`type` is checked but doesn't change scheduling. Any task whose upstream tasks
have all completed runs, up to the engine's `max_parallel`, so branches with no
dependency between them already run concurrently. Cycles through connections
or bindings are rejected.

## Pooling rules

A pooling rule is keyed by a method id. That method must have more than one
upstream method, counting both connections and bindings.

```yaml
pooling_rules:
  consensus:
    strategy: best          # required
    metric: docking_score   # required for best
    minimize: true          # optional, default false (maximize)
    function: my_func       # required for custom
```

| Strategy | Result |
|----------|--------|
| `any` | The first completed upstream task |
| `all` | All completed upstream tasks |
| `majority` | All completed tasks if more than half succeeded, otherwise none |
| `best` | The completed task with the best numeric `metric` output (an artifact metric reads `metadata["value"]`) |
| `first` | The first completed task. Raises an error if none completed |
| `custom` | The return value of a function registered with `ResultPooler.register_custom_function` |

> The current `ExecutionEngine` validates pooling rules and stores them on the
> `Pipeline` (as `any`, `best[metric]:minimize`, `custom[fn]`, …), but it
> doesn't call `ResultPooler` during a run. A method that should combine
> branches has to bind them to its own inputs.

## Failure policies

| Policy | On task failure |
|--------|-----------------|
| `stop` | Cancel the run (default) |
| `continue` | Mark every downstream task `SKIPPED` and keep running the rest |
| `retry` | Set the task back to pending, up to `retry_count` retries, then stop the run |

The policy that matters is the method-level `failure_policy`. Methods default
to `stop`, and the top-level `failure_policy` doesn't change that default. It is
only validated and saved in `pipeline.metadata`. Set the policy on each method
that needs it.

A run that ends with any failed task reports `success: false`, including runs
that used `continue`.

## Environment and variables

```yaml
environment:
  variables:
    RECEPTOR_NAME: target_protein
```

`${VAR}` placeholders anywhere in the file are replaced before the YAML is
parsed. Rapier checks these sources in order:

1. `${PROJECT_DIR}`: the project directory, which defaults to the YAML file's directory
2. Variables set with `ConfigParser.set_variable()`, or loaded from an earlier
   config parsed by the same parser
3. OS environment variables

Unresolved placeholders are left as they are. Substitution runs before
`environment.variables` is read, so variables defined in a file don't apply to
that same file on its first parse. Use OS environment variables or
`set_variable()` instead.

The parser always overwrites `environment.project_dir` with the resolved
project directory. A value in the file is ignored.

## Includes

```yaml
include: [common/resources.yaml]
```

Included files (relative to the project dir) are merged under the current
file, which wins on conflicts. Maps merge recursively, lists are concatenated
(included items first), and scalars are replaced. Included files don't get
variable substitution, and their own `include` keys aren't followed.

## `ui.layout` (workflow builder)

The [workflow builder](../README.md#workflow-builder) saves canvas positions
in a top-level `ui` key:

```yaml
ui:
  layout:
    input:receptor: {x: 40, y: 80}
    method:dock: {x: 360, y: 120}
    output:poses: {x: 700, y: 120}
```

Keys have the form `<kind>:<name>`, where `kind` is `input`, `method`, or
`output`. The parser ignores `ui`. On import, nodes without a saved position
get an automatic layout. The builder also keeps `connections`,
`pooling_rules`, and `environment` when it saves, though it has no editor for
them.

## Validation

```bash
python scripts/validate_config.py my_pipeline.yaml          # validate
python scripts/validate_config.py my_pipeline.yaml --build  # also build the Pipeline
```

Exit code `0` means valid, `1` invalid, `2` file not found. The checks are
listed under [Configuration Internals → Validation rules](config_reference.md#validation-rules).
