"""Artifact envelope, type registry, port contracts and conversion services.

The concrete data types (molecules, scores, structures, ...) live in
:mod:`rapier.data` and are registered when this package is imported.
"""

from .model import (
    Artifact,
    ArtifactProvenance,
    ArtifactStorage,
    artifact_from_data,
    artifact_to_data,
    data_class_for,
    format_from_suffix,
)
from .registry import ArtifactRegistry, ArtifactType, get_artifact_registry
from .contracts import ConfigField, ConfigSpec, InputSpec, OutputSpec, PortSpec
from .converters import (
    ArtifactConverter,
    ConverterRegistry,
    get_converter_registry,
    materialize_inline,
)
from .resolver import InputResolver, prepare_input

# Register the built-in data classes so Artifact(...) dispatches to them.
import rapier.data  # noqa: E402,F401

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
    "data_class_for",
    "format_from_suffix",
    "InputResolver",
    "prepare_input",
]
