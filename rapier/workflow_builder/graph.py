"""Convert between the builder's node graph and Rapier pipeline YAML.

Graph JSON (what the browser edits)::

    {
      "pipeline": {"name", "description", "version", "failure_policy", "extra": {...}},
      "nodes": [
        {"id", "kind": "input",  "name", "x", "y",
         "artifact": {"type", "format", "value" | "source", "metadata"}},
        {"id", "kind": "method", "name", "x", "y", "method_type",
         "config": {}, "resources": {}, "description", "failure_policy", "retry_count"},
        {"id", "kind": "output", "name", "x", "y"}
      ],
      "edges": [{"id", "from": {"node", "port"}, "to": {"node", "port"}}]
    }

Input nodes expose one output socket named ``artifact`` and output nodes one
input socket named ``artifact``. Node ``name`` becomes the YAML key/method id.
Canvas positions are stored under a top-level ``ui.layout`` key, which the
pipeline parser ignores.
"""

import re
from pathlib import PurePath
from typing import Any, Dict, List, Tuple

import yaml

from ..config.pipeline_config import PipelineConfig
from ..config.validator import ConfigValidator
from ..config.workflow_metadata import WorkflowMetadata
from ..core.resources import ResourceController

ARTIFACT_PORT = "artifact"
_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_\-]*$")
# Top-level pipeline keys the builder has no dedicated UI for but must round-trip.
_PASSTHROUGH_KEYS = ("connections", "pooling_rules", "environment")


class GraphError(ValueError):
    """Structural problem that prevents building a pipeline dict."""

    def __init__(self, errors: List[Dict[str, Any]]):
        super().__init__("; ".join(item["message"] for item in errors))
        self.errors = errors


def _issue(message: str, node: str = None, port: str = None) -> Dict[str, Any]:
    return {"message": message, "node": node, "port": port}


def graph_to_pipeline_dict(graph: Dict[str, Any]) -> Dict[str, Any]:
    """Build a pipeline dict from graph JSON; raises GraphError on structural problems."""
    nodes = {node["id"]: node for node in graph.get("nodes", [])}
    meta = graph.get("pipeline", {})
    issues: List[Dict[str, Any]] = []

    seen: Dict[Tuple[str, str], str] = {}
    for node in nodes.values():
        name = (node.get("name") or "").strip()
        kind = node.get("kind")
        if not _NAME_RE.match(name):
            issues.append(
                _issue(
                    f"Invalid name '{name}': use letters, digits, '_' or '-'",
                    node["id"],
                )
            )
            continue
        # Inputs and outputs have their own namespaces; methods share one.
        namespace = kind if kind in ("input", "output") else "method"
        if (namespace, name) in seen:
            issues.append(_issue(f"Duplicate {namespace} name '{name}'", node["id"]))
        seen[(namespace, name)] = node["id"]

    method_inputs: Dict[str, Dict[str, str]] = {
        nid: {} for nid, n in nodes.items() if n.get("kind") == "method"
    }
    outputs: Dict[str, str] = {}
    for edge in graph.get("edges", []):
        src, dst = nodes.get(edge["from"]["node"]), nodes.get(edge["to"]["node"])
        if src is None or dst is None:
            issues.append(_issue(f"Edge '{edge.get('id')}' references a missing node"))
            continue
        if src.get("kind") == "input":
            reference = f"pipeline.{src['name']}"
        elif src.get("kind") == "method":
            reference = f"{src['name']}.{edge['from']['port']}"
        else:
            issues.append(_issue("Pipeline outputs cannot feed other nodes", src["id"]))
            continue
        port = edge["to"]["port"]
        if dst.get("kind") == "method":
            if port in method_inputs[dst["id"]]:
                issues.append(
                    _issue(
                        f"Input '{port}' has more than one connection", dst["id"], port
                    )
                )
            method_inputs[dst["id"]][port] = reference
        elif dst.get("kind") == "output":
            if src.get("kind") != "method":
                issues.append(
                    _issue("Pipeline outputs must come from a method", dst["id"])
                )
                continue
            if dst["name"] in outputs:
                issues.append(_issue("Output has more than one connection", dst["id"]))
            outputs[dst["name"]] = reference
        else:
            issues.append(
                _issue("Pipeline inputs cannot receive connections", dst["id"])
            )

    if issues:
        raise GraphError(issues)

    data: Dict[str, Any] = {
        "name": meta.get("name") or "untitled_pipeline",
        "description": meta.get("description") or None,
        "version": meta.get("version") or "1.0.0",
        "failure_policy": meta.get("failure_policy") or "stop",
        "inputs": {},
        "methods": [],
        "metadata": meta.get("metadata", WorkflowMetadata().to_dict()),
    }
    if meta.get("metadata_file"):
        data["metadata_file"] = meta["metadata_file"]
    layout: Dict[str, Dict[str, float]] = {}
    for node in nodes.values():
        kind = node.get("kind")
        layout_key = f"{kind}:{node['name']}"
        layout[layout_key] = {
            "x": round(node.get("x") or 0),
            "y": round(node.get("y") or 0),
        }
        if kind == "input":
            artifact = dict(node.get("artifact") or {})
            entry = {"type": artifact.get("type"), "format": artifact.get("format")}
            if artifact.get("source"):
                entry["source"] = artifact["source"]
            else:
                entry["value"] = artifact.get("value", "")
            if artifact.get("metadata"):
                entry["metadata"] = artifact["metadata"]
            data["inputs"][node["name"]] = entry
        elif kind == "method":
            method: Dict[str, Any] = {
                "id": node["name"],
                "type": node.get("method_type"),
            }
            if node.get("description"):
                method["description"] = node["description"]
            method["inputs"] = method_inputs[node["id"]]
            if node.get("config"):
                method["config"] = node["config"]
            if (
                node.get("resource_controller") is not None
                and node.get("resources") is not None
            ):
                raise GraphError(
                    [
                        _issue(
                            "Define only one resource_controller per method", node["id"]
                        )
                    ]
                )
            controller = node.get("resource_controller", node.get("resources", {}))
            method["resource_controller"] = controller
            if node.get("failure_policy") and node["failure_policy"] != "stop":
                method["failure_policy"] = node["failure_policy"]
                method["retry_count"] = int(node.get("retry_count") or 1)
            data["methods"].append(method)
    if outputs:
        data["outputs"] = outputs
    for key in _PASSTHROUGH_KEYS:
        if meta.get("extra", {}).get(key):
            data[key] = meta["extra"][key]
    if data["description"] is None:
        del data["description"]
    data["ui"] = {"layout": layout}
    return data


