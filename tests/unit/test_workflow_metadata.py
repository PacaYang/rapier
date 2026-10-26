"""Workflow metadata loading, resource graph migration, and output placeholders."""

from copy import deepcopy

import pytest
import yaml

from rapier.config import ConfigParser, PipelineConfig, WorkflowMetadata
from rapier.config.workflow_metadata import OUTPUT_DIR_TEMPLATE
from rapier.workflow_builder import (
    demo_pipeline,
    graph_to_pipeline_dict,
    graph_to_workflow_files,
    pipeline_dict_to_graph,
    register_demo_methods,
    validate_graph,
    yaml_to_graph,
)
from rapier.workflow_builder.server import create_app


def workflow():
    return {"name": "example", "methods": [{"id": "step", "type": "unknown"}]}


def write_workflow(tmp_path, metadata):
    directory = tmp_path / "settings"
    directory.mkdir()
    (directory / "example.meta.yaml").write_text(yaml.safe_dump(metadata))
    data = {**workflow(), "metadata_file": "settings/example.meta.yaml"}
    path = tmp_path / "example.yaml"
    path.write_text(yaml.safe_dump(data))
    return path


def builder_client():
    pytest.importorskip("flask")
    return create_app().test_client()


def test_companion_metadata_resolves_relative_to_its_own_directory(tmp_path):
    path = write_workflow(tmp_path, {"working_dir": "../runs", "region": "cluster_a"})
    config = ConfigParser().parse_file(str(path))
    assert config.metadata.working_dir == "../runs"
    assert config.metadata.resolved_working_dir == str(tmp_path / "runs")
    assert config.metadata.region == "cluster_a"
    assert not (tmp_path / "runs").exists()
    roundtrip = PipelineConfig.from_dict(config.to_dict())
    assert roundtrip.metadata == config.metadata
    assert (
        roundtrip.metadata.resolved_working_dir == config.metadata.resolved_working_dir
    )


def test_companion_is_authoritative_and_substitutes_variables(tmp_path):
    path = write_workflow(tmp_path, {"working_dir": "${RUNS}", "region": "cluster_b"})
    data = yaml.safe_load(path.read_text())
    data["metadata"] = {"working_dir": "old", "region": "old"}
    path.write_text(yaml.safe_dump(data))
    parser = ConfigParser()
    parser.set_variable("RUNS", "computed")
    config = parser.parse_file(str(path))
    assert config.metadata.working_dir == "computed"
    assert config.metadata.region == "cluster_b"


def test_missing_companion_is_reported(tmp_path):
    with pytest.raises(FileNotFoundError, match="Workflow metadata file not found"):
        ConfigParser(str(tmp_path)).parse_dict(
            {**workflow(), "metadata_file": "absent.yaml"}
        )


@pytest.mark.parametrize(
    "metadata",
    [
        None,
        [],
        {},
        {"working_dir": "runs"},
        {"working_dir": "", "region": "x"},
        {"working_dir": "runs", "region": 123},
        {"working_dir": 123, "region": "x"},
    ],
)
def test_invalid_metadata_is_rejected(tmp_path, metadata):
    path = write_workflow(tmp_path, metadata)
    with pytest.raises(ValueError, match="metadata"):
        ConfigParser().parse_file(str(path))


def test_legacy_defaults_and_inline_metadata_do_not_mutate_source(tmp_path):
    data = workflow()
    original = deepcopy(data)
    config = ConfigParser(str(tmp_path)).parse_dict(data)
    assert data == original
    assert config.metadata.to_dict() == {"working_dir": ".", "region": "default"}
    assert config.metadata.resolved_working_dir == str(tmp_path)
    data["metadata"] = {"working_dir": "runs", "region": "west"}
    assert ConfigParser(str(tmp_path)).parse_dict(data).metadata.region == "west"


def test_parser_can_load_workflows_from_separate_directories(tmp_path):
    parser = ConfigParser()
    for name in ("one", "two"):
        directory = tmp_path / name
        directory.mkdir()
        path = write_workflow(directory, {"working_dir": "runs", "region": name})
        config = parser.parse_file(str(path))
        assert config.metadata.resolved_working_dir == str(directory / "settings/runs")


