# Rapier

**Modular Virtual Screening Platform for Molecule Discovery**

A SLURM-based platform for building customizable virtual screening pipelines
with robust dependency management, comprehensive monitoring, and smart file
management.

## Features

- 🧩 **Modular Design** — plugin-based architecture with a method registry
- 🔀 **Flexible Pipelines** — serial, parallel, and pooling execution patterns
- 🎯 **SLURM Integration** — native HPC job submission, monitoring, and dependencies (with a dry-run mode for local development)
- 📊 **Comprehensive Monitoring** — multi-level logging and dashboards
- 🔄 **Robust Failure Handling** — `stop`, `continue`, and `retry` policies
- 📁 **Smart File Management** — automatic cleanup that preserves important outputs
- 🧬 **Typed, Connectable Methods** — one `Method` base class; ports carry typed data (`Molecules`, `Poses`, `Scores`, ...) and score tables share an `id` key, so any compatible methods chain
- 🗺️ **Visual Workflow Builder** — drag-and-connect pipeline editor with typed sockets and live validation
- 🧪 **Tested** — unit, integration, and end-to-end test suites

## Installation

```bash
git clone <repository-url>
cd rapier
pip install -e .            # core install
pip install -e ".[dev]"     # with test/dev tools
pip install -e ".[ui]"      # with the visual workflow builder (Flask)
```

See [docs/installation.md](docs/installation.md) for external tools (RDKit,
AutoDock Vina, Meeko, Boltz-2, Smina, Open Babel) and SLURM notes.

## Quick Start

Write a pipeline in YAML:

```yaml
name: my_first_pipeline
version: 1.0.0
methods:
  - id: filter
    type: ligand_filter
    inputs:
      ligands: pipeline.ligands
    config:
      mw_max: 500
  - id: search
    type: similarity_search
    inputs:
      queries: pipeline.queries
      database: filter.filtered_ligands
    config:
      similarity_threshold: 0.7

inputs:
  ligands:
    type: molecule.collection
    format: sdf
    source: ligands.sdf
  queries:
    type: molecule.collection
    format: smiles
    source: queries.smi
```

Validate it:

```bash
python scripts/validate_config.py my_first_pipeline.yaml --build
```

Build and run it from Python:

```python
from rapier.config.parser import ConfigParser
from rapier.core.pipeline import Pipeline
from rapier.engine.executor import ExecutionEngine

config = ConfigParser().parse_file("my_first_pipeline.yaml")
pipeline = Pipeline.from_config(config)

engine = ExecutionEngine(max_parallel=4, dry_run=True)  # dry_run simulates SLURM
result = engine.execute(pipeline)
print(result.generate_report())
```

A dry run submits nothing and produces no outputs, so it checks scheduling
and scripts; `search` above fails there because `filter` has no
`filtered_ligands`. With `dry_run=False`, every built-in method runs on the
compute node through `python -m rapier.methods.runner`. After environment
activation, the method checks its dependencies before execution and validates
all collected output ports together afterward. Missing or inconsistent results
fail the task before downstream work can start.

See the [Getting Started guide](docs/getting_started.md) for a full walkthrough.

## Workflow Builder

Rapier includes a browser-based editor for designing pipelines visually.
Pipeline inputs, methods, and outputs are cards on an interactive canvas; you
wire them together by dragging between sockets, and the result exports as a
standard pipeline YAML.

```bash
pip install -e ".[ui]"
python -m rapier.workflow_builder            # http://127.0.0.1:8765
```

| Option | Description |
|--------|-------------|
| `--port N` | Port to serve on (default `8765`) |
| `--host ADDR` | Bind address (default `127.0.0.1`) |
| `--no-demo` | Hide the mock demo methods from the palette |
| `--debug` | Run Flask in debug mode |

**How it works**

