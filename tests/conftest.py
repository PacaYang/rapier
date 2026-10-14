"""Pytest configuration and shared fixtures for the Rapier test suite.

Provides reusable fixtures:
    - temp_project_dir: a temporary directory for project files
    - dummy_method / failing_method: concrete in-memory methods for testing
    - sample_pipeline: a small serial pipeline (A -> B -> C)
    - parallel_pipeline: a parallel pipeline ([A, B] -> pool -> C)
    - mock_slurm_client: a SlurmClient in dry-run mode
    - sample_config_dir / sample_data_dir: paths to checked-in fixtures
"""

import sys
from pathlib import Path

import pytest

# Ensure the package root is importable when tests run from any directory.
PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from rapier.core.method import Method
from rapier.core.task import Task
from rapier.core.pipeline import Pipeline
from rapier.core.enums import TaskStatus, FailurePolicy
from rapier.core.specs import ResourceSpec


# ---------------------------------------------------------------------------
# Concrete test method implementations
# ---------------------------------------------------------------------------

class DummyMethod(Method):
    """A trivial port-less method that succeeds immediately.

    Records the number of times it was executed so tests can assert on it.
    It overrides ``execute`` so orchestration tests never touch the filesystem.
    """

    register_name = "dummy"
    resources = ResourceSpec(cpus=1, memory="1GB", time="00:10:00")

    def __init__(self, name="dummy", config=None, **kwargs):
        super().__init__(name=name, config=config, **kwargs)
        self.execute_count = 0

    def run(self, inputs, ctx):
        return {}

    def execute(self, task: Task) -> TaskStatus:
        self.execute_count += 1
        task.outputs = {"result": "ok"}
        return TaskStatus.COMPLETED


class FailingMethod(Method):
    """A method that always fails, for testing failure handling."""

    register_name = "failing"

    def run(self, inputs, ctx):
        return {}

    def execute(self, task: Task) -> TaskStatus:
        task.error_message = "intentional failure"
        return TaskStatus.FAILED


# ---------------------------------------------------------------------------
# Filesystem fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def temp_project_dir(tmp_path):
    """Provide a temporary project directory with standard subfolders."""
    project = tmp_path / "project"
    for sub in ("inputs", "intermediates", "outputs", "logs"):
        (project / sub).mkdir(parents=True, exist_ok=True)
    return project


@pytest.fixture
def fixtures_dir():
    """Path to the checked-in test fixtures directory."""
    return Path(__file__).parent / "fixtures"


@pytest.fixture
def sample_config_dir(fixtures_dir):
    """Path to the sample pipeline configuration directory."""
    return fixtures_dir / "sample_configs"


@pytest.fixture
def sample_data_dir(fixtures_dir):
    """Path to the sample molecular data directory."""
    return fixtures_dir / "sample_data"


# ---------------------------------------------------------------------------
# Method fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def dummy_method():
    """A fresh DummyMethod instance."""
    return DummyMethod()


@pytest.fixture
def failing_method():
    """A fresh FailingMethod instance."""
    return FailingMethod()


@pytest.fixture
def sample_methods():
    """A dict of named sample method instances."""
    return {
        "filter": DummyMethod(name="filter"),
        "dock": DummyMethod(name="dock"),
        "score": DummyMethod(name="score"),
    }


# ---------------------------------------------------------------------------
# Pipeline fixtures
# ---------------------------------------------------------------------------

def _make_task(task_id, method, failure_policy=FailurePolicy.STOP):
    task = Task(task_id=task_id, method=method, inputs={})
    task.failure_policy = failure_policy
    return task


@pytest.fixture
def sample_pipeline(sample_methods):
    """A simple serial pipeline: filter -> dock -> score."""
    pipeline = Pipeline(name="sample", description="serial test pipeline")
    pipeline.add_task(_make_task("filter", sample_methods["filter"]))
    pipeline.add_task(_make_task("dock", sample_methods["dock"]))
    pipeline.add_task(_make_task("score", sample_methods["score"]))
    pipeline.add_connection("filter", "dock")
    pipeline.add_connection("dock", "score")
    return pipeline


@pytest.fixture
def parallel_pipeline():
    """A parallel pipeline: [vina, smina] -> pool -> analyze."""
    pipeline = Pipeline(name="parallel", description="parallel test pipeline")
    pipeline.add_task(_make_task("vina", DummyMethod(name="vina")))
    pipeline.add_task(_make_task("smina", DummyMethod(name="smina")))
    pipeline.add_task(_make_task("pool", DummyMethod(name="pool")))
    pipeline.add_task(_make_task("analyze", DummyMethod(name="analyze")))
    pipeline.add_connection("vina", "pool")
    pipeline.add_connection("smina", "pool")
    pipeline.add_connection("pool", "analyze")
    pipeline.add_pooling_rule("pool", "all")
    return pipeline


# ---------------------------------------------------------------------------
# SLURM fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_slurm_client():
    """A SlurmClient in dry-run mode (no real cluster needed)."""
    from rapier.slurm.client import SlurmClient
    return SlurmClient(dry_run=True)


# ---------------------------------------------------------------------------
# Markers
# ---------------------------------------------------------------------------

def pytest_configure(config):
    """Register custom markers."""
    config.addinivalue_line(
        "markers", "slurm: mark test as requiring a real SLURM installation"
    )
    config.addinivalue_line(
        "markers", "rdkit: mark test as requiring RDKit"
    )
    config.addinivalue_line(
        "markers", "vina: mark test as requiring AutoDock Vina"
    )
