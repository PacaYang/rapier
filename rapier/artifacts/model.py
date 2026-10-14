"""Serializable typed data exchanged between Rapier methods.

:class:`Artifact` is the envelope every method input and output travels in.
Each semantic type (molecules, scores, structures, ...) is a subclass declared
in :mod:`rapier.data`; constructing or deserializing an ``Artifact`` with a
registered type string returns an instance of that subclass.
"""

from __future__ import annotations

import os
import uuid
from dataclasses import dataclass, field, replace
from enum import Enum
from pathlib import Path
from typing import Any, ClassVar, Dict, List, Optional, Sequence, Tuple


# Data classes by semantic type name, filled in by ``Artifact.__init_subclass__``.
_DATA_CLASSES: Dict[str, type] = {}

_SUFFIX_FORMATS = {
    "smi": "smiles", "smiles": "smiles", "mmcif": "cif", "fa": "fasta", "faa": "fasta",
    "txt": "text", "htm": "html", "yml": "yaml",
}


def format_from_suffix(path: Any) -> str:
    """Representation implied by a file name (``ligands.smi`` -> ``smiles``)."""
    suffix = Path(path).suffix.lower().lstrip(".")
    return _SUFFIX_FORMATS.get(suffix, suffix)


def data_class_for(type_name: Optional[str]) -> type:
    """Most specific data class for a type string; unknown dotted names use their prefix."""
    name = type_name or ""
    while name:
        if name in _DATA_CLASSES:
            return _DATA_CLASSES[name]
        name = name.rpartition(".")[0]
    return Artifact


def data_classes() -> Dict[str, type]:
    """Registered data classes in definition order (parents before children)."""
    return dict(_DATA_CLASSES)


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
    """A typed, represented, and serializable method input or output.

    Subclasses describe one semantic type through class attributes:
    ``type_name`` (registry key, dotted for subtypes), ``formats`` (accepted
    representations, first is the default) and optionally ``check_value``
    (inline-payload validation) and ``coerce``/``from_value`` (how raw Python
    values such as paths, dicts or row lists become this type).
    """

    type: str
    format: str
    value: Any
    storage: ArtifactStorage = ArtifactStorage.FILE
    artifact_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    metadata: Dict[str, Any] = field(default_factory=dict)
    provenance: ArtifactProvenance = field(default_factory=ArtifactProvenance)

    type_name: ClassVar[Optional[str]] = None
    formats: ClassVar[Tuple[str, ...]] = ()

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        if cls.__dict__.get("type_name"):
            _DATA_CLASSES[cls.type_name] = cls

    def __new__(cls, *args: Any, **kwargs: Any) -> "Artifact":
        # Dispatch Artifact("table.scores", ...) to the registered Scores class.
        type_name = kwargs.get("type", args[0] if args else None)
        target = data_class_for(type_name) if isinstance(type_name, str) else cls
        if target is not cls and issubclass(target, cls):
            cls = target
        return object.__new__(cls)

    def __post_init__(self) -> None:
        if isinstance(self.storage, str):
            self.storage = ArtifactStorage(self.storage)
        if not self.type or not isinstance(self.type, str):
            raise ValueError("Artifact type must be a non-empty string")
        if not self.format or not isinstance(self.format, str):
            raise ValueError("Artifact format must be a non-empty string")
        if type(self) is not Artifact and not issubclass(data_class_for(self.type), type(self)):
            raise ValueError(f"{type(self).__name__} cannot hold artifact type '{self.type}'")
        if self.storage == ArtifactStorage.FILE:
            if not isinstance(self.value, (str, os.PathLike)):
                raise TypeError("File-backed artifact value must be a path")
            self.value = str(self.value)
        if isinstance(self.provenance, dict):
            self.provenance = ArtifactProvenance.from_dict(self.provenance)

    # ---- construction from raw values ------------------------------------

    @classmethod
    def check_value(cls, artifact: "Artifact") -> List[str]:
        """Errors in an inline payload; registered as the type's validator."""
        return []

    @classmethod
    def from_file(
        cls,
        path: Any,
        format: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        type_name: Optional[str] = None,
    ) -> "Artifact":
        """File-backed artifact; the format defaults to the file suffix."""
        type_name = type_name or cls.type_name
        if not type_name:
            raise TypeError("Artifact.from_file needs a type_name for the generic base class")
        format = format or format_from_suffix(path) or (cls.formats[0] if cls.formats else "file")
        return cls(type_name, format, Path(path), metadata=dict(metadata or {}))

    @classmethod
    def inline(
        cls,
        value: Any,
        format: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        type_name: Optional[str] = None,
    ) -> "Artifact":
        """Artifact whose payload is held in the pipeline rather than on disk."""
        type_name = type_name or cls.type_name
        format = format or (cls.formats[0] if cls.formats else "json")
        return cls(type_name, format, value, storage=ArtifactStorage.INLINE, metadata=dict(metadata or {}))

    @classmethod
    def coerce(
        cls,
        value: Any,
        *,
        type_name: Optional[str] = None,
        formats: Sequence[str] = (),
        directory: Optional[Path] = None,
        stem: str = "data",
    ) -> "Artifact":
        """Wrap a raw value (path, payload) as this type.

        ``formats`` are the formats the destination port accepts, used when the
        value does not imply one. ``directory``/``stem`` let subclasses write
        rendered values (e.g. score rows) to disk instead of keeping them inline.
        """
        if isinstance(value, Artifact):
            return value
        if isinstance(value, (str, os.PathLike)):
            # Trust the suffix whenever the type can represent it (a port needing
            # another format gets it by conversion); otherwise assume the port's format.
            fmt = format_from_suffix(value)
            known = tuple(cls.formats) + tuple(formats)
            if not fmt or (fmt not in known and "*" not in known):
                fmt = (tuple(formats) or tuple(cls.formats) or ("file",))[0]
            return cls.from_file(value, fmt, type_name=type_name)
        return cls.from_value(value, type_name=type_name, formats=formats, directory=directory, stem=stem)

    @classmethod
    def from_value(
        cls,
        value: Any,
        *,
        type_name: Optional[str] = None,
        formats: Sequence[str] = (),
        directory: Optional[Path] = None,
        stem: str = "data",
    ) -> "Artifact":
        """Hook for non-path raw values; JSON-like payloads become inline JSON."""
        if isinstance(value, (dict, list)) and ("json" in (tuple(formats) or cls.formats) or not cls.formats):
            return cls.inline(value, "json", type_name=type_name)
        raise TypeError(
            f"Cannot use a {type(value).__name__} value as {type_name or cls.type_name}"
        )

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
