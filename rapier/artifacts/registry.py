"""Artifact semantic type registry and built-in validators."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Set, Tuple

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


def _parent_type(cls: type) -> Tuple[str, ...]:
    """Registry parent of a data class: the nearest base that declares a type."""
    for base in cls.__mro__[1:]:
        name = base.__dict__.get("type_name")
        if name:
            return (name,)
    return ()


def _artifact_type(name: str, cls: type) -> ArtifactType:
    description = (cls.__doc__ or "").strip().splitlines()[0] if cls.__doc__ else ""
    return ArtifactType(name, tuple(cls.formats), _parent_type(cls), cls.check_value, description)


def create_default_registry() -> ArtifactRegistry:
    """Registry of every data class in :mod:`rapier.data` (and any later subclasses)."""
    import rapier.data  # noqa: F401  (registers the built-in data classes)
    from .model import data_classes

    registry = ArtifactRegistry()
    for name, cls in data_classes().items():
        registry.register(_artifact_type(name, cls))
    return registry


_default_registry: Optional[ArtifactRegistry] = None


def get_artifact_registry() -> ArtifactRegistry:
    """Shared registry; data classes defined after first use are added on demand."""
    global _default_registry
    if _default_registry is None:
        _default_registry = create_default_registry()
    else:
        from .model import data_classes

        for name, cls in data_classes().items():
            if not _default_registry.has(name):
                _default_registry.register(_artifact_type(name, cls))
    return _default_registry
