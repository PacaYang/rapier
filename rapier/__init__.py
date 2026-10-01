"""
Rapier: Modular Virtual Screening Platform for Molecule Discovery

A SLURM-based platform for customizable virtual screening pipelines with robust
dependency management, comprehensive monitoring, and smart file management.
"""

__version__ = "0.1.0"
__author__ = "Rapier Development Team"

from rapier.core import (
    BaseMethod,
    Task,
    Pipeline,
    ExecutionGraph,
    ConfigField,
    ConfigSpec,
    InputSpec,
    OutputSpec,
    PortSpec,
)
from rapier.artifacts import (
    Artifact,
    ArtifactConverter,
    ArtifactProvenance,
    ArtifactRegistry,
    ArtifactStorage,
    ArtifactType,
    ConverterRegistry,
)

__all__ = [
    "BaseMethod",
    "Task",
    "Pipeline",
    "ExecutionGraph",
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
]