- **Sockets come from method contracts.** Every input and output port declared
  in a method's `inputs`/`outputs` becomes a socket, coloured by artifact
  type. Square sockets accept many artifacts, `?` marks optional inputs, and
  unconnected required inputs are highlighted. Data types, including new
  ones such as `molecule.poses` and `table.scores`, come from the
  `rapier.data` classes.
- **Type-checked connections.** While dragging a wire, compatible sockets glow
  green (direct match) or amber (an automatic conversion such as `cif → pdb`
  will run); incompatible ones dim. Compatibility is computed server-side with
  the same rules as `ConfigValidator`, so the editor and CLI always agree.
- **Inspector.** Selecting a card shows a form generated from the method's
  `config_fields` (checkboxes, dropdowns, bounded numbers), resources, failure
  policy, and dropdowns for wiring inputs without dragging. Methods can supply
  richer widgets through a `ui_hints` class attribute; `molecule_filter`
  uses this for its rule editor and card summary chips.
- **Live validation.** Edits are checked with the full pipeline validator;
  errors are attached to the offending card and socket and listed in the
  bottom panel.
- **YAML round trip.** *Import YAML* opens any pipeline file, the *Example*
  menu loads the files in `examples/pipelines/`, and *Export YAML* (or
  `Ctrl/Cmd+S`) produces a file the parser accepts unchanged. Card positions
  are saved under a top-level `ui.layout` key, which the parser ignores. Work
  in progress is autosaved in the browser.

**Controls:** drag from the palette (or press Enter on a palette item) to add a
card · drag empty canvas to pan · scroll to zoom · drag a connected input away
to rewire it · Delete/Backspace removes the selection · arrow keys nudge the
focused card (Shift for larger steps).

**Demo methods.** The builder registers six mock methods (structure
prediction, complex co-folding, sequence design, pocket detection, docking,
rescoring) from `rapier/workflow_builder/demo_methods.py` so you can try
realistic multi-socket workflows without installing any tools. They write
placeholder outputs, are never picked up by `auto_discover()`, and can be
hidden with `--no-demo`.

> **Security:** the builder server has no authentication. It binds to
> localhost by default; don't expose it on a shared network with `--host 0.0.0.0`.

The builder designs, validates, and exports pipelines; running them still goes
through `ExecutionEngine` as shown above.

## Documentation

| Guide | Description |
|-------|-------------|
| [Installation](docs/installation.md) | Install Rapier and its dependencies |
| [Getting Started](docs/getting_started.md) | Your first pipeline, step by step |
| [Pipeline Configuration](docs/pipeline_configuration.md) | Pipeline YAML guide |
| [Config Reference](docs/config_reference.md) | Field-by-field configuration reference |
| [Method Reference](docs/method_reference.md) | All built-in methods |
| [Method Development](docs/method_development_guide.md) | Write your own methods |
| [Architecture](docs/architecture.md) | How the platform works |
| [Logging Guide](docs/logging_guide.md) | Logging, monitoring, dashboards, and alerts |
| [Troubleshooting](docs/troubleshooting.md) | Common problems and fixes |
| [API Reference](docs/api_reference.md) | Public classes and signatures |
| [FAQ](docs/faq.md) | Frequently asked questions |

## Built-in Methods

| Type | Purpose |
|------|---------|
| `ligand_filter` | Filter molecules by drug-likeness properties; reports every molecule's properties as scores |
| `molecule_filter` | Filter and gate poses by upstream scores and binding position |
| `similarity_search` | Fingerprint-based Tanimoto similarity search |
| `ligand_prep` | 3D conformer generation, protonation, minimization |
| `protein_prep` | Receptor preparation and PDBQT conversion |
| `colabfold_msa` | Generate chain-specific protein MSAs through the ColabFold MSA Server |
| `vina_docking` | AutoDock Vina docking with Meeko preparation/export |
| `smina_docking` | Smina docking with custom scoring |
| `boltz2` | Boltz-2 complex and affinity prediction (also registered as `boltz2_prediction`) |
| `af3` | AlphaFold 3 structure prediction with optional supplied MSAs |
| `drug_lamp` | Drug-target interaction prediction with DrugLAMP |
| `nesso` | Nesso protein-ligand affinity prediction |

