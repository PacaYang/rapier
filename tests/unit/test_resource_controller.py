"""Resource-controller configuration, isolation, and runtime integration."""

import json

import pytest

from rapier.config import ConfigParser, ConfigValidator, MethodConfig
from rapier.core.method import Method, RunContext
from rapier.core.pipeline import Pipeline
from rapier.core.resources import ResourceController
from rapier.core.specs import ResourceSpec
from rapier.core.task import Task


class ResourceMethod(Method):
    resources = ResourceSpec(
        cpus=4,
        memory="8GB",
        gpus=1,
        gpu_type="A100",
        time="02:00:00",
        partition="gpu",
    )

    def run(self, inputs, ctx):
        return {}


def test_controller_resolves_method_defaults_and_overrides():
    method = ResourceMethod()
    controller = ResourceController.resolve(
        method.resource_spec, {"cpus": 8, "memory": "16GB", "time_limit": "3h"}
    )

    assert controller.to_dict() == {
        "cpus": 8,
        "memory": "16GB",
        "gpus": 1,
        "gpu_type": "A100",
        "time_limit": "3h",
        "partition": "gpu",
        "nodes": 1,
    }
    assert method.resource_spec.cpus == 4
    assert method.resource_spec.time == "02:00:00"


@pytest.mark.parametrize(
    "resource_request",
    [
        [{"cpus": 1}, {"cpus": 2}],
        "cpu",
        {"cpu": {"cpus": 1}},
        {"cpus": True},
        {"cpus": 0},
        {"gpus": -1},
        {"gpus": 1.5},
        {"nodes": 0},
        {"memory": "manyGB"},
        {"memory": "0GB"},
        {"time_limit": "soon"},
        {"time_limit": "0h"},
        {"time_limit": "01:80:00"},
        {"gpu_type": ["A100", "V100"]},
        {"partition": 3},
    ],
)
def test_controller_rejects_invalid_resource_requests(resource_request):
    with pytest.raises(ValueError, match="resource controller"):
        ResourceController(resource_request)


@pytest.mark.parametrize("duration", ["30m", "02:00:00", "1-02:30:00"])
def test_controller_accepts_scheduler_duration_formats(duration):
    assert ResourceController({"time_limit": duration})["time_limit"] == duration


def test_method_config_uses_canonical_controller_and_reads_legacy_resources():
    configured = MethodConfig(
        id="step", type="example", resource_controller={"cpus": 3}
    )
    legacy = MethodConfig.from_dict(
        {"id": "step", "type": "example", "resources": {"cpus": 3}}
    )

    assert configured.resources == legacy.resources == {"cpus": 3}
    assert configured.resource_controller is configured.resources
    assert legacy.to_dict()["resource_controller"] == {"cpus": 3}
    assert "resources" not in legacy.to_dict()
    assert MethodConfig.from_dict(legacy.to_dict()).resources == {"cpus": 3}


@pytest.mark.parametrize("canonical, legacy", [({}, {}), ({"cpus": 1}, {})])
def test_method_config_rejects_ambiguous_resource_declarations(canonical, legacy):
    with pytest.raises(ValueError, match="both 'resource_controller'"):
        MethodConfig(
            id="step", type="example", resource_controller=canonical, resources=legacy
        )


def test_null_legacy_declaration_does_not_conflict_with_controller():
    config = MethodConfig.from_dict(
        {
            "id": "step",
            "type": "example",
            "resource_controller": {"cpus": 2},
            "resources": None,
        }
    )
    assert config.resources == {"cpus": 2}


def test_tasks_own_controllers_without_mutating_shared_defaults_or_metadata():
    method = ResourceMethod()
    metadata = {"resources": {"cpus": 2}}
    first = Task("first", method, {}, metadata)
    second = Task("second", method, {}, metadata)

    assert first.method is method
    assert first.method is not second.method
    assert first.resource_controller is not second.resource_controller
    assert first.method.resource_controller is first.resource_controller
    assert second.method.resource_controller is second.resource_controller
    assert first.resource_controller["cpus"] == second.resource_controller["cpus"] == 2
    assert metadata == {"resources": {"cpus": 2}}
    resources = first.resource_controller.to_dict()
    resources["cpus"] = 100
    assert first.resource_controller["cpus"] == 2
    assert second.resource_controller["cpus"] == 2
    assert method.resource_spec.cpus == 4


def test_task_uses_method_defaults_without_explicit_request():
    task = Task("step", ResourceMethod(), {})

    assert task.resource_controller["cpus"] == 4
    assert task.resource_controller["time_limit"] == "02:00:00"
    assert task.metadata["resources"] == task.resource_controller.to_dict()
    assert RunContext.for_task(task).resources == task.resource_controller.to_dict()


def test_task_serialization_preserves_controller_without_method():
    task = Task(
        "step", ResourceMethod(), {}, resource_controller={"cpus": 6, "nodes": 2}
    )
    serialized = json.loads(json.dumps(task.to_dict()))
    restored = Task.from_dict(serialized)

    assert restored.resource_controller is not task.resource_controller
    assert restored.resource_controller.to_dict() == task.resource_controller.to_dict()
    assert restored.metadata["resources"] == serialized["resource_controller"]
    assert restored.metadata["resource_controller"] == serialized["resource_controller"]


def test_legacy_serialized_task_gets_controller_from_resource_metadata():
    serialized = Task("old", ResourceMethod(), {}).to_dict()
    serialized.pop("resource_controller")
    serialized["metadata"].pop("resource_controller")
    restored = Task.from_dict(serialized)

    assert restored.resource_controller["cpus"] == 4
    assert restored.resource_controller["gpu_type"] == "A100"


def test_pipeline_build_resolves_one_controller_per_method():
    config = ConfigParser().parse_dict(
        {
            "name": "resource_test",
            "methods": [
                {
                    "id": "first",
                    "type": "ligand_filter",
                    "resource_controller": {"cpus": 5, "memory": "12GB"},
                },
                {"id": "second", "type": "ligand_filter"},
            ],
        }
    )
    pipeline = Pipeline.from_config(config)
    first, second = pipeline.tasks["first"], pipeline.tasks["second"]

    assert first.resource_controller["cpus"] == 5
    assert first.resource_controller["memory"] == "12GB"
    assert first.resource_controller["time_limit"] == first.method.resource_spec.time
    assert second.resource_controller["cpus"] == second.method.resource_spec.cpus
    assert first.resource_controller is first.method.resource_controller
    assert second.resource_controller is second.method.resource_controller
    assert first.resource_controller is not second.resource_controller
    assert first.metadata["resources"] == first.resource_controller.to_dict()


def test_validator_reports_resources_using_the_controller_contract():
    validator = ConfigValidator()

    assert validator.validate_resources({"time_limit": "02:00:00"}) == []
    assert validator.validate_resources({"nodes": 0})
    assert validator.validate_resources([{"cpus": 2}])
