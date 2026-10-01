"""Unit tests for normalized artifacts, contracts, and converters."""

from pathlib import Path

import pytest

from rapier.artifacts import (
    Artifact,
    ArtifactConverter,
    ArtifactProvenance,
    ArtifactRegistry,
    ArtifactStorage,
    ArtifactType,
    ConverterRegistry,
    get_artifact_registry,
)
from rapier.core.specs import ConfigField, ConfigSpec, PortSpec
from rapier.core.task import Task


def test_file_artifact_round_trip(tmp_path):
    path = tmp_path / "molecules.sdf"
    path.write_text("test")
    artifact = Artifact(
        "molecule.collection",
        "sdf",
        path,
        metadata={"count": 1},
        provenance=ArtifactProvenance(producing_task="prepare", output_port="molecules"),
    )

    restored = Artifact.from_dict(artifact.to_dict())

    assert restored.artifact_id == artifact.artifact_id
    assert restored.path == path
    assert restored.metadata == {"count": 1}
    assert restored.provenance.producing_task == "prepare"
    assert Path(restored) == path


def test_task_serialization_preserves_artifacts(tmp_path):
    path = tmp_path / "scores.csv"
    path.write_text("score\n1\n")
    task = Task("score", method=None, inputs={
        "scores": Artifact("table", "csv", path),
    })
    task.outputs = {"report": Artifact("report", "json", tmp_path / "report.json")}

    restored = Task.from_dict(task.to_dict())

    assert isinstance(restored.inputs["scores"], Artifact)
    assert restored.inputs["scores"].type == "table"
    assert isinstance(restored.outputs["report"], Artifact)


def test_inline_sequence_and_binding_site_validation():
    registry = get_artifact_registry()
    valid_sequence = Artifact(
        "sequence.dna", "inline", "ACGTN", storage=ArtifactStorage.INLINE
    )
    invalid_sequence = Artifact(
        "sequence.dna", "inline", "ACGU", storage=ArtifactStorage.INLINE
    )
    valid_site = Artifact(
        "binding_site",
        "box",
        {"center": [1, 2, 3], "size": [20, 20, 20]},
        storage=ArtifactStorage.INLINE,
    )
    invalid_site = Artifact(
        "binding_site",
        "box",
        {"center": [1, 2], "size": [20, -1, 20]},
        storage=ArtifactStorage.INLINE,
    )

    assert registry.validate(valid_sequence)[0]
    assert not registry.validate(invalid_sequence)[0]
    assert registry.validate(valid_site)[0]
    assert not registry.validate(invalid_site)[0]


def test_port_type_format_and_generic_file_compatibility(tmp_path):
    path = tmp_path / "ligands.sdf"
    path.write_text("data")
    artifact = Artifact("molecule.collection", "sdf", path)

    assert PortSpec("molecule.collection", "sdf").accepts(artifact)
    assert not PortSpec("molecule.collection", "smiles").accepts(artifact)
    assert PortSpec("file", "sdf").accepts(artifact)


def test_config_spec_defaults_and_strict_validation_are_isolated():
    spec = ConfigSpec(fields={
        "mode": ConfigField(str, default="fast", choices=("fast", "accurate")),
        "values": ConfigField(list, default=[]),
        "limit": ConfigField(int, required=True, minimum=1),
    })

    first = spec.normalize({"limit": 2})
    first["values"].append(1)
    second = spec.normalize({"limit": 3})
    assert second["values"] == []

    with pytest.raises(ValueError, match="Unknown configuration"):
        spec.normalize({"limit": 1, "extra": True})
    with pytest.raises(ValueError, match="must be >="):
        spec.normalize({"limit": 0})


def test_converter_selection_and_provenance(tmp_path):
    artifact_registry = ArtifactRegistry()
    artifact_registry.register(ArtifactType("example", ("a", "b", "c")))
    converters = ConverterRegistry(artifact_registry)

    def make_converter(target_format, marker):
        def convert(artifact, work_dir):
            path = work_dir / f"converted.{target_format}"
            path.write_text(marker)
            return Artifact("example", target_format, path)
        return convert

    converters.register(ArtifactConverter(
        "low-priority", "example", "a", "example", "c",
        make_converter("c", "low"), priority=10,
    ))
    converters.register(ArtifactConverter(
        "high-priority", "example", "a", "example", "c",
        make_converter("c", "high"), priority=20,
    ))
    source_path = tmp_path / "source.a"
    source_path.write_text("source")
    source = Artifact("example", "a", source_path)

    result = converters.convert_for_port(source, PortSpec("example", "c"), tmp_path)

    assert result.path.read_text() == "high"
    assert result.provenance.source_artifact_ids == [source.artifact_id]
    assert result.provenance.conversions[0]["converter"] == "high-priority"


def test_converter_reports_unavailable_destination(tmp_path):
    registry = ArtifactRegistry()
    registry.register(ArtifactType("example", ("a", "b")))
    converters = ConverterRegistry(registry)
    path = tmp_path / "source.a"
    path.write_text("source")

    with pytest.raises(ValueError, match="No available conversion"):
        converters.convert_for_port(
            Artifact("example", "a", path), PortSpec("example", "b"), tmp_path
        )
