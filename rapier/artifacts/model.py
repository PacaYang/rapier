"""Serializable normalized data exchanged between Rapier methods."""

from __future__ import annotations

import os
import uuid
from dataclasses import dataclass, field, replace
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional


class ArtifactStorage(str, Enum):
    """How an artifact payload is stored."""

    FILE = "file"
    INLINE = "inline"


@dataclass
class ArtifactProvenance:
    """Origin and transformation history for an artifact."""

    source_artifact_ids: List[str] = field(default_factory=list)
    producing_task: Optional[str] = None
    output_port: Optional[str] = None
    conversions: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "source_artifact_ids": list(self.source_artifact_ids),
            "producing_task": self.producing_task,
            "output_port": self.output_port,
            "conversions": list(self.conversions),
        }

    @classmethod
    def from_dict(cls, data: Optional[Dict[str, Any]]) -> "ArtifactProvenance":
        data = data or {}
        return cls(
            source_artifact_ids=list(data.get("source_artifact_ids", [])),
            producing_task=data.get("producing_task"),
            output_port=data.get("output_port"),
            conversions=list(data.get("conversions", [])),
        )


@dataclass
class Artifact:
    """A typed, represented, and serializable method input or output."""

    type: str
    format: str
    value: Any
    storage: ArtifactStorage = ArtifactStorage.FILE
    artifact_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    metadata: Dict[str, Any] = field(default_factory=dict)
    provenance: ArtifactProvenance = field(default_factory=ArtifactProvenance)

    def __post_init__(self) -> None:
        if isinstance(self.storage, str):
            self.storage = ArtifactStorage(self.storage)
        if not self.type or not isinstance(self.type, str):
            raise ValueError("Artifact type must be a non-empty string")
        if not self.format or not isinstance(self.format, str):
            raise ValueError("Artifact format must be a non-empty string")
        if self.storage == ArtifactStorage.FILE:
            if not isinstance(self.value, (str, os.PathLike)):
                raise TypeError("File-backed artifact value must be a path")
            self.value = str(self.value)
        if isinstance(self.provenance, dict):
            self.provenance = ArtifactProvenance.from_dict(self.provenance)

    @property
    def is_file(self) -> bool:
        return self.storage == ArtifactStorage.FILE

    @property
    def path(self) -> Path:
        if not self.is_file:
            raise TypeError(f"Artifact {self.artifact_id} is not file-backed")
        return Path(self.value)

    def with_value(
        self,
        value: Any,
        storage: Optional[ArtifactStorage] = None,
        format: Optional[str] = None,
    ) -> "Artifact":
        """Return a copy with a new payload while retaining its identity metadata."""
        return replace(
            self,
            value=value,
            storage=storage or self.storage,
            format=format or self.format,
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "artifact_id": self.artifact_id,
            "type": self.type,
            "format": self.format,
            "storage": self.storage.value,
            "value": str(self.value) if self.is_file else self.value,
            "metadata": dict(self.metadata),
            "provenance": self.provenance.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Artifact":
        return cls(
            artifact_id=data.get("artifact_id", str(uuid.uuid4())),
            type=data["type"],
            format=data["format"],
            storage=ArtifactStorage(data.get("storage", "file")),
            value=data["value"],
            metadata=dict(data.get("metadata", {})),
            provenance=ArtifactProvenance.from_dict(data.get("provenance")),
        )

    def __fspath__(self) -> str:
        """Allow v1 code using ``Path(task.inputs[name])`` to keep working."""
        return str(self.path)

    def __str__(self) -> str:
        return str(self.value)


def artifact_to_data(value: Any) -> Any:
    """Recursively serialize artifacts embedded in task/result dictionaries."""
    if isinstance(value, Artifact):
        return {"__rapier_artifact__": True, **value.to_dict()}
    if isinstance(value, dict):
        return {key: artifact_to_data(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [artifact_to_data(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    return value


def artifact_from_data(value: Any) -> Any:
    """Recursively restore artifacts from serialized task/result data."""
    if isinstance(value, dict) and value.get("__rapier_artifact__") is True:
        data = dict(value)
        data.pop("__rapier_artifact__", None)
        return Artifact.from_dict(data)
    if isinstance(value, dict):
        return {key: artifact_from_data(item) for key, item in value.items()}
    if isinstance(value, list):
        return [artifact_from_data(item) for item in value]
    return value
