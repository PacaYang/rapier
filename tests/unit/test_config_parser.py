"""Unit tests for configuration parsing and validation."""

import pytest

from rapier.config.parser import ConfigParser
from rapier.config.pipeline_config import PipelineConfig
from rapier.config.method_config import MethodConfig, ConnectionConfig, PoolingRuleConfig
from rapier.config.validator import ConfigValidator


SIMPLE_YAML = """
name: test_pipeline
description: A test pipeline
version: 1.0.0
failure_policy: stop
methods:
  - id: filter_step
    type: filtering
    parameters:
      max_violations: 1
    resources:
      cpus: 2
      memory: 4GB
  - id: dock_step
    type: vina_dock
    parameters:
      exhaustiveness: 8
connections:
  - from: filter_step
    to: dock_step
    type: serial
"""


class TestConfigParser:
    def test_parse_string(self):
        parser = ConfigParser()
        config = parser.parse_string(SIMPLE_YAML)
        assert isinstance(config, PipelineConfig)
        assert config.name == "test_pipeline"
        assert len(config.methods) == 2
        assert len(config.connections) == 1

    def test_parse_file(self, tmp_path):
        config_file = tmp_path / "pipeline.yaml"
        config_file.write_text(SIMPLE_YAML)
        parser = ConfigParser()
        config = parser.parse_file(str(config_file))
        assert config.name == "test_pipeline"

    def test_parse_missing_file_raises(self):
        parser = ConfigParser()
        with pytest.raises(FileNotFoundError):
            parser.parse_file("/no/such/file.yaml")

    def test_parse_invalid_yaml_raises(self):
        parser = ConfigParser()
        with pytest.raises(ValueError):
            parser.parse_string("name: [unclosed")

    def test_parse_non_dict_raises(self):
        parser = ConfigParser()
        with pytest.raises(ValueError):
            parser.parse_string("- just\n- a\n- list")

    def test_variable_substitution_custom(self):
        parser = ConfigParser()
        parser.set_variable("MYVAR", "substituted")
        yaml_with_var = SIMPLE_YAML + "\nenvironment:\n  note: ${MYVAR}\n"
        config = parser.parse_string(yaml_with_var)
        assert config.environment.get("note") == "substituted"

    def test_project_dir_substitution(self, tmp_path):
        parser = ConfigParser(project_dir=str(tmp_path))
        yaml_with_var = SIMPLE_YAML + "\nenvironment:\n  data: ${PROJECT_DIR}/data\n"
        config = parser.parse_string(yaml_with_var)
        assert str(tmp_path) in config.environment.get("data")


class TestMethodConfig:
    def test_valid(self):
        m = MethodConfig(id="a", type="filtering")
        assert m.id == "a"
        assert m.failure_policy == "stop"

    def test_empty_id_raises(self):
        with pytest.raises(ValueError):
            MethodConfig(id="", type="filtering")

    def test_invalid_failure_policy_raises(self):
        with pytest.raises(ValueError):
            MethodConfig(id="a", type="filtering", failure_policy="explode")

    def test_from_dict(self):
        m = MethodConfig.from_dict({"id": "a", "type": "filtering", "parameters": {"x": 1}})
        assert m.parameters == {"x": 1}


class TestConnectionConfig:
    def test_single_from_normalized_to_list(self):
        c = ConnectionConfig(from_methods="a", to_method="b")
        assert c.from_methods == ["a"]

    def test_multiple_from(self):
        c = ConnectionConfig(from_methods=["a", "b"], to_method="c", connection_type="parallel")
        assert c.from_methods == ["a", "b"]
        assert c.connection_type == "parallel"

    def test_invalid_connection_type_raises(self):
        with pytest.raises(ValueError):
            ConnectionConfig(from_methods="a", to_method="b", connection_type="weird")


class TestPoolingRuleConfig:
    def test_best_requires_metric(self):
        with pytest.raises(ValueError):
            PoolingRuleConfig(strategy="best")

    def test_best_with_metric(self):
        p = PoolingRuleConfig(strategy="best", metric="score")
        assert p.metric == "score"

    def test_invalid_strategy_raises(self):
        with pytest.raises(ValueError):
            PoolingRuleConfig(strategy="nonsense")

    def test_custom_requires_function(self):
        with pytest.raises(ValueError):
            PoolingRuleConfig(strategy="custom")


class TestConfigValidator:
    def test_valid_config_passes(self):
        config = ConfigParser().parse_string(SIMPLE_YAML)
        is_valid, errors = ConfigValidator().validate(config)
        assert is_valid, errors

    def test_connection_to_nonexistent_method(self):
        config = ConfigParser().parse_string(SIMPLE_YAML)
        config.connections.append(ConnectionConfig(from_methods="filter_step", to_method="ghost"))
        is_valid, errors = ConfigValidator().validate(config)
        assert not is_valid
        assert any("ghost" in e for e in errors)

    def test_circular_dependency_detected(self):
        config = ConfigParser().parse_string(SIMPLE_YAML)
        # add edge dock_step -> filter_step to make a cycle
        config.connections.append(ConnectionConfig(from_methods="dock_step", to_method="filter_step"))
        is_valid, errors = ConfigValidator().validate(config)
        assert not is_valid
        assert any("circular" in e.lower() for e in errors)

    def test_pooling_rule_requires_multiple_upstream(self):
        config = ConfigParser().parse_string(SIMPLE_YAML)
        config.pooling_rules["dock_step"] = PoolingRuleConfig(strategy="all")
        is_valid, errors = ConfigValidator().validate(config)
        # dock_step only has one upstream (filter_step), so pooling is invalid
        assert not is_valid
        assert any("pooling" in e.lower() for e in errors)
