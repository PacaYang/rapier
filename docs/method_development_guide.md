# Method Development Guide

This guide explains how to develop custom screening methods for the Rapier platform.

## Table of Contents

1. [Overview](#overview)
2. [Method Lifecycle](#method-lifecycle)
3. [The Method Class](#the-method-class)
4. [Creating a Simple Method](#creating-a-simple-method)
5. [Ports and Data Types](#ports-and-data-types)
6. [Configuration Schema](#configuration-schema)
7. [Config-Dependent Port Requirements](#config-dependent-port-requirements)
8. [Workflow Builder Hints](#workflow-builder-hints)
9. [Environment Requirements](#environment-requirements)
10. [Resource Specifications](#resource-specifications)
11. [Running on SLURM](#running-on-slurm)
12. [Parallelizable and Checkpointable Methods](#parallelizable-and-checkpointable-methods)
13. [Testing Your Method](#testing-your-method)
14. [Registering Your Method](#registering-your-method)

## Overview

A **method** in Rapier represents a screening algorithm or computational workflow. Every method subclasses `rapier.Method` and declares, as class attributes:

- **Inputs**: named ports that accept typed data (`Molecules`, `ProteinStructure`, `BindingSite`, ...)
- **Outputs**: named ports the method fills with typed data (`Poses`, `Scores`, `Report`, ...)
- **Config**: a strict, validated schema of settings, separate from inputs
- **Environment**: what software dependencies are required
- **Resources**: what computational resources (CPU, memory, time) are needed

You implement one function, `run(inputs, ctx) -> {port: value}`. The base class wraps raw inputs as each port's data class, converts and stages them, validates them, and turns whatever `run` returns into typed, validated output artifacts with provenance. The platform handles scheduling, dependency management, and result tracking.

## Method Lifecycle

For each task, the engine:

1. **Instantiates** the method as `method_class(name=<method id>, config=<config>)`. The constructor runs `migrate_config`, then validates the config strictly. The pipeline validator builds every method the same way ahead of time, so config errors surface at validation.
2. **Resolves bindings**: `InputResolver` looks up each bound `pipeline.<name>` or `<method>.<port>` value.
3. **Formats inputs** with `format_inputs`: each value is wrapped as the port's data class, validated, converted to a format the port accepts, and staged (symlinked or copied) into `<work_dir>/input/normalized/`.
4. **Validates** with `validate_inputs` (port contract, `validate_bindings`, then your `check_inputs`) and `validate_environment()`.
5. **Submits** `get_command(task)` as the body of the SLURM script. By default this is the generic runner, which calls `execute(task)` on the compute node (see [Running on SLURM](#running-on-slurm)).
6. **Collects** outputs with `collect_outputs(task)` once the job has finished. This is skipped in dry runs, which execute nothing.

`execute(task)` is the in-process path, used by the generic runner, tests and local runs: `format_inputs` → `validate_inputs` → `run` → `format_outputs`, with the result stored in `task.outputs`. Any exception fails the task with `"<display_name> failed: <message>"`. Raise `MethodError` for an expected, user-facing failure.

You must implement `run`. Override `check_config`, `migrate_config`, `validate_bindings`, `check_inputs` or `validate_environment` when the defaults aren't enough, and `get_command` / `collect_outputs` only when the method needs its own job runner.

## The Method Class

`rapier.core.method.Method` (re-exported as `rapier.Method`) is the only base class. A subclass declares:

| Attribute | Purpose |
|-----------|---------|
| `register_name` | Type used in pipeline YAML; enables auto-discovery |
| `display_name`, `category`, `version` | Builder label, palette group, version string (default `"1.0"`) |
| `inputs` / `outputs` | `{port: PortSpec(DataClass, formats, ...)}` |
| `config_fields` | `{name: ConfigField(...)}` |
| `resources` | `ResourceSpec` defaults |
| `environment` | `EnvironmentSpec` requirements |
| `ui_hints` | Optional workflow-builder widget hints |

and overrides any of these hooks:

| Hook | Called | Returns |
|------|--------|---------|
| `run(inputs, ctx)` | by `execute` | `{output_port: value}` (required) |
| `migrate_config(config)` | before config validation | the translated config dict (rename deprecated keys) |
| `check_config(config)` | after per-field validation | list of cross-field errors |
| `validate_bindings(bound_ports)` | by the pipeline validator and `validate_inputs` | list of errors for config-dependent ports |
| `check_inputs(inputs)` | after the port contract passes | list of content errors |

The constructor is `Method(name=None, config=None, *, version=None, parameters=None)`. `name` is the pipeline method id (default `register_name`). `parameters` is an alias of `config`, and passing both is an error. The instance exposes `config` (normalized), `input_spec`, `output_spec`, `config_spec`, `resource_spec` and `env_requirements`.

`run` receives the formatted inputs (data-class instances, or lists of them for `many` ports) and a `RunContext` with `task`, `work_dir`, `input_dir`, `output_dir` (`<work_dir>/outputs`, where `run` should write) and `resources` (the task's resource request).

### ParallelizableMethod (mixin)

Marks a method as safe to split across ligand chunks (`can_parallelize()` returns `True`) and provides `split_inputs` / `merge_outputs` hooks.

### CheckpointableMethod (mixin)

Adds `save_checkpoint`, `load_checkpoint` and `should_checkpoint`, storing JSON under `work_dir/checkpoints/`.

## Creating a Simple Method

Here's a complete molecular weight filter:

```python
from typing import Any, Dict, List

from rapier import (ConfigField, EnvironmentSpec, Method, MethodError, Molecules,
                    PortSpec, ResourceSpec, RunContext, Scores)
from rapier.methods.utils import rdkit_molecules, write_rdkit_sdf


class MolecularWeightFilter(Method):
    """Filter molecules by molecular weight."""

    register_name = "mw_filter"
    display_name = "MW Filter"
    category = "filter"
    inputs = {
        "molecules": PortSpec(Molecules, ("sdf", "smiles"), description="Molecules to filter"),
    }
    outputs = {
        "filtered": PortSpec(Molecules, "sdf", description="Molecules passing the filter"),
        "properties": PortSpec(Scores, "csv", description="Molecular weight of every molecule"),
    }
    config_fields = {
        "mw_min": ConfigField((int, float), default=0, minimum=0, description="Lower bound (Da)"),
        "mw_max": ConfigField((int, float), default=500, minimum=0, description="Upper bound (Da)"),
    }
    resources = ResourceSpec(cpus=1, memory="2GB", time="00:30:00", partition="cpu")
    environment = EnvironmentSpec(python_packages=["rdkit"])

    def check_config(self, config: Dict[str, Any]) -> List[str]:
        # Also runs when a field already failed, without that key, so use .get().
        if config.get("mw_min", 0) > config.get("mw_max", 500):
            return ["mw_min cannot be greater than mw_max"]
        return []

    def run(self, inputs: Dict[str, Any], ctx: RunContext) -> Dict[str, Any]:
        from rdkit.Chem import Descriptors

        rows, kept = [], []
        for record, mol in rdkit_molecules(inputs["molecules"]):  # (MoleculeRecord, Mol or None)
            if mol is None:
                continue
            mw = Descriptors.MolWt(mol)
            passed = self.config["mw_min"] <= mw <= self.config["mw_max"]
            rows.append({"id": record.id, "mw": round(mw, 3), "passed": passed})
            if passed:
                kept.append(mol)
        if not rows:
            raise MethodError("No valid molecules in input")
        return {
            "filtered": write_rdkit_sdf(kept, ctx.output_dir / "filtered.sdf"),  # a path
            "properties": rows,  # row dicts become outputs/properties.csv (Scores)
        }
```

**Important**: write files to `ctx.output_dir` and return a value for every required output port. Returning a port that isn't declared is an error. The canonical, commented example is [`examples/methods/custom_method_template.py`](../examples/methods/custom_method_template.py).

## Ports and Data Types

Data travels between methods as `Artifact`s: `Artifact(type, format, value, storage=..., metadata=...)`. Each semantic type is a data class in `rapier.data`, and an `Artifact` built or deserialized with a registered type string is an instance of that class (`Artifact("table.scores", "csv", path)` is a `Scores`). File-backed artifacts hold a path and implement `os.PathLike`; inline artifacts hold the value itself, for example a binding-site box `{"center": [...], "size": [...]}`.

| Class | Type string | Useful readers and constructors |
|-------|-------------|---------------------------------|
| `Sequence`, `ProteinSequence`, `DnaSequence`, `RnaSequence` | `sequence[.protein\|.dna\|.rna]` | `.sequence`, `from_string()` |
| `Molecule`, `Molecules` | `molecule`, `molecule.collection` | `.records()`, `.ids()`, `from_smiles()`, `write_sdf()` |
| `Poses` (subtype of `Molecules`) | `molecule.poses` | as `Molecules`; records sharing an id are ranked poses |
| `Structure`, `ProteinStructure`, `NucleicAcidStructure`, `ComplexStructure` | `structure[.protein\|...]` | `.path` |
| `BindingSite` | `binding_site` | `.box()`, `from_box()` |
| `Table`, `Scores` (subtype of `Table`) | `table`, `table.scores` | `.rows()`; `Scores.records()`, `.directions()`, `from_records()` |
| `Report` | `report` | `.load()` |
| `File` | `file` | `.path` |

`Molecules.records()` reads SDF or SMILES without RDKit and yields `MoleculeRecord`s (`id`, `name`, `index`, `pose`, `smiles` or `block`, `properties`, `atoms`). `id` is `molecule_id(name)`, the sanitized join key shared by every method, so poses and score tables written by different methods line up. Score tables are keyed by `id` (plus `pose` for per-pose rows), and `Scores.from_records(rows, path, directions={"col": "lower"})` records each column's direction in `metadata["columns"][col]["better"]`, which `molecule_filter` uses to rank.

A `PortSpec` describes one named port:

```python
PortSpec(
    types,                 # data class(es) or type string(s): Molecules, (Molecule, Molecules), "table"
    formats=None,          # str or tuple: accepted formats; empty = any
    required=True,
    cardinality="one",     # "one" or "many" (inputs/outputs are then lists)
    description="",
)
```

Types are stored as type strings, and `port.data_class` is the first one's class. A port accepts an artifact whose type equals, or is a subtype of, one of `types` (`Poses` fit a `Molecules` port, `Scores` a `Table` port), and whose format is in `formats`. A port typed `File` accepts any file-backed artifact. To add a type, subclass a data class with a new dotted `type_name`; it is registered automatically. Converters go in with `get_converter_registry().register(ArtifactConverter(...))`, and a converter for a parent type also applies to its subtypes. The built-in types, formats and converters are listed in the [Method Reference](method_reference.md#artifact-types).

**Inputs.** `format_inputs` wraps raw values with `port.coerce`: a path becomes a file artifact (format from its suffix), a SMILES string or list of strings becomes inline `smiles`, a `{center, size}` dict becomes a `box`, and an artifact passes through. It then validates the value, converts it if the format doesn't match (for example SMILES → SDF with RDKit), and stages it. `run` can therefore assume every input already matches its port.

**Outputs.** `format_outputs` wraps each returned value as its output port's class: an artifact passes through, a path becomes a file artifact, a list of row dicts becomes a `Table`/`Scores` file named after the port, and a dict becomes a JSON `Report` (or an inline box for a `BindingSite` port). It then checks required ports, cardinality, type/format and file existence, adds provenance (producing task, output port, conversions), and rejects undeclared ports.

## Configuration Schema

Settings are declared in `config_fields`, separate from inputs:

```python
ConfigField(
    types,               # type or tuple of types; bool is never accepted for int unless listed
    required=False,
    default=<missing>,   # deep-copied into the normalized config
    choices=None,
    minimum=None,
    maximum=None,
    validator=None,      # callable(value) -> error string or None
    description="",      # shown in the workflow builder inspector
)
```

The constructor calls `migrate_config(config)`, then validates the result against a strict `ConfigSpec`: it fills defaults, rejects unknown keys, checks types, choices and bounds, runs the per-field validators and then `check_config(config)`, and stores the result in `self.config`. All errors are raised together as one `ValueError` joined with `;`. Use `check_config` for cross-field rules; `molecule_filter`, for example, requires `rank_by` whenever `top_n` or `top_percent` is set. Use `migrate_config` to keep accepting a renamed key: `molecule_filter` turns the old `ascending: bool` into `order`.

Pipelines put settings under `config:`. The older `parameters:` key is deprecated and cannot be combined with `config:`. In Python, `parameters=` is accepted as an alias of `config=`.

## Config-Dependent Port Requirements

If whether a port is needed depends on config, define `validate_bindings`:

```python
def validate_bindings(self, bound_ports) -> List[str]:
    """Return one error per missing port; bound_ports are the connected input names."""
```

`ConfigValidator.validate_contracts` calls it with the method's bound input names, so the error appears at pipeline validation instead of at run time. `validate_inputs` also calls it with the ports that received a value, so methods run from Python get the same check. `molecule_filter` uses it to require `binding_site` when `position_mode` or a box metric is in use.

## Workflow Builder Hints

The workflow builder (`python -m rapier.workflow_builder`) builds its palette from the registry. A few optional class attributes refine how a method appears there; the rest of Rapier ignores them:

| Attribute | Effect |
|-----------|--------|
| `display_name` | Card and palette label (default: `register_name` in title case) |
| `category` | Palette group (default: the `rapier.methods.<category>` subpackage) |
| `ui_hints` | `{field: {...}}`, merged into that config field's description |

Recognized `ui_hints` keys:

- `widget`: override the inferred widget (`select`, `checkbox`, `number`, `json`, `text`). `"rules"` gives the criteria rule editor and also reads `operators`, `metrics` and `suggestions`.
- `suggestions`: list of values offered as autocomplete.
- `card`: `True` shows the field's value as a summary chip on the canvas card when it differs from the default.
- `card_format`: chip template, with `{value}` and other config fields as placeholders (e.g. `"top {value}% of {percent_of}"`).

Field widgets are otherwise inferred from `ConfigField`: `choices` gives a select, `bool` a checkbox, numbers a number input, and lists/dicts a JSON editor. The `description` becomes the help text, so keep it short. Type descriptions come from the data classes (the first docstring line), and sockets are coloured by the type's root (`molecule`, `table`, ...), so a new subtype appears in the builder without extra work. A subclass that only changes `register_name`/`display_name` (like `boltz2_prediction`) is shown as an alias of its parent.

## Environment Requirements

Declare software dependencies with `EnvironmentSpec`:

```python
environment = EnvironmentSpec(
    executables=["vina", "mk_prepare_receptor.py", "python"],  # must be on PATH
    python_packages=["rdkit"],                                 # must be importable
    modules=["conda/4.10", "cuda/11.2"],                       # SLURM modules (descriptive)
    conda_env="screening-env",                                 # descriptive
    env_variables={},                                          # descriptive
)
```

The default `validate_environment()` checks `executables` and `python_packages`, and `TaskRunner.prepare_task` calls it before submitting. The check runs on the machine that submits the job, not on the compute node. `modules`, `conda_env` and `env_variables` are not applied; SLURM scripts take `modules`, `conda_env` and `env_vars` from task metadata. Override `validate_environment()` for checks the spec can't express or for friendlier messages, as `vina_docking` does:

```python
def validate_environment(self) -> Tuple[bool, List[str]]:
    from rapier.methods.utils import check_executable

    errors = []
    if not check_executable('vina'):
        errors.append("AutoDock Vina (vina) not found in PATH")
    try:
        import prody  # noqa: F401
    except ImportError:
        errors.append("ProDy Python package not found")
    return not errors, errors
```

## Resource Specifications

Declare default resources with `ResourceSpec`:

```python
resources = ResourceSpec(
    cpus=4,             # CPU cores
    memory="8GB",       # e.g. "8GB" or "8000MB"
    time="02:00:00",    # HH:MM:SS
    partition="cpu",
    gpus=0,
    gpu_type=None,      # e.g. "A100"
    nodes=1,
)
```

`ResourceSpec.to_slurm_directives()` renders the `#SBATCH` lines. The values are shown in the workflow builder and used as fallbacks by some methods (Vina uses `cpus` when `cpu` is unset). SLURM submission itself uses the `resources:` block of the method entry in the pipeline YAML (available to `run` as `ctx.resources`), so document sensible values for users.

**Guidelines**:
- Be conservative—request what you'll actually use
- Time limits prevent runaway jobs
- Consider parallel execution for large datasets

## Running on SLURM

The task runner submits `get_command(task)` as the job body. Every method gets a working default from the generic runner (`rapier.methods.runner`), so a method that only implements `run` executes on SLURM exactly as it does locally:

1. `get_command(task)` writes `<work_dir>/task.json` (method module and class, step name, config, the already-staged inputs, resources) and returns `<python> -m rapier.methods.runner --task <work_dir>/task.json`, using the submitting interpreter's absolute path.
2. The job rebuilds the method from its import path, runs the same `execute(task)` and writes the status, error and formatted outputs to `<work_dir>/outputs/outputs.json`. A failed run exits non-zero, so SLURM reports the job as failed.
3. `collect_outputs(task)` reads `outputs.json` back, raising the recorded error if the run failed. Without the file it returns `task.outputs` from a local `execute`.

Because the job imports the method by module path, define it in an importable module (not `__main__` or a notebook), and make the same Python path and packages available on the compute nodes.

Override the pair when a method needs its own job runner, as `vina_docking` and `boltz2` do with manifest runners:

1. `get_command(task)` calls `self.format_inputs(task.inputs, ctx.input_dir)` (with `ctx = RunContext.for_task(task)`), writes a JSON manifest (inputs, config, work dir) into `task.work_dir`, and returns `python -m your.runner_module --manifest <path>`.
2. `run(inputs, ctx)` writes the same manifest and calls the runner in-process, so local and SLURM runs are identical.
3. `collect_outputs(task)` reads the files the runner wrote under `outputs/` and returns `self.format_outputs({...}, ctx)`.

Keep the runner module importable without RDKit or other heavy dependencies where you can, and write a summary JSON so `collect_outputs` can report partial failures through artifact metadata.

## Parallelizable and Checkpointable Methods

The mixins are combined with `Method`, for example `class SminaDocking(Method, ParallelizableMethod)`. A method with several internal stages (Vina prepares the receptor, then docks) runs them inside one `run` call and one job, passing files through `ctx.work_dir` or `ctx.task.intermediate_outputs`.

`ParallelizableMethod.split_inputs(inputs, num_chunks)` and `merge_outputs(chunk_outputs)` define how work would be divided. The engine does not call them automatically yet. Parallel docking is expressed in the pipeline instead, by running several method entries side by side and pooling their outputs (see `examples/pipelines/04_parallel_docking.yaml` and [Pipeline Configuration](pipeline_configuration.md)).

`CheckpointableMethod` adds `save_checkpoint(task, data)`, `load_checkpoint(task)` and `should_checkpoint(elapsed)`; nothing in the engine calls them, so `run` decides when to save and resume.

## Testing Your Method

### Unit Testing

Call the method directly with raw values (paths, SMILES strings, dicts or artifacts) and check the typed outputs:

```python
# tests/methods/test_mw_filter.py
import pytest

from rapier import Task
from rapier.core.enums import TaskStatus
from my_package.mw_filter import MolecularWeightFilter


def test_config_is_validated():
    with pytest.raises(ValueError):
        MolecularWeightFilter(config={"mw_max": "heavy"})
    with pytest.raises(ValueError):
        MolecularWeightFilter(config={"mw_min": 600})


def test_execute(tmp_path):
    method = MolecularWeightFilter(config={"mw_max": 90})
    task = Task("mw", method, inputs={"molecules": ["CCO ethanol", "c1ccccc1O phenol"]})
    task.work_dir = tmp_path / "work"

    assert method.execute(task) == TaskStatus.COMPLETED, task.error_message
    assert [row["id"] for row in task.outputs["properties"].records()] == ["ethanol", "phenol"]
    assert task.outputs["filtered"].ids() == ["ethanol"]
```

`execute` has already validated the outputs against the output ports. Mark tests that need optional tools with the markers in `pyproject.toml` (`rdkit`, `vina`, `slurm`, `boltz2_vina`) so they can be skipped where the tool is absent.

### Pipeline Validation

Wire the method into a small pipeline and validate it before running:

```bash
python scripts/validate_config.py my_pipeline.yaml
```

This catches unknown config keys, type errors, unknown or unbound ports, incompatible artifact types without a converter, and `validate_bindings` errors.

## Registering Your Method

### Auto-Discovery

Add `register_name` to your class:

```python
class MyMethod(Method):
    register_name = "my_method"  # Used as `type:` in pipeline configs
```

`MethodRegistry.auto_discover()` imports every module under `rapier.methods` and registers each `Method` subclass with a `register_name`. It only scans that package, so a method outside it must be registered manually. Import errors are printed as warnings and skipped.

### Manual Registration

Register explicitly:

```python
from rapier.methods import get_registry

registry = get_registry()  # MethodRegistry() returns the same singleton
registry.register('my_method', MyMethod, metadata={
    'description': 'My custom screening method',
})
```

`register` raises if the name is taken or the class is not a `Method` subclass. `get_info(name)` returns the metadata plus `class` and `module`; when no description is given, the first line of the class docstring is used.

### Using in Pipelines

Once registered, use it in pipeline configs:

```yaml
name: mw_then_dock

inputs:
  ligands:
    type: molecule.collection
    format: sdf
    source: data/ligands.sdf      # file inputs use source:, inline ones value:

methods:
  - id: mw
    type: mw_filter           # Your register_name
    inputs:
      molecules: pipeline.ligands
    config:
      mw_max: 500

  - id: dock
    type: vina_docking
    inputs:
      ligands: mw.filtered     # <method id>.<output port>
      receptor: pipeline.receptor
```

## Migrating Older Methods

`BaseMethod`, `SingleStepMethod`, `MultiStepMethod`/`MethodStep`, `contract_version`, `from_config`, `normalize_config` and the file-list `InputSpec(required_files=...)` / `OutputSpec(files=...)` forms have been removed. To port a method: subclass `Method`; move the specs into the `inputs`/`outputs`/`config_fields`/`resources`/`environment` class attributes; move the body of `execute` (or of every step) into `run`, returning `{port: value}` instead of setting `task.outputs`; and move content checks from `validate_inputs` into `check_inputs`. Score tables should use `id`/`pose` keys (readers still accept `ligand`/`mode`).

## Best Practices

1. **Validate Early**: Put content checks in `check_inputs` so they run before heavy computation
2. **Use Temp Directories**: Write intermediate files to `ctx.work_dir / "temp"`
3. **Handle Errors Gracefully**: Raise `MethodError` with a clear message; partial per-molecule failures belong in a report
4. **Log Progress**: Use print statements or logging—they appear in SLURM logs
5. **Clean Output**: Only write final results to `ctx.output_dir`
6. **Document Parameters**: Give every `ConfigField` a short `description`
7. **Key Results by Id**: Name molecules with `record.id` and key score rows by `id`/`pose`, with `directions`, so they join downstream
8. **Test with Real Data**: Don't just test with toy examples

## Common Patterns

### Running External Tools

```python
from rapier.methods.utils import run_command

returncode, stdout, stderr = run_command(
    ['vina', '--receptor', str(inputs['receptor'].path), '--ligand', str(ligand)],
    cwd=ctx.work_dir,
)
if returncode != 0:
    raise MethodError(f"Vina failed: {stderr}")
```

Other helpers in `rapier.methods.utils`: `check_executable(s)`, `validate_pdb_file`, `validate_sdf_file`, `parse_vina_output`, `parse_box_spec`, `rdkit_molecules`, `write_rdkit_sdf`, `resolve_box` (box from a `binding_site` input, else `box_center`/`box_size` config) and `DOCKING_SCORE_COLUMNS`.

### Processing with RDKit

```python
from rapier.methods.utils import rdkit_molecules, write_rdkit_sdf

results = []
for record, mol in rdkit_molecules(inputs["ligands"]):  # SDF or SMILES input
    if mol is None:
        continue
    # Process mol; its _Name is record.name and SDF properties are kept
    results.append(mol)

path = write_rdkit_sdf(results, ctx.output_dir / "processed.sdf")
```

### Logging Results

Return a dict on a `Report` output port; it is written to `outputs/<port>.json`:

```python
outputs = {
    "log": {  # declared as PortSpec(Report, "json")
        "total_molecules": total,
        "successful": success_count,
        "failed": fail_count,
        "parameters": self.config,
    },
}
```

## Need Help?

- Check existing methods in `rapier/methods/` for examples; `screening/molecule_filter.py` shows `ui_hints`, `migrate_config`, `check_config` and `validate_bindings`
- Use the template: `examples/methods/custom_method_template.py`
- See the [Method Reference](method_reference.md) for artifact types and built-in contracts
- Test incrementally—validate, then execute, then integrate