def pipeline_dict_to_graph(data: Dict[str, Any]) -> Dict[str, Any]:
    """Build graph JSON from a pipeline dict. Nodes without saved layout get x/y = None."""
    layout = (data.get("ui") or {}).get("layout", {})
    metadata = WorkflowMetadata.from_dict(
        data.get("metadata", WorkflowMetadata().to_dict())
    )
    nodes: List[Dict[str, Any]] = []
    edges: List[Dict[str, Any]] = []

    def position(kind: str, name: str) -> Dict[str, Any]:
        saved = layout.get(f"{kind}:{name}") or {}
        return {"x": saved.get("x"), "y": saved.get("y")}

    for name, artifact in (data.get("inputs") or {}).items():
        nodes.append(
            {
                "id": f"in_{name}",
                "kind": "input",
                "name": name,
                "artifact": dict(artifact),
                **position("input", name),
            }
        )
    for method in data.get("methods") or []:
        config = method.get("config") or method.get("parameters") or {}
        if (
            method.get("resource_controller") is not None
            and method.get("resources") is not None
        ):
            raise ValueError("Define only one of resource_controller or resources")
        controller = ResourceController(
            method.get("resource_controller", method.get("resources"))
        ).to_dict()
        nodes.append(
            {
                "id": f"m_{method['id']}",
                "kind": "method",
                "name": method["id"],
                "method_type": method.get("type"),
                "config": dict(config),
                "resource_controller": controller,
                "description": method.get("description") or "",
                "failure_policy": method.get("failure_policy", "stop"),
                "retry_count": method.get("retry_count", 1),
                **position("method", method["id"]),
            }
        )

    def source(reference: str) -> Dict[str, str]:
        head, _, tail = reference.partition(".")
        if head == "pipeline":
            return {"node": f"in_{tail}", "port": ARTIFACT_PORT}
        return {"node": f"m_{head}", "port": tail}

    for method in data.get("methods") or []:
        for port, reference in (method.get("inputs") or {}).items():
            edges.append(
                {
                    "id": f"e_{method['id']}_{port}",
                    "from": source(reference),
                    "to": {"node": f"m_{method['id']}", "port": port},
                }
            )
    for name, reference in (data.get("outputs") or {}).items():
        nodes.append(
            {
                "id": f"out_{name}",
                "kind": "output",
                "name": name,
                **position("output", name),
            }
        )
        edges.append(
            {
                "id": f"e_out_{name}",
                "from": source(reference),
                "to": {"node": f"out_{name}", "port": ARTIFACT_PORT},
            }
        )

    meta = {
        key: data.get(key)
        for key in ("name", "description", "version", "failure_policy")
    }
    meta["metadata"] = metadata.to_dict()
    meta["metadata_file"] = (
        data.get("metadata_file") or f"{data.get('name') or 'workflow'}.meta.yaml"
    )
    meta["extra"] = {key: data[key] for key in _PASSTHROUGH_KEYS if data.get(key)}
    return {"pipeline": meta, "nodes": nodes, "edges": edges}


