"""Method port and configuration contracts."""

from __future__ import annotations

import copy
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple, Type, Union

from .model import Artifact, ArtifactStorage, data_class_for
from .registry import ArtifactRegistry, get_artifact_registry


_MISSING = object()


TypeRef = Union[str, Type[Artifact]]


def _type_name(item: TypeRef) -> str:
    if isinstance(item, str):
        return item
    if isinstance(item, type) and issubclass(item, Artifact) and item.type_name:
        return item.type_name
    raise TypeError(f"Port types must be type strings or Artifact data classes, got {item!r}")


@dataclass(frozen=True)
class PortSpec:
    """Accepted or produced data for one named method port.

    ``types`` may be data classes (``PortSpec(Molecules, "sdf")``) or their
    type strings (``PortSpec("molecule.collection", "sdf")``); they are stored
    as type strings so the spec stays serializable.
    """

    types: Tuple[str, ...]
    formats: Tuple[str, ...] = ()
    required: bool = True
    cardinality: str = "one"
    description: str = ""

    def __init__(
        self,
        types: Union[TypeRef, Iterable[TypeRef]],
        formats: Union[str, Iterable[str], None] = None,
        required: bool = True,
        cardinality: str = "one",
        description: str = "",
    ) -> None:
        if isinstance(types, (str, type)):
            types = (types,)
        normalized_types = tuple(_type_name(item) for item in types)
        if isinstance(formats, str):
            normalized_formats = (formats,)
        else:
            normalized_formats = tuple(formats or ())
        if not normalized_types:
            raise ValueError("PortSpec requires at least one artifact type")
        if cardinality not in ("one", "many"):
            raise ValueError("Port cardinality must be 'one' or 'many'")
        object.__setattr__(self, "types", normalized_types)
        object.__setattr__(self, "formats", normalized_formats)
        object.__setattr__(self, "required", required)
        object.__setattr__(self, "cardinality", cardinality)
        object.__setattr__(self, "description", description)

    def accepts(
        self,
        artifact: Artifact,
        registry: Optional[ArtifactRegistry] = None,
        check_format: bool = True,
    ) -> bool:
        registry = registry or get_artifact_registry()
        type_ok = any(registry.is_compatible(artifact.type, expected) for expected in self.types)
        if "file" in self.types and artifact.is_file:
            type_ok = True
        format_ok = not self.formats or artifact.format in self.formats
        return type_ok and (format_ok or not check_format)

    @property
    def data_class(self) -> Type[Artifact]:
        """Data class used to wrap raw values bound to this port (first declared type)."""
        return data_class_for(self.types[0])

    def coerce(self, value: Any, directory: Optional[Path] = None, stem: str = "data") -> Artifact:
        """Wrap a raw value (path, payload, rows) as this port's data type."""
        return self.data_class.coerce(
            value, type_name=self.types[0], formats=self.formats, directory=directory, stem=stem
        )

    def placeholder(self) -> Artifact:
        """Representative non-materialized artifact for static compatibility checks."""
        return Artifact(self.types[0], self.formats[0] if self.formats else "unknown",
                        "contract-placeholder", storage=ArtifactStorage.INLINE)


class _PortMap:
    """Named ports on one side (inputs or outputs) of a method contract."""

    side = "input"

    def __init__(self, ports: Optional[Dict[str, PortSpec]] = None) -> None:
        if ports is not None and not isinstance(ports, dict):
            raise TypeError(f"{type(self).__name__} takes a dict of PortSpec")
        self.ports: Dict[str, PortSpec] = dict(ports or {})

    @property
    def required(self) -> List[str]:
        return [name for name, port in self.ports.items() if port.required]

    @property
    def optional(self) -> List[str]:
        return [name for name, port in self.ports.items() if not port.required]

    def validate(
        self,
        values: Any,
        registry: Optional[ArtifactRegistry] = None,
    ) -> Tuple[bool, List[str]]:
        """Check presence, cardinality, artifact validity and type/format of every port."""
        label = self.side.capitalize()
        if not isinstance(values, dict):
            return False, [f"{label}s must be a dictionary of artifacts"]
        registry = registry or get_artifact_registry()
        verb = "received" if self.side == "input" else "produced"
        errors: List[str] = []
        for name, port in self.ports.items():
            if values.get(name) is None:
                if port.required:
                    errors.append(f"Missing required {self.side}: {name}")
                continue
            items = values[name]
            if port.cardinality == "many":
                if not isinstance(items, (list, tuple)):
                    errors.append(f"{label} '{name}' must contain multiple artifacts")
                    continue
            else:
                items = [items]
            for item in items:
                if not isinstance(item, Artifact):
                    errors.append(f"{label} '{name}' must be a normalized Artifact")
                    continue
                valid, item_errors = registry.validate(item)
                errors.extend(f"{label} '{name}': {error}" for error in item_errors)
                if valid and not port.accepts(item, registry):
                    errors.append(
                        f"{label} '{name}' {verb} {item.type}/{item.format}; expected types "
                        f"{list(port.types)} and formats {list(port.formats) or 'any'}"
                    )
        unknown = sorted(set(values) - set(self.ports))
        if unknown:
            errors.append(f"Unknown {self.side} port(s): {', '.join(unknown)}")
        return not errors, errors


