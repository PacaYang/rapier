# Stage 8: Testing & Documentation

## Goal
Develop comprehensive tests covering unit tests for core classes, integration tests for SLURM interaction, and end-to-end pipeline tests. Write user documentation including getting started guide, pipeline configuration reference, method development guide, and troubleshooting. Create example pipelines demonstrating common screening workflows.

## Files to Create

### Testing Files

#### `tests/__init__.py`
- Test suite initialization
- Shared test fixtures and utilities

#### `tests/conftest.py`
- Pytest configuration and fixtures
- Fixtures:
  - `temp_project_dir` - temporary project directory
  - `mock_slurm_client` - mock SLURM for testing without cluster
  - `sample_pipeline` - example pipeline for testing
  - `sample_methods` - example method instances

#### `tests/unit/__init__.py`
- Unit tests module

#### `tests/unit/test_core.py`
- Tests for core classes: BaseMethod, Task, Pipeline, ExecutionGraph
- Test task creation, status transitions
- Test pipeline construction, validation
- Test execution graph building, dependency resolution

#### `tests/unit/test_specs.py`
- Tests for specification classes
- Test InputSpec, OutputSpec, EnvironmentSpec, ResourceSpec
- Test validation logic
- Test serialization/deserialization

#### `tests/unit/test_config_parser.py`
- Tests for configuration parsing
- Test YAML parsing
- Test validation of pipeline configs
- Test error handling for invalid configs
- Test variable substitution

#### `tests/unit/test_execution_graph.py`
- Tests for execution graph operations
- Test DAG construction
- Test cycle detection
- Test topological sorting
- Test dependency resolution

#### `tests/unit/test_scheduler.py`
- Tests for task scheduler
- Test ready task identification
- Test completion handling
- Test failure policy enforcement

#### `tests/unit/test_pooler.py`
- Tests for result pooling
- Test all pooling rules: any, all, majority, best
- Test with different result types

#### `tests/integration/__init__.py`
- Integration tests module

#### `tests/integration/test_slurm.py`
- Tests for SLURM integration (mock or real if available)
- Test job script generation
- Test job submission (if SLURM available, else mock)
- Test job status polling
- Test dependency string building

#### `tests/integration/test_file_management.py`
- Tests for file staging, collection, cleanup
- Test with real filesystem operations
- Test symlink vs copy behavior
- Test cleanup policies

#### `tests/integration/test_logging.py`
- Tests for logging system
- Test log file creation
- Test structured logging format
- Test log aggregation

#### `tests/e2e/__init__.py`
- End-to-end tests module

#### `tests/e2e/test_simple_pipeline.py`
- E2E test for simple serial pipeline
- Create config, execute pipeline, verify results
- Uses mock methods or fast real methods

#### `tests/e2e/test_parallel_pipeline.py`
- E2E test for parallel pipeline with pooling
- Tests the A,D → Pooling → C pattern

#### `tests/e2e/test_failure_handling.py`
- E2E test for failure policies
- Test STOP, CONTINUE, RETRY policies
- Verify downstream task handling

#### `tests/e2e/test_vina_pipeline.py`
- E2E test with real Vina docking (if available)
- Skip if Vina not installed
- Tests multi-step method execution

#### `tests/fixtures/sample_configs/simple.yaml`
- Sample pipeline configuration for testing
- A → B → C serial pipeline

#### `tests/fixtures/sample_configs/parallel.yaml`
- Sample configuration for parallel pipeline

#### `tests/fixtures/sample_data/receptor.pdb`
- Small test receptor PDB file

#### `tests/fixtures/sample_data/ligands.sdf`
- Small test ligand SDF file (2-3 molecules)

### Documentation Files

#### `README.md`
- Project overview and quick start
- Installation instructions
- Basic usage example
- Links to detailed documentation

#### `docs/installation.md`
- Detailed installation guide
- System requirements
- Python dependencies
- SLURM configuration
- External tool installation (Vina, RDKit, etc.)

#### `docs/getting_started.md`
- Tutorial for first-time users
- Create your first pipeline
- Run a simple virtual screening
- Understand the outputs

#### `docs/pipeline_configuration.md`
- Complete pipeline configuration reference
- All configuration options explained
- YAML syntax guide
- Examples for each option

#### `docs/method_reference.md`
- Documentation for all built-in methods
- For each method:
  - Purpose and algorithm description
  - Input requirements
  - Output format
  - Parameters and defaults
  - Resource requirements
  - Example usage

#### `docs/architecture.md`
- System architecture overview
- Component descriptions
- Data flow diagrams
- Design decisions and rationale

#### `docs/troubleshooting.md`
- Common issues and solutions
- SLURM-related problems
- Environment issues
- File permission errors
- Debugging techniques

#### `docs/api_reference.md`
- API documentation for developers
- Core classes and methods
- Extension points for customization

#### `docs/faq.md`
- Frequently asked questions
- How do I...?
- Why does...?
- What's the difference between...?

### Example Pipelines

#### `examples/pipelines/01_ligand_filtering.yaml`
- Simple pipeline: filter ligands by properties
- Good first example for users

#### `examples/pipelines/02_similarity_screening.yaml`
- Similarity search against database
- Demonstrates result ranking

#### `examples/pipelines/03_vina_docking.yaml`
- Complete Vina docking workflow
- Prep → Dock → Analyze
- Shows multi-step method usage

#### `examples/pipelines/04_parallel_docking.yaml`
- Parallel docking with multiple methods
- Vina and Smina in parallel → Pooling → Analysis
- Matches user's example topology

#### `examples/pipelines/05_failure_handling.yaml`
- Demonstrates failure policies
- Some tasks allowed to fail with continue policy

