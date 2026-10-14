"""Resolve pipeline references and stage input data at task boundaries.

:class:`InputResolver` turns a task's ``input_bindings`` (``pipeline.<name>``
or ``<task>.<port>``) into the raw upstream values. Each method then formats
those values for its own ports with :meth:`rapier.core.method.Method.format_inputs`,
which uses :func:`prepare_input` to validate, convert and stage every artifact.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any, Dict, Optional

from .contracts import PortSpec
from .converters import ConverterRegistry, get_converter_registry, materialize_inline
from .model import Artifact, ArtifactStorage
from .registry import ArtifactRegistry, get_artifact_registry

_STAGED_SUFFIXES = {
    "smiles": ".smi", "sdf": ".sdf", "pdb": ".pdb", "cif": ".cif", "pdbqt": ".pdbqt",
    "fasta": ".fasta", "csv": ".csv", "tsv": ".tsv", "json": ".json",
}


class InputResolver:
    """Look up the raw values bound to a task's input ports."""

    def resolve_task(self, task: "Task") -> Dict[str, Any]:
        bindings = task.metadata.get("input_bindings", {})
        if not bindings:
            return dict(task.inputs)
        pipeline = getattr(task, "pipeline", None)
        if pipeline is None:
            raise ValueError(f"Task '{task.task_id}' is not attached to a pipeline")
        resolved = dict(task.inputs)
        for port_name, reference in bindings.items():
            resolved[port_name] = self.resolve_reference(pipeline, reference)
        return resolved

    @staticmethod
    def resolve_reference(pipeline: "Pipeline", reference: str) -> Any:
        if reference.startswith("pipeline."):
            name = reference[len("pipeline."):]
            if name not in pipeline.artifacts:
                raise ValueError(f"Unknown pipeline artifact '{name}'")
            return pipeline.artifacts[name]
        source_task_id, separator, source_port = reference.partition(".")
        if not separator or source_task_id not in pipeline.tasks:
            raise ValueError(f"Invalid artifact reference '{reference}'")
        source_task = pipeline.tasks[source_task_id]
        if source_port not in source_task.outputs:
            raise ValueError(
                f"Upstream output '{reference}' is not available; task status is "
                f"{source_task.status.name}"
            )
        return source_task.outputs[source_port]


def prepare_input(
    artifact: Artifact,
    port: PortSpec,
    input_dir: Optional[Path],
    name: str,
    artifact_registry: Optional[ArtifactRegistry] = None,
    converter_registry: Optional[ConverterRegistry] = None,
) -> Artifact:
    """Validate an artifact, convert it to a format the port accepts and stage it.

    Without ``input_dir`` nothing is written: the artifact is only validated
    and must already satisfy the port.
    """
    artifact_registry = artifact_registry or get_artifact_registry()
    converter_registry = converter_registry or get_converter_registry()
    valid, errors = artifact_registry.validate(artifact)
    if not valid:
        raise ValueError("; ".join(errors))
    prepared = artifact
    if input_dir is not None:
        input_dir = Path(input_dir)
        input_dir.mkdir(parents=True, exist_ok=True)
        prepared = materialize_inline(prepared, input_dir, name)
        if not port.accepts(prepared, artifact_registry):
            prepared = converter_registry.convert_for_port(prepared, port, input_dir)
        if prepared.is_file:
            prepared = _stage_file(prepared, input_dir, name)
    if not port.accepts(prepared, artifact_registry):
        raise ValueError(
            f"{prepared.type}/{prepared.format} does not satisfy types {list(port.types)} "
            f"and formats {list(port.formats) or 'any'}"
        )
    return prepared


def _stage_file(artifact: Artifact, input_dir: Path, name: str) -> Artifact:
    source = artifact.path.resolve()
    target = input_dir / f"{name}-{source.stem}{_STAGED_SUFFIXES.get(artifact.format, source.suffix)}"
    if source == target.resolve():
        return artifact
    if target.exists() or target.is_symlink():
        target.unlink()
    try:
        target.symlink_to(source)
    except OSError:
        shutil.copy2(source, target)
    staged = artifact.with_value(target, storage=ArtifactStorage.FILE)
    staged.metadata = {**artifact.metadata, "staged_from": str(source)}
    return staged
