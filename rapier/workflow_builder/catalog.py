"""Serialize method, artifact and converter registries for the builder UI.

Method cards come straight from each :class:`~rapier.core.method.Method`
subclass's declared ports, config fields, resources and ``ui_hints``; data
types (and their descriptions) from the :mod:`rapier.data` classes. Socket
compatibility is precomputed here with the same rules the pipeline validator
uses (``PortSpec.accepts`` then ``ConverterRegistry.find_path``) so the
browser never re-implements the type system.
"""

import json
from typing import Any, Dict, Iterable, List, Optional, Tuple

from ..artifacts import Artifact, ArtifactStorage, get_artifact_registry, get_converter_registry
from ..artifacts.contracts import _MISSING, ConfigField, PortSpec
from ..methods.registry import MethodRegistry, get_registry


def port_signature(port: PortSpec) -> str:
    """Stable key identifying what an input port accepts."""
    return f"{','.join(port.types)}|{','.join(port.formats)}"


def source_key(artifact_type: str, artifact_format: str) -> str:
    return f"{artifact_type}/{artifact_format}"


def output_representation(port: PortSpec) -> Tuple[str, str]:
    """Representative (type, format) of an output port, as the validator sees it."""
    return port.types[0], port.formats[0] if port.formats else "unknown"


def describe_port(name: str, port: PortSpec) -> Dict[str, Any]:
    return {
        "name": name,
        "types": list(port.types),
        "formats": list(port.formats),
        "required": port.required,
        "cardinality": port.cardinality,
        "description": port.description,
        "sig": port_signature(port),
    }


def _json_safe(value: Any) -> Any:
    try:
        json.dumps(value)
        return value
    except (TypeError, ValueError):
        return repr(value)


def describe_field(name: str, field_spec: ConfigField) -> Dict[str, Any]:
    type_names = [item.__name__ for item in field_spec.types]
    if field_spec.choices is not None:
        widget = "select"
    elif type_names == ["bool"]:
        widget = "checkbox"
    elif set(type_names) <= {"int", "float"}:
        widget = "number"
    elif set(type_names) & {"list", "dict", "tuple"}:
        widget = "json"
    else:
        widget = "text"
    result = {
        "name": name,
        "types": type_names,
        "widget": widget,
        "integer": type_names == ["int"],
        "required": field_spec.required,
        "choices": [_json_safe(item) for item in field_spec.choices] if field_spec.choices else None,
        "minimum": field_spec.minimum,
        "maximum": field_spec.maximum,
        "description": field_spec.description,
    }
    if field_spec.default is not _MISSING:
        result["default"] = _json_safe(field_spec.default)
    return result


def _category(method_class: type) -> str:
    explicit = getattr(method_class, "category", None)
    if explicit:
        return explicit
    parts = method_class.__module__.split(".")
    if len(parts) >= 3 and parts[1] == "methods":
        return parts[2]
    return "other"


# Class attributes that only rename or describe a registration, not change behaviour.
_IDENTITY_ATTRS = {
    "__module__", "__qualname__", "__doc__", "__firstlineno__", "__static_attributes__",
    "register_name", "display_name",
}


def _alias_of(name: str, method_class: type, registry: MethodRegistry) -> Optional[str]:
    """Name of a registered parent that this class merely re-registers."""
    if set(method_class.__dict__) - _IDENTITY_ATTRS:
        return None
    for other in registry.list_available():
        other_class = registry.get(other)
        if other != name and other_class is not method_class and issubclass(method_class, other_class):
            return other
    return None


def describe_method(name: str, registry: MethodRegistry) -> Dict[str, Any]:
    method_class = registry.get(name)
    info = registry.get_info(name)
    doc = (method_class.__doc__ or "").strip()
    result: Dict[str, Any] = {
        "name": name,
        "label": getattr(method_class, "display_name", None) or name.replace("_", " ").title(),
        "category": _category(method_class),
        "summary": info.get("description", ""),
        "doc": doc,
        "demo": bool(info.get("demo")),
        "alias_of": _alias_of(name, method_class, registry),
        "inputs": [],
        "outputs": [],
        "config": [],
        "resources": {},
        "error": None,
    }
    try:
        method = method_class(name=name)
        result["inputs"] = [describe_port(k, v) for k, v in method.input_spec.ports.items()]
        result["outputs"] = [describe_port(k, v) for k, v in method.output_spec.ports.items()]
        # Methods may declare per-field widget hints (e.g. a rule editor) via ``ui_hints``.
        hints = method.ui_hints or {}
        result["config"] = [
            {**describe_field(k, v), **_json_safe(hints.get(k, {}))}
            for k, v in method.config_spec.fields.items()
        ]
        spec = method.resource_spec
        result["resources"] = {
            "cpus": spec.cpus, "memory": spec.memory, "gpus": spec.gpus, "time": spec.time,
        }
    except Exception as exc:  # A broken method should not break the whole palette.
        result["error"] = f"{type(exc).__name__}: {exc}"
    return result


def _compatibility(sources: Iterable[Tuple[str, str]], ports: Dict[str, PortSpec]) -> Dict[str, Dict[str, Any]]:
    artifact_registry = get_artifact_registry()
    converter_registry = get_converter_registry()
    table: Dict[str, Dict[str, Any]] = {}
    for artifact_type, artifact_format in sources:
        # Generic files only exist on disk; everything else mirrors the validator's inline placeholder.
        storage = ArtifactStorage.FILE if artifact_type == "file" else ArtifactStorage.INLINE
        artifact = Artifact(artifact_type, artifact_format, "placeholder", storage=storage)
        row: Dict[str, Any] = {}
        for sig, port in ports.items():
            if port.accepts(artifact, artifact_registry):
                row[sig] = {"status": "direct"}
                continue
            path = converter_registry.find_path(artifact_type, artifact_format, port.types, port.formats)
            if path is not None:
                row[sig] = {"status": "convert", "path": [item.name for item in path]}
        table[source_key(artifact_type, artifact_format)] = row
    return table


def build_catalog(registry: Optional[MethodRegistry] = None) -> Dict[str, Any]:
    """Full JSON-serializable description of everything the builder can place."""
    registry = registry or get_registry()
    artifact_registry = get_artifact_registry()

    methods = {name: describe_method(name, registry) for name in registry.list_available()}

    types = {}
    sources = set()
    for type_name in artifact_registry.list_types():
        spec = artifact_registry.get(type_name)
        formats = [item for item in spec.formats if item != "*"]
        types[type_name] = {"formats": formats, "parents": list(spec.parents), "description": spec.description}
        sources.update((type_name, item) for item in formats)

    input_ports: Dict[str, PortSpec] = {}
    for name in methods:
        try:
            method = registry.get(name)(name=name)
        except Exception:
            continue
        for port in method.input_spec.ports.values():
            input_ports.setdefault(port_signature(port), port)
        sources.update(output_representation(port) for port in method.output_spec.ports.values())

    # Offer concrete formats for the wildcard "file" type based on what ports ask for.
    file_formats = sorted({
        fmt for port in input_ports.values() if "file" in port.types for fmt in port.formats
    })
    if "file" in types:
        types["file"]["formats"] = file_formats + ["other"]
        sources.update(("file", fmt) for fmt in types["file"]["formats"])

    return {
        "methods": methods,
        "types": types,
        "converters": get_converter_registry().list_converters(available_only=True),
        "compat": _compatibility(sorted(sources), input_ports),
    }
