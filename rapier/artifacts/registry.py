"""Artifact semantic type registry and built-in validators."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Set, Tuple

from .model import Artifact, ArtifactStorage


ArtifactValidator = Callable[[Artifact], List[str]]


@dataclass(frozen=True)
class ArtifactType:
    """Definition of a semantic artifact type."""

    name: str
    formats: Tuple[str, ...]
    parents: Tuple[str, ...] = ()
    validator: Optional[ArtifactValidator] = None
    description: str = ""


class ArtifactRegistry:
    """Extensible catalog shared by all normalized methods."""

    def __init__(self) -> None:
        self._types: Dict[str, ArtifactType] = {}

    def register(self, artifact_type: ArtifactType, replace: bool = False) -> None:
        if artifact_type.name in self._types and not replace:
            raise ValueError(f"Artifact type '{artifact_type.name}' is already registered")
        self._types[artifact_type.name] = artifact_type

    def get(self, name: str) -> ArtifactType:
        if name not in self._types:
            raise KeyError(f"Unknown artifact type '{name}'")
        return self._types[name]

    def has(self, name: str) -> bool:
        return name in self._types

    def list_types(self) -> List[str]:
        return sorted(self._types)

    def is_compatible(self, actual: str, expected: str) -> bool:
        if actual == expected:
            return True
        if actual not in self._types or expected not in self._types:
            return False
        visited: Set[str] = set()
        pending = list(self._types[actual].parents)
        while pending:
            parent = pending.pop()
            if parent == expected:
                return True
            if parent in visited or parent not in self._types:
                continue
            visited.add(parent)
            pending.extend(self._types[parent].parents)
        return False

    def validate(self, artifact: Artifact, check_file: bool = True) -> Tuple[bool, List[str]]:
        errors: List[str] = []
        if not self.has(artifact.type):
            return False, [f"Unknown artifact type '{artifact.type}'"]

        type_spec = self.get(artifact.type)
        if "*" not in type_spec.formats and artifact.format not in type_spec.formats:
            errors.append(
                f"Format '{artifact.format}' is not valid for artifact type "
                f"'{artifact.type}'; expected one of {list(type_spec.formats)}"
            )
        if artifact.storage == ArtifactStorage.FILE and check_file:
            path = artifact.path
            if not path.exists():
                errors.append(f"Artifact file not found: {path}")
            elif not path.is_file():
                errors.append(f"Artifact path is not a file: {path}")
        if type_spec.validator:
            errors.extend(type_spec.validator(artifact))
        return not errors, errors


def _sequence_validator(alphabet: str) -> ArtifactValidator:
    allowed = set(alphabet.upper())

    def validate(artifact: Artifact) -> List[str]:
        if artifact.storage != ArtifactStorage.INLINE:
            return []
        sequence = re.sub(r"\s+", "", str(artifact.value)).upper()
        invalid = sorted(set(sequence) - allowed)
        if not sequence:
            return ["Inline sequence cannot be empty"]
        if invalid:
            return [f"Sequence contains invalid symbols: {', '.join(invalid)}"]
        return []

    return validate


def _binding_site_validator(artifact: Artifact) -> List[str]:
    if artifact.storage != ArtifactStorage.INLINE:
        return []
    value = artifact.value
    if artifact.format == "box":
        if not isinstance(value, dict):
            return ["Binding-site box must be an object with center and size"]
        center = value.get("center")
        size = value.get("size")
        if not isinstance(center, (list, tuple)) or len(center) != 3:
            return ["Binding-site box center must contain three coordinates"]
        if not isinstance(size, (list, tuple)) or len(size) != 3:
            return ["Binding-site box size must contain three dimensions"]
        if not all(isinstance(item, (int, float)) for item in list(center) + list(size)):
            return ["Binding-site box center and size must be numeric"]
        if any(item <= 0 for item in size):
            return ["Binding-site box dimensions must be positive"]
    elif artifact.format == "residues":
        if not isinstance(value, list) or not value:
            return ["Residue binding site must be a non-empty list"]
    return []


def create_default_registry() -> ArtifactRegistry:
    registry = ArtifactRegistry()
    definitions: Iterable[ArtifactType] = (
        ArtifactType("sequence", ("inline", "fasta"), description="Biological sequence"),
        ArtifactType(
            "sequence.protein",
            ("inline", "fasta"),
            ("sequence",),
            _sequence_validator("ABCDEFGHIKLMNPQRSTVWXYZ*UOJ"),
        ),
        ArtifactType(
            "sequence.dna",
            ("inline", "fasta"),
            ("sequence",),
            _sequence_validator("ACGTNRYKMSWBDHV-"),
        ),
        ArtifactType(
            "sequence.rna",
            ("inline", "fasta"),
            ("sequence",),
            _sequence_validator("ACGUNRYKMSWBDHV-"),
        ),
        ArtifactType("molecule", ("smiles", "sdf", "mol2", "pdbqt", "ccd")),
        ArtifactType("molecule.collection", ("smiles", "sdf", "mol2", "pdbqt", "ccd")),
        ArtifactType("structure", ("pdb", "cif", "pdbqt")),
        ArtifactType("structure.protein", ("pdb", "cif", "pdbqt"), ("structure",)),
        ArtifactType("structure.nucleic_acid", ("pdb", "cif"), ("structure",)),
        ArtifactType("structure.complex", ("pdb", "cif"), ("structure",)),
        ArtifactType(
            "binding_site",
            ("box", "residues", "reference", "json"),
            validator=_binding_site_validator,
        ),
        ArtifactType("table", ("csv", "tsv", "json")),
        ArtifactType("report", ("json", "text", "html", "csv")),
        ArtifactType("file", ("*",)),
    )
    for definition in definitions:
        registry.register(definition)
    return registry


_default_registry = create_default_registry()


def get_artifact_registry() -> ArtifactRegistry:
    return _default_registry
