# Pipeline Configuration Reference

Complete reference for Rapier pipeline configuration files. Pipelines are
defined in YAML (JSON is also accepted, since YAML is a superset).

> See also the existing [config reference](config_reference.md) for additional
> notes, and [examples/pipelines/](../examples/pipelines/) for working files.

## Top-level structure

```yaml
name: my_pipeline            # required
description: ...             # optional
version: 1.0.0               # optional (semver: X.Y.Z)
failure_policy: stop         # optional: stop | continue | retry  (default: stop)

methods: [...]               # required, at least one
inputs: {...}                # optional normalized pipeline artifacts
outputs: {...}               # optional exported method outputs
connections: [...]           # optional
pooling_rules: {...}         # optional
environment: {...}           # optional
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `name` | string | yes | Unique pipeline name (non-empty) |
| `description` | string | no | Human-readable description |
| `version` | string | no | Semantic version `X.Y.Z` |
| `failure_policy` | enum | no | Global default policy: `stop`, `continue`, `retry` |
| `methods` | list | yes | Method definitions (≥1) |
| `connections` | list | no | Edges between methods |
| `pooling_rules` | map | no | How to combine parallel branch results |
| `environment` | map | no | Variables and project settings |

## Methods

Each entry in `methods` defines one processing step.

```yaml
methods:
  - id: dock                 # required, unique, must start with a letter
    type: vina_docking       # required, a registered method type
    description: ...          # optional
    inputs:                  # normalized artifact port bindings
      ligands: pipeline.ligands
    config:                  # optional, method-specific
      exhaustiveness: 8
    resources:               # optional, SLURM resources
      cpus: 8
      memory: 8GB
      time_limit: 2h
      gpus: 0
      partition: standard
    failure_policy: stop     # optional, overrides global
    retry_count: 3           # required if failure_policy: retry (≥1)
```

### Method fields

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `id` | string | yes | Unique identifier; must start with a letter |
| `type` | string | yes | Registered method type (see [Method Reference](method_reference.md)) |
| `description` | string | no | Description |
| `inputs` | map | no | Named method ports bound to `pipeline.<input>` or `<method>.<output>` |
| `config` | map | no | Strictly validated method-specific settings |
| `resources` | map | no | SLURM resource request |
| `failure_policy` | enum | no | `stop`, `continue`, or `retry` |
| `retry_count` | int | no | Number of retries (≥1), used with `retry` |

`parameters` remains a deprecated alias for `config`. Supplying both is an
error. Pipelines without normalized input declarations continue to use the v1
runtime input path.

## Normalized Artifacts

Top-level inputs declare reusable data independently of method configuration:

```yaml
inputs:
  receptor:
    type: structure.protein
    format: pdb
    source: data/receptor.pdb
  ligands:
    type: molecule.collection
    format: smiles
    source: data/ligands.smi
  site:
    type: binding_site
    format: box
    value:
      center: [10.0, 15.0, 20.0]
      size: [20.0, 20.0, 20.0]
```

Exactly one of `source` (a local file resolved relative to the pipeline) and
`value` (an inline JSON-safe payload) is required. Built-in semantic types are
protein/DNA/RNA sequences, molecules and molecule collections, protein/nucleic
acid/complex structures, binding sites, tables, reports, and generic files.
Formats such as SMILES, SDF, PDB, CIF, PDBQT, FASTA, CSV, and JSON describe a
representation, not the semantic type.

Method bindings are explicit:

```yaml
methods:
  - id: dock
    type: vina_docking
    inputs:
      receptor: pipeline.receptor
      ligands: prepare.prepared_ligands
      binding_site: pipeline.site
    config:
      exhaustiveness: 8

outputs:
  poses: dock.docked_poses
```

An upstream reference automatically creates a data dependency. `connections`
may still add control-only dependencies. Rapier validates port type/format
compatibility before execution and uses a registered adapter when an accepted
conversion is available.

### Resources

| Field | Type | Format | Example |
|-------|------|--------|---------|
| `cpus` | int | ≥1 | `8` |
| `memory` | string | `<n>MB`/`<n>GB`/`<n>TB` | `8GB` |
| `time_limit` | string | `<n>s`/`<n>m`/`<n>h`/`<n>d` | `2h` |
| `gpus` | int | ≥0 | `1` |
| `partition` | string | — | `gpu` |

## Connections

Connections define execution order. A connection runs `to` after all `from`
methods complete.

```yaml
connections:
  # Serial: A then B
  - from: filter
    to: dock
    type: serial

  # Parallel join: both vina and smina feed into consensus
  - from: [vina, smina]
    to: consensus
    type: parallel
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `from` | string or list | yes | Source method id(s) |
| `to` | string | yes | Target method id |
| `type` | enum | no | `serial` or `parallel` (default: `serial`) |

A method with multiple `from` sources receives input from all of them and
typically uses a pooling rule to combine them.

## Pooling Rules

When multiple branches feed into one method, a pooling rule defines how their
results are combined. Pooling rules are keyed by the target method id.

```yaml
pooling_rules:
  consensus:
    strategy: best          # required
    metric: docking_score   # required for 'best'
    minimize: true          # optional (default false → maximize)
    function: my_func       # required for 'custom'
```

### Strategies

| Strategy | Description |
|----------|-------------|
| `any` | Accept the first successful upstream result |
| `all` | Combine all successful results |
| `majority` | Proceed only if >50% of branches succeeded |
| `best` | Select the result with the best `metric` value |
| `first` | Use the first completed result |
| `custom` | Apply a registered `function` |

> A pooling rule requires its target method to have **more than one** upstream
> connection, or validation fails.

## Environment

```yaml
environment:
  variables:
    RECEPTOR_NAME: target_protein
  project_dir: /path/to/project
```

### Variable substitution

The parser substitutes `${VAR}` references using, in order:

1. `${PROJECT_DIR}` → the resolved project directory
2. Custom variables defined under `environment.variables`
3. OS environment variables

Unresolved variables are left unchanged.

```yaml
methods:
  - id: dock
    type: vina_docking
    inputs:
      receptor: pipeline.receptor

inputs:
  receptor:
    type: structure.protein
    format: pdbqt
    source: ${PROJECT_DIR}/data/receptor.pdbqt
```

## Failure Policies

| Policy | Behavior on failure |
|--------|---------------------|
| `stop` | Halt the entire pipeline (default) |
| `continue` | Skip the failed task and its downstream tasks; continue the rest |
| `retry` | Re-run the task up to `retry_count` times, then stop if still failing |

Policies can be set globally (top-level `failure_policy`) and overridden
per-method.

## Validation

Validate any configuration with:

```bash
python scripts/validate_config.py my_pipeline.yaml
```

The validator checks:

- Required fields and non-empty name
- At least one method, no duplicate ids
- Method ids start with a letter
- Connections reference existing methods
- No circular dependencies
- Pooling rules target valid multi-input methods with valid strategies
- Resource specification formats