## Example Pipelines

Ready-to-run configurations live in [`examples/pipelines/`](examples/pipelines/):

- `01_ligand_filtering.yaml` — single-method filtering
- `02_similarity_screening.yaml` — filter then similarity search
- `03_vina_docking.yaml` — prepare, dock, filter
- `04_parallel_docking.yaml` — parallel Vina + Smina with pooling
- `05_failure_handling.yaml` — mixed failure policies
- `06_boltz2_prediction.yaml` — Boltz-2 structure and affinity prediction
- `07_boltz2_vina.yaml` — Boltz-2-predicted receptor and binding box passed to Vina
- `08_docking_funnel.yaml` — two-round Vina docking funnel gated by `molecule_filter`
- `09_af3_prediction.yaml` — AlphaFold 3 structure prediction from protein sequences
- `10_drug_lamp_prediction.yaml` — DrugLAMP protein-ligand interaction prediction
- `11_nesso_prediction.yaml` — Nesso affinity prediction
- `12_colabfold_msa.yaml` — Generate a ColabFold MSA set and reuse it in AF3 and Boltz-2
- `simple_serial.yaml`, `multi_step_method.yaml`, `parallel_pooling.yaml` — minimal serial, multi-step, and pooling topologies

All examples can also be opened in the [workflow builder](#workflow-builder) from its *Example* menu.

## Running the Tests

```bash
pip install -e ".[dev]"
scripts/run_tests.sh             # all tests
scripts/run_tests.sh unit        # unit | integration | e2e | methods | all
scripts/run_tests.sh all --cov   # with a coverage report
```

Tests that need external software carry pytest markers and are skipped when the
software is missing:

| Marker | Requires |
|--------|----------|
| `slurm` | a real SLURM installation (`sbatch`) |
| `rdkit` | RDKit |
| `vina` | AutoDock Vina, Open Babel, and RDKit |
| `boltz2_vina` | Boltz-2, Vina, Meeko, ProDy, network access, and `RAPIER_RUN_BOLTZ2_VINA_E2E=1` |

```bash
python -m pytest -m "not slurm and not rdkit and not vina and not boltz2_vina"   # fast local run
python -m pytest -m vina                                                         # only Vina tests
RAPIER_RUN_BOLTZ2_VINA_E2E=1 python -m pytest -m boltz2_vina                     # expensive Boltz-2 → Vina run
```

## Project Structure

```
rapier/
├── rapier/                    # Main package
│   ├── core/                  # Core classes (Method, Task, Pipeline, Graph)
│   ├── data/                  # Typed data classes (Molecules, Poses, Scores, ...)
│   ├── artifacts/             # Artifact registry, port contracts, converters
│   ├── config/                # YAML configuration system
│   ├── slurm/                 # SLURM integration
│   ├── engine/                # Execution engine
│   ├── logging/               # Logging & monitoring
│   ├── storage/               # File management
│   ├── methods/               # Method library
│   └── workflow_builder/      # Visual pipeline editor (Flask app + static UI)
├── tests/                     # unit / integration / e2e tests + fixtures
├── docs/                      # Documentation
├── examples/                  # Example pipelines and method template
├── scripts/                   # validate_config.py, run_tests.sh, cleanup tools
└── pyproject.toml             # Package configuration
```

## Requirements

- Python 3.8+
- NetworkX, PyYAML, Jinja2
- SLURM (for HPC execution; optional for development)
- Flask (optional, for the workflow builder)
- RDKit, AutoDock Vina, Meeko, Boltz-2, Smina, Open Babel (for specific methods)

(`scripts/run_tests.sh`) and add tests for new functionality. See the
[Method Development Guide](docs/method_development_guide.md) to add new methods.