def test_built_tasks_receive_placeholder_without_execution_paths(tmp_path):
    register_demo_methods()
    config = ConfigParser(str(tmp_path)).parse_dict(
        {**demo_pipeline(), "metadata": {"working_dir": "runs", "region": "west"}}
    )
    pipeline = config.to_pipeline()
    assert pipeline.workflow_metadata is config.metadata
    assert pipeline.metadata["workflow"]["region"] == "west"
    for task in pipeline.tasks.values():
        assert task.metadata["output_directory"] == {
            "template": OUTPUT_DIR_TEMPLATE,
            "working_dir": str(tmp_path / "runs"),
            "region": "west",
            "task_id": task.task_id,
            "status": "placeholder",
        }
        assert task.work_dir is None
        assert task.output_dir is None
        assert task.resource_controller is task.method.resource_controller
    assert not (tmp_path / "runs").exists()


def test_graph_exports_companion_and_migrates_resources(tmp_path):
    data = {**demo_pipeline(), "metadata": {"working_dir": "runs", "region": "west"}}
    data["methods"][0]["resources"] = {"cpus": 3, "memory": "8GB"}
    graph = pipeline_dict_to_graph(data)
    method = next(node for node in graph["nodes"] if node["kind"] == "method")
    assert method["resource_controller"] == {"cpus": 3, "memory": "8GB"}
    assert "resources" not in method
    bundle = graph_to_workflow_files(graph)
    pipeline_data = yaml.safe_load(bundle["yaml"])
    assert pipeline_data["metadata_file"] == bundle["metadata_filename"]
    assert "metadata" not in pipeline_data
    (tmp_path / bundle["filename"]).write_text(bundle["yaml"])
    (tmp_path / bundle["metadata_filename"]).write_text(bundle["metadata_yaml"])
    config = ConfigParser().parse_file(str(tmp_path / bundle["filename"]))
    assert config.metadata.region == "west"
    imported = yaml_to_graph(bundle["yaml"], bundle["metadata_yaml"])
    assert imported["pipeline"]["metadata"] == data["metadata"]
    assert all(
        "resource_controller" in m for m in graph_to_pipeline_dict(imported)["methods"]
    )


def test_companion_import_requires_metadata_payload():
    bundle = graph_to_workflow_files(pipeline_dict_to_graph(demo_pipeline()))
    with pytest.raises(ValueError, match="companion metadata file"):
        yaml_to_graph(bundle["yaml"])


def test_invalid_graph_metadata_is_reported():
    graph = pipeline_dict_to_graph(demo_pipeline())
    graph["pipeline"]["metadata"]["region"] = ""
    assert not validate_graph(graph)["valid"]


def test_metadata_absolute_working_directory(tmp_path):
    metadata = WorkflowMetadata(str(tmp_path), "cluster_a", base_dir="/elsewhere")
    assert metadata.resolved_working_dir == str(tmp_path)


def test_builder_export_and_two_file_import_contract():
    client = builder_client()
    graph = pipeline_dict_to_graph(
        {**demo_pipeline(), "metadata": {"working_dir": "runs", "region": "west"}}
    )

    response = client.post("/api/export", json=graph)
    assert response.status_code == 200
    bundle = response.get_json()
    assert "metadata:" in bundle["yaml"]
    assert "metadata:" not in bundle["workflow_yaml"]
    assert "working_dir: runs" in bundle["metadata_yaml"]
    assert bundle["metadata_filename"].endswith(".meta.yaml")

    imported = client.post(
        "/api/import",
        json={
            "yaml": bundle["workflow_yaml"],
            "metadata_yaml": bundle["metadata_yaml"],
        },
    )
    assert imported.status_code == 200
    imported_graph = imported.get_json()
    assert imported_graph["pipeline"]["metadata"] == {
        "working_dir": "runs",
        "region": "west",
    }
    method = next(node for node in imported_graph["nodes"] if node["kind"] == "method")
    assert "resource_controller" in method


def test_builder_rejects_workflow_without_companion_payload():
    client = builder_client()
    bundle = graph_to_workflow_files(pipeline_dict_to_graph(demo_pipeline()))

    response = client.post("/api/import", data=bundle["yaml"])

    assert response.status_code == 400
    assert "companion metadata file" in response.get_json()["error"]


def test_builder_example_listing_handles_companion_metadata():
    client = builder_client()

    names = client.get("/api/examples").get_json()
    assert "13_workflow_metadata" in names
    assert "13_workflow_metadata.meta" not in names

    response = client.get("/api/examples/13_workflow_metadata")
    assert response.status_code == 200
    assert response.get_json()["pipeline"]["metadata"] == {
        "working_dir": "../../runs/workflow_metadata",
        "region": "local",
    }
