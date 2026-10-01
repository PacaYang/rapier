"""Normalized artifact contracts and conversion services."""

from .model import (
    Artifact,
    ArtifactProvenance,
    ArtifactStorage,
    artifact_from_data,
    artifact_to_data,
)
from .registry import ArtifactRegistry, ArtifactType, get_artifact_registry
from .contracts import ConfigField, ConfigSpec, InputSpec, OutputSpec, PortSpec
from .converters import (
    ArtifactConverter,
    ConverterRegistry,
    get_converter_registry,
    materialize_inline,
)
from .resolver import InputResolver, OutputNormalizer

__all__ = [
    "Artifact",
    "ArtifactProvenance",
    "ArtifactStorage",
    "ArtifactRegistry",
    "ArtifactType",
    "ArtifactConverter",
    "ConverterRegistry",
    "PortSpec",
    "InputSpec",
    "OutputSpec",
    "ConfigField",
    "ConfigSpec",
    "get_artifact_registry",
    "get_converter_registry",
    "materialize_inline",
    "artifact_to_data",
    "artifact_from_data",
    "InputResolver",
    "OutputNormalizer",
]
