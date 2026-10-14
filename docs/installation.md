# Installation

This guide covers installing Rapier and its dependencies.

## System Requirements

- **Python**: 3.8 or newer
- **Operating system**: Linux (recommended for HPC/SLURM use), macOS for development
- **SLURM**: required only for running pipelines on a cluster; development and
  testing work without it
- **Disk**: enough space for input libraries and intermediate docking files

## Installing Rapier

### From source (development)

```bash
git clone <your-repo-url> rapier
cd rapier
pip install -e .
```

This installs the core dependencies (`networkx`, `pyyaml`, `jinja2`) and makes
the `rapier` package importable.

### Development dependencies

To run the test suite:

```bash
pip install -e ".[dev]"
# or
pip install -r requirements-dev.txt
```

This adds `pytest`, `pytest-cov`, `black`, and `flake8`.

### Workflow builder

The visual pipeline editor needs Flask:

```bash
pip install -e ".[ui]"
python -m rapier.workflow_builder     # http://127.0.0.1:8765
```

## Core Python Dependencies

| Package | Purpose | Required |
|---------|---------|----------|
| `networkx` | DAG construction and topological sorting | Yes |
| `pyyaml` | Pipeline configuration parsing | Yes |
| `jinja2` | SLURM script generation | Yes |
| `pytest`, `pytest-cov`, `black`, `flake8` | Testing and linting | `dev` extra |
| `flask` | Workflow builder | `ui` extra |
| `rich` | Richer `CLIMonitor` output | Optional |
| `flask-cors`, `flask-socketio` | Logging `Dashboard` / `DashboardWithWebSocket` | Optional |
| `requests` | Alert `webhook_handler` | Optional |

## External Scientific Tools

The built-in methods shell out to standard cheminformatics tools. Install only
those required by the methods you use.

### Optional normalization providers

The normalized artifact core has no chemistry or structure-parser dependency.
Install providers only for conversions used by your pipelines:

```bash
pip install rdkit gemmi
```

RDKit enables SMILES/SDF adapters and Gemmi enables PDB/mmCIF adapters. When
`obabel` is installed, Rapier registers it as a lower-priority fallback.
Configuration validation reports a missing provider before execution when a
port requires a conversion that is unavailable.

### RDKit

Needed by `ligand_filter`, `similarity_search`, and `ligand_prep`.

```bash
conda install -c conda-forge rdkit
# or
pip install rdkit
```

### AutoDock Vina

Needed by `vina_docking`. Rapier uses the official [AutoDock Vina](https://github.com/ccsb-scripps/AutoDock-Vina)
command-line executable and [Meeko](https://github.com/forlilab/Meeko)
preparation/export tools. The `vina` Python package
alone provides Python bindings and does not guarantee that the `vina`
executable is installed.

```bash
conda install -c conda-forge vina meeko gemmi prody
# or download from https://vina.scripps.edu/
# pip alternative: pip install -U numpy scipy rdkit vina meeko gemmi prody
```

`vina_docking` also checks that ProDy is importable, because Meeko's receptor
preparation needs it. Verify the complete environment before submitting a
pipeline:

```bash
command -v vina
command -v mk_prepare_receptor.py
command -v mk_prepare_ligand.py
command -v mk_export.py
python -c "import prody"
```

### Smina

Needed by `smina_docking` (which also needs Open Babel).

```bash
conda install -c conda-forge smina
```

### Open Babel

Needed by `protein_prep` and `smina_docking`, and used as a fallback format
converter. It is not used by the Vina integration for PDBQT preparation or pose export because converting
PDBQT back through Open Babel can lose bond orders and formal charges. Meeko's
`mk_export.py` is used instead.

```bash
conda install -c conda-forge openbabel
```

### Boltz-2

Needed by `boltz2` for biomolecular structure prediction and optional
protein-ligand affinity prediction. Install the official package in a fresh
environment; use the CUDA extra on NVIDIA GPU nodes:

```bash
pip install -U "boltz[cuda]"
# CPU-only alternative:
pip install -U boltz
```

Verify the CLI before submitting a pipeline:

```bash
command -v boltz
boltz predict --help
```

Boltz can use the remote MSA service with `use_msa_server: true` (the default),
or you can provide normalized `.a3m`/CSV MSA artifacts. The MSA server may
require authentication and network access from the compute node.

The Boltz-2 → Vina handoff (`emit_docking_inputs: true`) additionally requires
Gemmi to read the predicted PDB/mmCIF, remove ligand chains, write the receptor,
and calculate the normalized docking box:

```bash
pip install gemmi
```

## SLURM Configuration

Rapier interacts with SLURM through the standard CLI commands (`sbatch`,
`squeue`, `sacct`, `scancel`). No special configuration is required beyond
having these on your `PATH`. To test without a cluster, the platform supports a
**dry-run** mode that simulates job submission.

## Verifying the Installation

```bash
# Confirm the package imports
python3 -c "import rapier; print(rapier.__version__)"

# Validate an example pipeline
python3 scripts/validate_config.py examples/pipelines/01_ligand_filtering.yaml --build

# Run the test suite (needs the dev extra)
scripts/run_tests.sh unit
```

Tests that need SLURM or external tools are marked `slurm`, `rdkit`, `vina`, or
`boltz2_vina` and skip when the tools are missing. See
[Running the Tests](../README.md#running-the-tests).

If the unit tests pass and the example validates, you're ready to go. Continue
to the [Getting Started](getting_started.md) guide.

## Troubleshooting

If you hit problems during installation, see the
[Troubleshooting guide](troubleshooting.md).
