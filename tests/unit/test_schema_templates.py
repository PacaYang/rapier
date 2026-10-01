"""Unit tests for config schema validators and template generators."""

import pytest

from rapier.config.schema import (
    validate_pooling_strategy,
    validate_failure_policy,
    validate_resource_spec,
    PIPELINE_SCHEMA,
)
from rapier.config.templates import (
    create_simple_pipeline,
    create_parallel_pipeline,
    create_pooling_pipeline,
    create_diamond_pipeline,
)
from rapier.config.validator import ConfigValidator


class TestSchemaValidators:
    def test_valid_pooling_strategies(self):
        for s in ("any", "all", "majority", "first"):
            assert validate_pooling_strategy(s)

    def test_best_requires_metric(self):
        assert not validate_pooling_strategy("best")
        assert validate_pooling_strategy("best", metric="score")

    def test_invalid_strategy(self):
        assert not validate_pooling_strategy("bogus")

    def test_failure_policy(self):
        assert validate_failure_policy("stop")
        assert validate_failure_policy("continue")
        assert validate_failure_policy("retry")
        assert not validate_failure_policy("explode")

    def test_resource_spec_valid(self):
        assert validate_resource_spec({"cpus": 4, "memory": "8GB", "time_limit": "2h"})

    def test_resource_spec_empty(self):
        assert validate_resource_spec({})

    def test_resource_spec_bad_memory(self):
        assert not validate_resource_spec({"memory": "lots"})

    def test_resource_spec_bad_time(self):
        assert not validate_resource_spec({"time_limit": "soon"})

    def test_schema_has_required_keys(self):
        assert PIPELINE_SCHEMA["required"] == ["name", "methods"]


class TestTemplates:
    def test_simple_pipeline(self):
        config = create_simple_pipeline("p", ["filtering", "docking", "scoring"])
        assert len(config.methods) == 3
        assert len(config.connections) == 2
        # Serial chain
        is_valid, errors = ConfigValidator().validate(config)
        assert is_valid, errors

    def test_parallel_pipeline(self):
        config = create_parallel_pipeline(
            "p",
            initial_method="prep",
            parallel_methods=["vina", "smina"],
            final_method="consensus",
        )
        # initial + 2 parallel + final
        assert len(config.methods) == 4
        is_valid, errors = ConfigValidator().validate(config)
        assert is_valid, errors

    def test_pooling_pipeline(self):
        config = create_pooling_pipeline(
            "p",
            initial_methods=["vina", "smina"],
            pooling_method="consensus",
            pooling_strategy="best",
            metric="score",
            final_method="rescore",
        )
        assert "pooling" in config.pooling_rules
        is_valid, errors = ConfigValidator().validate(config)
        assert is_valid, errors

    def test_diamond_pipeline(self):
        config = create_diamond_pipeline(
            "p",
            preparation_method="prep",
            parallel_methods=["vina", "smina", "autodock"],
            pooling_method="consensus",
            final_method="scoring",
            pooling_strategy="majority",
        )
        # prep + 3 parallel + pool + final
        assert len(config.methods) == 6
        is_valid, errors = ConfigValidator().validate(config)
        assert is_valid, errors

    def test_diamond_pooling_rule_registered(self):
        config = create_diamond_pipeline(
            "p",
            preparation_method="prep",
            parallel_methods=["a", "b"],
            pooling_method="pool",
            final_method="final",
        )
        assert "pooling" in config.pooling_rules