def graph_to_yaml(graph: Dict[str, Any]) -> str:
    """Export a self-contained YAML representation, including inline metadata."""
    return yaml.safe_dump(
        graph_to_pipeline_dict(graph), sort_keys=False, allow_unicode=True, width=100
    )


def graph_to_workflow_files(graph: Dict[str, Any]) -> Dict[str, str]:
    """Export workflow YAML with a separate, authoritative metadata file."""
    data = graph_to_pipeline_dict(graph)
    metadata = WorkflowMetadata.from_dict(data.pop("metadata"))
    # Browser downloads are basenames; keep the reference beside the workflow.
    filename = re.sub(r"[^A-Za-z0-9_.-]", "_", data["name"]).strip(".") or "workflow"
    reference = data.get("metadata_file") or f"{filename}.meta.yaml"
    metadata_filename = PurePath(reference).name
    if metadata_filename in ("", ".", ".."):
        raise ValueError("metadata_file must name a YAML file")
    data["metadata_file"] = metadata_filename
    return {
        "yaml": yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=100),
        "metadata_yaml": yaml.safe_dump(metadata.to_dict(), sort_keys=False),
        "filename": f"{filename}.yaml",
        "metadata_filename": metadata_filename,
    }


def yaml_to_graph(text: str, metadata_yaml: str = None) -> Dict[str, Any]:
    data = yaml.safe_load(text)
    if not isinstance(data, dict):
        raise ValueError("Pipeline YAML must be a mapping")
    if metadata_yaml is not None:
        data["metadata"] = WorkflowMetadata.from_dict(
            yaml.safe_load(metadata_yaml)
        ).to_dict()
    elif data.get("metadata_file") and "metadata" not in data:
        raise ValueError(
            "Select the companion metadata file together with the workflow YAML"
        )
    return pipeline_dict_to_graph(data)


_ERROR_PATTERNS = (
    (re.compile(r"^Method '([^']+)'"), "method"),
    (re.compile(r"^Pipeline input '([^']+)'"), "input"),
    (re.compile(r"^Pipeline output '([^']+)'"), "output"),
)
_PORT_PATTERNS = (
    re.compile(r"^Method '[^']+' input '([^']+)'"),
    re.compile(r"input port '([^']+)'"),
)


def _locate(message: str, name_to_node: Dict[Tuple[str, str], str]) -> Dict[str, Any]:
    for pattern, kind in _ERROR_PATTERNS:
        match = pattern.match(message)
        if match:
            port = None
            for port_pattern in _PORT_PATTERNS:
                port_match = port_pattern.search(message)
                if port_match:
                    port = port_match.group(1)
                    break
            return _issue(message, name_to_node.get((kind, match.group(1))), port)
    return _issue(message)


def validate_graph(graph: Dict[str, Any]) -> Dict[str, Any]:
    """Run the full pipeline validator and attribute each error to a node where possible."""
    try:
        data = graph_to_pipeline_dict(graph)
    except GraphError as exc:
        return {"valid": False, "errors": exc.errors}

    name_to_node = {}
    for node in graph.get("nodes", []):
        kind = node.get("kind")
        name_to_node[
            (kind if kind in ("input", "output") else "method", node.get("name"))
        ] = node["id"]

    # Resolve relative input sources like ConfigParser does, without exporting the path.
    base_dir = graph.get("pipeline", {}).get("base_dir")
    if base_dir and not data.get("environment", {}).get("project_dir"):
        data["environment"] = {**data.get("environment", {}), "project_dir": base_dir}

    try:
        config = PipelineConfig.from_dict(data)
    except (KeyError, TypeError, ValueError) as exc:
        return {"valid": False, "errors": [_locate(str(exc), name_to_node)]}

    valid, errors = ConfigValidator().validate(config)
    return {
        "valid": valid,
        "errors": [_locate(message, name_to_node) for message in errors],
    }
