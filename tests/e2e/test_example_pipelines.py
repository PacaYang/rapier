"""End-to-end test that all shipped example pipelines are valid.

Ensures the configurations in examples/pipelines/ parse, validate, and build
into Pipeline objects. Keeps documentation examples honest.
"""

from pathlib import Path

import pytest

from rapier.config.parser import ConfigParser
from rapier.config.validator import ConfigValidator
from rapier.core.pipeline import Pipeline


EXAMPLES_DIR = Path(__file__).parent.parent.parent / "examples" / "pipelines"

EXAMPLE_CONFIGS = sorted(EXAMPLES_DIR.glob("*.yaml"))


@pytest.mark.skipif(not EXAMPLE_CONFIGS, reason="No example pipelines found")
@pytest.mark.parametrize("config_path", EXAMPLE_CONFIGS, ids=lambda p: p.name)
def test_example_config_is_valid(config_path):
    config = ConfigParser().parse_file(str(config_path))
    is_valid, errors = ConfigValidator().validate(config)
    assert is_valid, f"{config_path.name}: {errors}"


@pytest.mark.skipif(not EXAMPLE_CONFIGS, reason="No example pipelines found")
@pytest.mark.parametrize("config_path", EXAMPLE_CONFIGS, ids=lambda p: p.name)
def test_example_config_builds_pipeline(config_path):
    config = ConfigParser().parse_file(str(config_path))
    pipeline = Pipeline.from_config(config)
    assert isinstance(pipeline, Pipeline)
    assert len(pipeline.tasks) == len(config.methods)