#### `examples/pipelines/README.md`
- Overview of example pipelines
- What each example demonstrates
- How to run them

### Scripts and Tools

#### `scripts/run_tests.sh`
- Script to run test suite
- Options for unit, integration, e2e tests
- Coverage reporting

#### `scripts/generate_docs.sh`
- Generate API documentation with Sphinx
- Build HTML/PDF docs

#### `scripts/validate_config.py`
- Command-line tool to validate pipeline configs
- Usage: `python validate_config.py pipeline.yaml`

### Package Metadata

#### `pyproject.toml`
- Python package configuration
- Dependencies, entry points, metadata
- Build system configuration

#### `setup.py` (if needed for compatibility)
- Alternative package setup script

#### `requirements.txt`
- Core dependencies

#### `requirements-dev.txt`
- Development dependencies (pytest, sphinx, etc.)

## Files to Modify

### `README.md` (existing)
- Update with comprehensive project information
- Add badges (tests passing, coverage, version)
- Add usage examples

## Implementation Order

1. Create pytest configuration (`conftest.py`)
2. Write unit tests for core classes (`test_core.py`, `test_specs.py`)
3. Write unit tests for configuration (`test_config_parser.py`)
4. Write unit tests for execution components (`test_scheduler.py`, `test_pooler.py`)
5. Create test fixtures (sample configs and data)
6. Write integration tests (`test_slurm.py`, `test_file_management.py`, `test_logging.py`)
7. Write end-to-end tests (`test_simple_pipeline.py`, etc.)
8. Write installation guide (`installation.md`)
9. Write getting started guide (`getting_started.md`)
10. Write configuration reference (`pipeline_configuration.md`)
11. Write method reference (`method_reference.md`)
12. Write architecture documentation (`architecture.md`)
13. Write troubleshooting guide (`troubleshooting.md`)
14. Create example pipelines with documentation
15. Generate API reference (`api_reference.md`)
16. Write FAQ (`faq.md`)
17. Create package metadata (`pyproject.toml`, `requirements.txt`)
18. Create utility scripts (`run_tests.sh`, `validate_config.py`)
19. Update main README

## Verification Steps

```bash
# Run unit tests
pytest tests/unit/ -v
# Expected: All unit tests pass

# Run integration tests
pytest tests/integration/ -v
# Expected: Integration tests pass (or skip if SLURM unavailable)

# Run end-to-end tests
pytest tests/e2e/ -v
# Expected: E2E tests pass

# Run full test suite with coverage
pytest --cov=rapier --cov-report=html --cov-report=term
# Expected: >80% code coverage

# Test specific components
pytest tests/unit/test_core.py::test_pipeline_construction -v
pytest tests/integration/test_slurm.py::test_job_submission -v

# Validate example configurations
python scripts/validate_config.py examples/pipelines/01_ligand_filtering.yaml
# Expected: Configuration valid

python scripts/validate_config.py examples/pipelines/04_parallel_docking.yaml
# Expected: Configuration valid

# Generate documentation
cd docs
sphinx-build -b html . _build/html
# Expected: HTML docs generated without errors

# Install package in development mode
pip install -e .
# Expected: Package installs successfully

# Test CLI entry point (if implemented)
rapier --version
rapier validate examples/pipelines/01_ligand_filtering.yaml
# Expected: Commands work

# Run example pipeline (dry-run)
rapier run examples/pipelines/01_ligand_filtering.yaml --dry-run
# Expected: Pipeline validates and shows execution plan
```

## Key Decisions

**1. Pytest as Testing Framework**
- Industry standard for Python testing
- Rich fixture system
- Excellent plugin ecosystem (coverage, mocking, etc.)
- Good integration with CI/CD

**2. Three-Tier Test Strategy**
- **Unit tests**: Fast, isolated, test individual components
- **Integration tests**: Test component interactions, may use mocks
- **E2E tests**: Test complete workflows, closer to real usage
- Run all levels in CI, run unit tests locally during development

**3. Mock SLURM for Testing**
- Most developers don't have SLURM locally
- Mock SLURM client for unit/integration tests
- Real SLURM tests marked with `@pytest.mark.slurm` and skipped if unavailable
- CI can run on SLURM-equipped runner for full testing

**4. Test Fixtures for Reproducibility**
- Small sample data files checked into repo
- Consistent test data across all test runs
- Fast test execution (small datasets)

**5. Documentation Structure**
- **Tutorials** (getting_started.md): learning-oriented
- **How-to guides** (troubleshooting.md): problem-oriented
- **Reference** (api_reference.md, method_reference.md): information-oriented
- **Explanation** (architecture.md): understanding-oriented
- Follows Diátaxis documentation framework

**6. Example-Driven Documentation**
- Every feature has a working example
- Examples in examples/ directory are tested
- Documentation references real, tested code
- Users can copy-paste and modify examples

**7. Sphinx for API Documentation**
- Auto-generate API docs from docstrings
- Supports multiple output formats (HTML, PDF)
- Cross-referencing between docs and code
- Standard in scientific Python community

**8. Coverage Targets**
- Target >80% overall coverage
- Core components (engine, scheduler) should be >90%
- Allow lower coverage for UI/logging components
- Track coverage over time, prevent regressions

**9. Continuous Integration**
- Run tests on every commit
- Test on multiple Python versions (3.8, 3.9, 3.10, 3.11)
- Generate coverage reports
- Deploy docs automatically

**10. Package Distribution**
- Use modern Python packaging (pyproject.toml)
- Publish to PyPI for easy installation: `pip install rapier`
- Include all necessary package data (templates, schemas)
- Semantic versioning for releases
