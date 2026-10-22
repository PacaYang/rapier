# Repository Guidelines

## Project Structure & Module Organization

Rapier is a Python 3.8+ package. Production code lives in `rapier/`: `core`
defines pipelines and methods, `data` and `artifacts` define typed values and
contracts, `config` parses and validates YAML, `engine` and `slurm` execute
workflows, and `methods`, `logging`, `storage`, and `workflow_builder` provide
the method library, monitoring, file management, and optional Flask UI.
Tests are organized under `tests/unit`, `tests/integration`, `tests/methods`,
and `tests/e2e`. Put reusable examples in `examples/`, documentation in
`docs/`, and repository utilities in `scripts/`.

## Build, Test, and Development Commands

Install the editable package and development tools with:

```bash
pip install -e ".[dev]"
```

Run focused suites with `scripts/run_tests.sh unit`, `integration`, `methods`,
or `e2e`; run everything with `scripts/run_tests.sh all`. Add `--cov` for a
coverage report. For a fast local run without optional external software, use:

```bash
python -m pytest -m "not slurm and not rdkit and not vina and not boltz2_vina"
```

Validate and construct a pipeline before execution with
`python scripts/validate_config.py <pipeline.yaml> --build`. The optional
workflow builder runs with `python -m rapier.workflow_builder`.

## Coding Style & Naming Conventions

Use four-space indentation, type hints where practical, and clear PEP 8
Python. Format changed Python files with `black`; check them with `flake8`.
Use `snake_case` for modules, functions, variables, and YAML keys;
`PascalCase` for classes; and lowercase `snake_case` names for registered
methods (for example, `ligand_filter`). Keep method contracts and validation
logic explicit and update relevant documentation when behavior changes.

## Testing Guidelines

Use pytest names such as `test_config_parser.py`, `test_validates_ports`, and
`TestScheduler`. Add tests beside the affected area, and mark tests requiring
SLURM, RDKit, Vina, or Boltz-2 with the markers declared in `pyproject.toml`.
Do not require those external tools for the default local suite.

## Commit & Pull Request Guidelines

Use short, action-oriented commit subjects that explain the change. Pull
requests should describe user-visible behavior, list validation commands and
external dependencies, and update docs or examples when interfaces change.
Include screenshots for workflow-builder UI changes and call out any SLURM or
scientific-tool setup needed to reproduce the result.

## Security & Configuration Tips

Use `dry_run=True` when validating scheduling locally. The workflow builder has
no authentication, so keep it bound to localhost unless deployment controls
are in place; never commit credentials or private input data.
