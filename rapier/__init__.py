"""
Rapier: Modular Virtual Screening Platform for Molecule Discovery

A SLURM-based platform for customizable virtual screening pipelines with robust
dependency management, comprehensive monitoring, and smart file management.
"""

__version__ = "0.1.0"
__author__ = "Rapier Development Team"

from rapier.core import (
    Method,
    MethodError,
    RunContext,
    Task,
    Pipeline,
    ExecutionGraph,
    ConfigField,
    ConfigSpec,
    EnvironmentSpec,
    InputSpec,
    OutputSpec,
    PortSpec,
    ResourceSpec,
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
from rapier.data import (
    BindingSite,
    ComplexStructure,
    DnaSequence,
    File,
    Molecule,
    Molecules,
    NucleicAcidStructure,
    Poses,
    ProteinSequence,
    ProteinStructure,
    Report,
    RnaSequence,
    Scores,
    Sequence,
    Structure,
    Table,
)

__all__ = [
    "Method",
    "MethodError",
    "RunContext",
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
    "EnvironmentSpec",
    "ResourceSpec",
    # Data types
    "Sequence",
    "ProteinSequence",
    "DnaSequence",
    "RnaSequence",
    "Molecule",
    "Molecules",
    "Poses",
    "Structure",
    "ProteinStructure",
    "NucleicAcidStructure",
    "ComplexStructure",
    "BindingSite",
    "Table",
    "Scores",
    "Report",
    "File",
]
