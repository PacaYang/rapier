"""Configuration and runtime tests for normalized method dataflow."""

from pathlib import Path

import pytest

from rapier.artifacts import Artifact, ArtifactStorage, InputResolver
from rapier.config import ConfigParser, ConfigValidator, MethodConfig
from rapier.core.enums import TaskStatus
from rapier.core.method import Method, RunContext
from rapier.core.pipeline import Pipeline
from rapier.core.specs import PortSpec
from rapier.core.task import Task
from rapier.data import BindingSite, Molecules
from rapier.engine.result import ExecutionResult
from datetime import datetime


class NormalizedPassthrough(Method):
    inputs = {
        "data": PortSpec(Molecules, "sdf"),
        "site": PortSpec(BindingSite, "box", required=False),
    }
    outputs = {"data": PortSpec(Molecules, "sdf")}

    def run(self, inputs, ctx):
        return {"data": inputs["data"]}


def test_normalized_yaml_builds_data_edges(tmp_path):
    ligands = tmp_path / "ligands.sdf"
    ligands.write_text("molecules")
    content = f"""
name: normalized
inputs:
  ligands:
    type: molecule.collection
    format: sdf
    source: {ligands}
methods:
  - id: first
    type: ligand_filter
    inputs:
      ligands: pipeline.ligands
    config:
      mw_max: 500
  - id: second
    type: ligand_filter
    inputs:
      ligands: first.filtered_ligands
    config:
      mw_max: 450
outputs:
  final: second.filtered_ligands
"""
    config = ConfigParser(project_dir=str(tmp_path)).parse_string(content)
    valid, errors = ConfigValidator().validate(config)
    assert valid, errors

    pipeline = Pipeline.from_config(config)
    assert pipeline.get_downstream_tasks("first") == ["second"]
    assert isinstance(pipeline.tasks["first"].method, Method)
    assert pipeline.output_bindings == {"final": "second.filtered_ligands"}


def test_config_and_parameters_cannot_both_be_supplied():
    with pytest.raises(ValueError, match="both"):
        MethodConfig(
            id="method", type="ligand_filter",
            config={"mw_max": 500}, parameters={"mw_max": 400},
        )


def test_contract_validation_rejects_unknown_port(tmp_path):
    ligands = tmp_path / "ligands.sdf"
    ligands.write_text("molecules")
    config = ConfigParser(project_dir=str(tmp_path)).parse_string(f"""
name: invalid
inputs:
  ligands: {{type: molecule.collection, format: sdf, source: {ligands}}}
methods:
  - id: filter
    type: ligand_filter
    inputs:
      wrong_port: pipeline.ligands
""")

    valid, errors = ConfigValidator().validate(config)
    assert not valid
    assert any("unknown input port 'wrong_port'" in error for error in errors)


def test_contract_validation_rejects_unknown_builtin_config(tmp_path):
    ligands = tmp_path / "ligands.sdf"
    ligands.write_text("molecules")
    config = ConfigParser(project_dir=str(tmp_path)).parse_string(f"""
name: invalid_config
inputs:
  ligands: {{type: molecule.collection, format: sdf, source: {ligands}}}
methods:
  - id: filter
    type: ligand_filter
    inputs:
      ligands: pipeline.ligands
    config:
      not_a_filter_setting: true
""")

    valid, errors = ConfigValidator().validate(config)
    assert not valid
    assert any("Unknown configuration field" in error for error in errors)


def test_resolver_routes_upstream_artifact_and_inline_site(tmp_path):
    data_path = tmp_path / "source.sdf"
    data_path.write_text("molecules")
    pipeline = Pipeline("runtime")
    source = Task("source", method=NormalizedPassthrough("source"), inputs={})
    source.outputs = {"data": Artifact("molecule.collection", "sdf", data_path)}
    source.status = TaskStatus.COMPLETED
    destination = Task(
        "destination",
        method=NormalizedPassthrough("destination"),
        inputs={},
        metadata={
            "input_bindings": {
                "data": "source.data",
                "site": "pipeline.site",
            }
        },
    )
    pipeline.artifacts["site"] = Artifact(
        "binding_site", "box",
        {"center": [0, 0, 0], "size": [20, 20, 20]},
        storage=ArtifactStorage.INLINE,
    )
    pipeline.add_task(source)
    pipeline.add_task(destination)

    raw = InputResolver().resolve_task(destination)
    formatted = destination.method.format_inputs(raw, tmp_path / "destination" / "input" / "normalized")

    assert isinstance(formatted["data"], Molecules)
    assert formatted["data"].path.exists()
    assert formatted["data"].path.parent.name == "normalized"
    assert isinstance(formatted["site"], BindingSite)
    assert formatted["site"].storage == ArtifactStorage.INLINE


def test_unresolvable_reference_is_reported():
    pipeline = Pipeline("missing")
    task = Task("t", method=NormalizedPassthrough(), inputs={},
                metadata={"input_bindings": {"data": "pipeline.nope"}})
    pipeline.add_task(task)
    with pytest.raises(ValueError, match="Unknown pipeline artifact"):
        InputResolver().resolve_task(task)


def test_format_outputs_adds_provenance(tmp_path):
    output = tmp_path / "output.sdf"
    output.write_text("molecules")
    task = Task("producer", method=NormalizedPassthrough(), inputs={})
    task.work_dir = tmp_path

    normalized = task.method.format_outputs({"data": str(output)}, RunContext.for_task(task))

    artifact = normalized["data"]
    assert isinstance(artifact, Molecules)
    assert artifact.type == "molecule.collection"
    assert artifact.provenance.producing_task == "producer"
    assert artifact.provenance.output_port == "data"


def test_execution_result_preserves_exported_artifacts(tmp_path):
    path = tmp_path / "final.sdf"
    path.write_text("molecules")
    result = ExecutionResult(
        pipeline_name="pipeline",
        success=True,
        start_time=datetime.now(),
        pipeline_outputs={"final": Artifact("molecule.collection", "sdf", path)},
    )

    restored = ExecutionResult.from_dict(result.to_dict())

    assert isinstance(restored.get_final_outputs()["final"], Artifact)
    assert restored.get_final_outputs()["final"].path == path
