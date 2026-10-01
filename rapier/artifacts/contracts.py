"""Method port and configuration contracts."""

from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple, Type, Union

from .model import Artifact
from .registry import ArtifactRegistry, get_artifact_registry


_MISSING = object()


@dataclass(frozen=True)
class PortSpec:
    """Accepted or produced artifacts for one named method port."""

    types: Tuple[str, ...]
    formats: Tuple[str, ...] = ()
    required: bool = True
    cardinality: str = "one"
    description: str = ""

    def __init__(
        self,
        types: Union[str, Iterable[str]],
        formats: Union[str, Iterable[str], None] = None,
        required: bool = True,
        cardinality: str = "one",
        description: str = "",
    ) -> None:
        normalized_types = (types,) if isinstance(types, str) else tuple(types)
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


class InputSpec:
    """Named normalized input ports with a v1 file-spec compatibility view."""

    def __init__(
        self,
        ports: Optional[Dict[str, PortSpec]] = None,
        required_files: Optional[Union[List[str], Dict[str, Any]]] = None,
        optional_files: Optional[Union[List[str], Dict[str, Any]]] = None,
        parameters: Optional[Dict[str, Any]] = None,
    ) -> None:
        if ports is not None and not isinstance(ports, dict):
            if required_files is not None:
                raise TypeError("InputSpec received both positional and named legacy files")
            required_files = ports
            ports = None
        self.required_files = required_files or []
        self.optional_files = optional_files or []
        self.parameters = parameters or {}
        self.legacy = ports is None and bool(required_files or optional_files or parameters)
        self.ports: Dict[str, PortSpec] = dict(ports or {})
        self._add_legacy_ports(self.required_files, required=True)
        self._add_legacy_ports(self.optional_files, required=False)
        if ports is not None:
            self.required_files = [name for name, port in self.ports.items() if port.required]
            self.optional_files = [name for name, port in self.ports.items() if not port.required]

    def _add_legacy_ports(self, declarations: Any, required: bool) -> None:
        if not declarations:
            return
        items = declarations.items() if isinstance(declarations, dict) else ((name, {}) for name in declarations)
        for name, declaration in items:
            if name in self.ports:
                continue
            declaration = declaration if isinstance(declaration, dict) else {}
            formats = declaration.get("format", ())
            self.ports[name] = PortSpec(
                "file",
                formats=formats,
                required=required,
                description=declaration.get("description", ""),
            )

    def validate(
        self,
        inputs: Dict[str, Any],
        registry: Optional[ArtifactRegistry] = None,
    ) -> Tuple[bool, List[str]]:
        from pathlib import Path

        errors: List[str] = []
        registry = registry or get_artifact_registry()
        for name, port in self.ports.items():
            if name not in inputs:
                if port.required:
                    errors.append(f"Missing required input: {name}")
                continue
            values = inputs[name] if port.cardinality == "many" else [inputs[name]]
            if port.cardinality == "many" and not isinstance(values, (list, tuple)):
                errors.append(f"Input '{name}' must contain multiple artifacts")
                continue
            for value in values:
                if isinstance(value, Artifact):
                    valid, artifact_errors = registry.validate(value)
                    errors.extend(f"Input '{name}': {error}" for error in artifact_errors)
                    if valid and not port.accepts(value, registry):
                        errors.append(
                            f"Input '{name}' received {value.type}/{value.format}; expected "
                            f"types {list(port.types)} and formats {list(port.formats) or 'any'}"
                        )
                elif self.legacy:
                    if value and not Path(value).exists():
                        errors.append(f"Input file not found: {value}")
                else:
                    errors.append(f"Input '{name}' must be a normalized Artifact")

        for param_name, param_spec in self.parameters.items():
            if param_spec.get("required", False) and param_name not in inputs:
                errors.append(f"Missing required parameter: {param_name}")
        return not errors, errors


class OutputSpec:
    """Named normalized output ports with legacy expected-file support."""

    def __init__(
        self,
        ports: Optional[Dict[str, PortSpec]] = None,
        files: Optional[Union[List[str], Dict[str, Any]]] = None,
        formats: Optional[Dict[str, str]] = None,
        optional_files: Optional[Union[List[str], Dict[str, Any]]] = None,
    ) -> None:
        if ports is not None and not isinstance(ports, dict):
            if files is not None:
                raise TypeError("OutputSpec received both positional and named legacy files")
            files = ports
            ports = None
        self.files = files or []
        self.formats = formats or {}
        self.optional_files = optional_files or []
        self.legacy = ports is None and bool(files or optional_files)
        self.ports: Dict[str, PortSpec] = dict(ports or {})
        self._add_legacy_ports(self.files, required=True)
        self._add_legacy_ports(self.optional_files, required=False)
        if ports is not None:
            self.files = [name for name, port in self.ports.items() if port.required]
            self.optional_files = [name for name, port in self.ports.items() if not port.required]

    def _add_legacy_ports(self, declarations: Any, required: bool) -> None:
        if not declarations:
            return
        items = declarations.items() if isinstance(declarations, dict) else ((name, {}) for name in declarations)
        for name, declaration in items:
            if name in self.ports:
                continue
            declaration = declaration if isinstance(declaration, dict) else {}
            formats = declaration.get("format", self.formats.get(name, ()))
            self.ports[name] = PortSpec(
                "file",
                formats=formats,
                required=required,
                description=declaration.get("description", ""),
            )

    def validate(self, output: Any) -> Tuple[bool, List[str]]:
        from pathlib import Path

        if isinstance(output, (str, Path)):
            if not self.legacy:
                return False, [
                    "Normalized OutputSpec validation requires a dictionary of artifacts"
                ]
            output_dir = Path(output)
            declarations = self.files.keys() if isinstance(self.files, dict) else self.files
            errors = [
                f"Expected output not found: {pattern}"
                for pattern in declarations
                if not (output_dir / pattern).exists()
            ]
            return not errors, errors

        if not isinstance(output, dict):
            return False, ["Outputs must be a dictionary or output directory"]
        errors: List[str] = []
        registry = get_artifact_registry()
        for name, port in self.ports.items():
            if name not in output:
                if port.required:
                    errors.append(f"Missing required output: {name}")
                continue
            values = output[name] if port.cardinality == "many" else [output[name]]
            if port.cardinality == "many" and not isinstance(values, (list, tuple)):
                errors.append(f"Output '{name}' must contain multiple artifacts")
                continue
            for value in values:
                if not isinstance(value, Artifact):
                    errors.append(f"Output '{name}' must be a normalized Artifact")
                    continue
                valid, artifact_errors = registry.validate(value)
                errors.extend(f"Output '{name}': {error}" for error in artifact_errors)
                if valid and not port.accepts(value, registry):
                    errors.append(
                        f"Output '{name}' produced {value.type}/{value.format}; expected "
                        f"types {list(port.types)} and formats {list(port.formats) or 'any'}"
                    )
        return not errors, errors


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
        if self.validator:
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
