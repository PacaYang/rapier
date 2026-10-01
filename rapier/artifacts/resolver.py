"""Resolve pipeline artifact references at task execution boundaries."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from .contracts import PortSpec
from .converters import ConverterRegistry, get_converter_registry, materialize_inline
from .model import Artifact, ArtifactProvenance, ArtifactStorage
from .registry import ArtifactRegistry, get_artifact_registry


class InputResolver:
    """Resolve, convert, and stage the artifacts bound to a task."""

    def __init__(
        self,
        artifact_registry: Optional[ArtifactRegistry] = None,
        converter_registry: Optional[ConverterRegistry] = None,
    ) -> None:
        self.artifact_registry = artifact_registry or get_artifact_registry()
        self.converter_registry = converter_registry or get_converter_registry()

    def resolve_task(self, task: "Task") -> Dict[str, Any]:
        bindings = task.metadata.get("input_bindings", {})
        if not bindings:
            if (
                task.method is not None
                and getattr(task.method, "contract_version", 1) >= 2
                and task.inputs
            ):
                task.inputs = self._normalize_direct_inputs(task)
            return task.inputs
        if task.method is None:
            raise ValueError(f"Task '{task.task_id}' has bindings but no method instance")
        pipeline = getattr(task, "pipeline", None)
        if pipeline is None:
            raise ValueError(f"Task '{task.task_id}' is not attached to a pipeline")

        resolved: Dict[str, Any] = {}
        for port_name, reference in bindings.items():
            port = task.method.input_spec.ports.get(port_name)
            if port is None:
                raise ValueError(f"Task '{task.task_id}' has no input port '{port_name}'")
            value = self._resolve_reference(pipeline, reference)
            values = value if isinstance(value, list) else [value]
            if port.cardinality == "one" and len(values) != 1:
                raise ValueError(
                    f"Task '{task.task_id}' input '{port_name}' expects one artifact, "
                    f"received {len(values)}"
                )
            normalized = [
                self._prepare_artifact(task, port_name, port, artifact, index)
                for index, artifact in enumerate(values)
            ]
            resolved[port_name] = normalized if port.cardinality == "many" else normalized[0]

        if getattr(task.method, "contract_version", 1) < 2:
            task.inputs = {
                name: self._unwrap_legacy(value)
                for name, value in resolved.items()
            }
        else:
            task.inputs = resolved
        return task.inputs

    def _normalize_direct_inputs(self, task: "Task") -> Dict[str, Any]:
        """Coerce legacy path dictionaries supplied directly to migrated methods."""
        normalized: Dict[str, Any] = {}
        for port_name, value in task.inputs.items():
            port = task.method.input_spec.ports.get(port_name)
            if port is None:
                normalized[port_name] = value
                continue
            values = value if port.cardinality == "many" and isinstance(value, list) else [value]
            artifacts = []
            for index, item in enumerate(values):
                if not isinstance(item, Artifact):
                    item = self._coerce_direct_value(item, port)
                artifacts.append(self._prepare_artifact(task, port_name, port, item, index))
            normalized[port_name] = artifacts if port.cardinality == "many" else artifacts[0]
        return normalized

    @staticmethod
    def _coerce_direct_value(value: Any, port: PortSpec) -> Artifact:
        if isinstance(value, (str, Path)):
            path = Path(value)
            suffix = path.suffix.lower().lstrip(".")
            format_aliases = {"smi": "smiles", "smiles": "smiles", "mmcif": "cif"}
            format_name = format_aliases.get(suffix, suffix)
            if not format_name:
                format_name = port.formats[0] if port.formats else "file"
            return Artifact(port.types[0], format_name, path)
        if isinstance(value, dict) and "binding_site" in port.types:
            return Artifact(
                "binding_site", "box", value, storage=ArtifactStorage.INLINE
            )
        raise TypeError(
            f"Cannot coerce direct value of type {type(value).__name__} for port "
            f"accepting {list(port.types)}"
        )

    @staticmethod
    def _resolve_reference(pipeline: "Pipeline", reference: str) -> Any:
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

    def _prepare_artifact(
        self,
        task: "Task",
        port_name: str,
        port: PortSpec,
        artifact: Any,
        index: int,
    ) -> Artifact:
        if not isinstance(artifact, Artifact):
            raise TypeError(
                f"Task '{task.task_id}' input '{port_name}' resolved to "
                f"{type(artifact).__name__}, not Artifact"
            )
        valid, errors = self.artifact_registry.validate(artifact)
        if not valid:
            raise ValueError(f"Input '{port_name}': {'; '.join(errors)}")

        input_dir = Path(task.input_dir) / "normalized"
        input_dir.mkdir(parents=True, exist_ok=True)
        prepared = materialize_inline(artifact, input_dir, f"{port_name}-{index}")
        if not port.accepts(prepared, self.artifact_registry):
            prepared = self.converter_registry.convert_for_port(prepared, port, input_dir)
        if prepared.is_file:
            prepared = self._stage_file(prepared, input_dir, port_name, index)
        if not port.accepts(prepared, self.artifact_registry):
            raise ValueError(
                f"Input '{port_name}' resolved to {prepared.type}/{prepared.format}, which "
                f"does not satisfy the port contract"
            )
        return prepared

    @staticmethod
    def _stage_file(artifact: Artifact, input_dir: Path, port_name: str, index: int) -> Artifact:
        source = artifact.path.resolve()
        suffixes = {
            "smiles": ".smi",
            "sdf": ".sdf",
            "pdb": ".pdb",
            "cif": ".cif",
            "pdbqt": ".pdbqt",
            "fasta": ".fasta",
            "csv": ".csv",
            "tsv": ".tsv",
            "json": ".json",
        }
        suffix = suffixes.get(artifact.format, source.suffix)
        target = input_dir / f"{port_name}-{index}-{source.stem}{suffix}"
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

    @classmethod
    def _unwrap_legacy(cls, value: Any) -> Any:
        if isinstance(value, Artifact):
            return str(value.path) if value.is_file else value.value
        if isinstance(value, list):
            return [cls._unwrap_legacy(item) for item in value]
        return value


class OutputNormalizer:
    """Normalize and validate method outputs immediately after execution."""

    def __init__(self, artifact_registry: Optional[ArtifactRegistry] = None) -> None:
        self.artifact_registry = artifact_registry or get_artifact_registry()

    def normalize_task(self, task: "Task") -> Dict[str, Any]:
        if task.method is None:
            return task.outputs
        spec = task.method.output_spec
        normalized: Dict[str, Any] = {}
        errors: List[str] = []

        for port_name, port in spec.ports.items():
            if port_name not in task.outputs:
                if port.required:
                    errors.append(f"Missing required output '{port_name}'")
                continue
            raw = task.outputs[port_name]
            values = raw if port.cardinality == "many" and isinstance(raw, list) else [raw]
            if port.cardinality == "many" and not isinstance(raw, list):
                errors.append(f"Output '{port_name}' must contain multiple artifacts")
                continue
            artifacts: List[Artifact] = []
            for value in values:
                try:
                    artifact = self._coerce(value, port)
                    self._attach_provenance(artifact, task.task_id, port_name)
                    valid, artifact_errors = self.artifact_registry.validate(artifact)
                    if not valid:
                        errors.extend(f"Output '{port_name}': {error}" for error in artifact_errors)
                    elif not port.accepts(artifact, self.artifact_registry):
                        errors.append(
                            f"Output '{port_name}' produced {artifact.type}/{artifact.format}; "
                            f"expected {list(port.types)}/{list(port.formats) or 'any'}"
                        )
                    artifacts.append(artifact)
                except (TypeError, ValueError) as exc:
                    errors.append(f"Output '{port_name}': {exc}")
            normalized[port_name] = artifacts if port.cardinality == "many" else artifacts[0]

        extras = set(task.outputs) - set(spec.ports)
        if extras and getattr(task.method, "contract_version", 1) >= 2:
            errors.append(f"Unknown output port(s): {', '.join(sorted(extras))}")
        elif extras:
            normalized.update({name: task.outputs[name] for name in extras})

        if errors:
            raise ValueError("; ".join(errors))
        task.outputs = normalized
        return normalized

    @staticmethod
    def _coerce(value: Any, port: PortSpec) -> Artifact:
        if isinstance(value, Artifact):
            return value
        if isinstance(value, (str, Path)):
            path = Path(value)
            format_name = port.formats[0] if port.formats else path.suffix.lstrip(".").lower() or "file"
            return Artifact(port.types[0], format_name, path)
        raise TypeError(f"cannot normalize value of type {type(value).__name__}")

    @staticmethod
    def _attach_provenance(artifact: Artifact, task_id: str, port_name: str) -> None:
        artifact.provenance = ArtifactProvenance(
            source_artifact_ids=list(artifact.provenance.source_artifact_ids),
            producing_task=task_id,
            output_port=port_name,
            conversions=list(artifact.provenance.conversions),
        )