class InputSpec(_PortMap):
    """Named input ports of a method."""

    side = "input"


class OutputSpec(_PortMap):
    """Named output ports of a method."""

    side = "output"


@dataclass(frozen=True)
class ConfigField:
    """Validation rules for one method configuration value."""

    types: Tuple[Type[Any], ...]
    required: bool = False
    default: Any = _MISSING
    choices: Optional[Tuple[Any, ...]] = None
    minimum: Optional[float] = None
    maximum: Optional[float] = None
    validator: Optional[Callable[[Any], Optional[str]]] = None
    description: str = ""

    def __init__(
        self,
        types: Union[Type[Any], Iterable[Type[Any]]],
        required: bool = False,
        default: Any = _MISSING,
        choices: Optional[Iterable[Any]] = None,
        minimum: Optional[float] = None,
        maximum: Optional[float] = None,
        validator: Optional[Callable[[Any], Optional[str]]] = None,
        description: str = "",
    ) -> None:
        normalized_types = (types,) if isinstance(types, type) else tuple(types)
        object.__setattr__(self, "types", normalized_types)
        object.__setattr__(self, "required", required)
        object.__setattr__(self, "default", default)
        object.__setattr__(self, "choices", tuple(choices) if choices is not None else None)
        object.__setattr__(self, "minimum", minimum)
        object.__setattr__(self, "maximum", maximum)
        object.__setattr__(self, "validator", validator)
        object.__setattr__(self, "description", description)


class ConfigSpec:
    """Strict schema for settings that are separate from method inputs."""

    def __init__(
        self,
        fields: Optional[Dict[str, ConfigField]] = None,
        strict: bool = True,
        validator: Optional[Callable[[Dict[str, Any]], List[str]]] = None,
    ) -> None:
        self.fields = fields or {}
        self.strict = strict
        self.validator = validator

    def normalize(self, config: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        config = dict(config or {})
        errors: List[str] = []
        if self.strict:
            unknown = sorted(set(config) - set(self.fields))
            if unknown:
                errors.append(f"Unknown configuration field(s): {', '.join(unknown)}")

        normalized: Dict[str, Any] = {}
        for name, field_spec in self.fields.items():
            if name not in config:
                if field_spec.required and field_spec.default is _MISSING:
                    errors.append(f"Missing required configuration field: {name}")
                elif field_spec.default is not _MISSING:
                    normalized[name] = copy.deepcopy(field_spec.default)
                continue
            value = config[name]
            if not isinstance(value, field_spec.types) or (
                isinstance(value, bool) and bool not in field_spec.types
            ):
                expected = ", ".join(item.__name__ for item in field_spec.types)
                errors.append(f"Configuration '{name}' must be of type {expected}")
                continue
            if field_spec.choices is not None and value not in field_spec.choices:
                errors.append(f"Configuration '{name}' must be one of {list(field_spec.choices)}")
            if field_spec.minimum is not None and value < field_spec.minimum:
                errors.append(f"Configuration '{name}' must be >= {field_spec.minimum}")
            if field_spec.maximum is not None and value > field_spec.maximum:
                errors.append(f"Configuration '{name}' must be <= {field_spec.maximum}")
            if field_spec.validator:
                error = field_spec.validator(value)
                if error:
                    errors.append(f"Configuration '{name}': {error}")
            normalized[name] = value

        if not self.strict:
            normalized.update({key: value for key, value in config.items() if key not in normalized})
        # Cross-field checks only see complete, per-field-valid configs.
        if self.validator and not errors:
            errors.extend(self.validator(normalized))
        if errors:
            raise ValueError("; ".join(errors))
        return normalized

    def validate(self, config: Optional[Dict[str, Any]]) -> Tuple[bool, List[str]]:
        try:
            self.normalize(config)
            return True, []
        except ValueError as exc:
            return False, [part.strip() for part in str(exc).split(";")]
