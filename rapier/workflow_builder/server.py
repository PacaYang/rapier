"""Flask app serving the node-based workflow builder.

Run with ``python -m rapier.workflow_builder`` (requires ``pip install -e ".[ui]"``).
The server has no authentication and binds to localhost by default; do not
expose it on a shared network.
"""

from pathlib import Path
from typing import Optional

from ..methods.registry import MethodRegistry, get_registry
from .catalog import build_catalog
from .demo_methods import demo_pipeline, register_demo_methods
from .graph import (
    GraphError,
    graph_to_yaml,
    pipeline_dict_to_graph,
    validate_graph,
    yaml_to_graph,
)
from .graph import graph_to_workflow_files

STATIC_DIR = Path(__file__).parent / "static"
EXAMPLES_DIR = Path(__file__).resolve().parents[2] / "examples" / "pipelines"
MAX_YAML_BYTES = 1_000_000


def create_app(registry: Optional[MethodRegistry] = None, demo: bool = True):
    from flask import Flask, jsonify, request, send_from_directory

    registry = registry or get_registry()
    registry.auto_discover()
    if demo:
        register_demo_methods(registry)

    app = Flask(__name__, static_folder=None)
    app.config["MAX_CONTENT_LENGTH"] = MAX_YAML_BYTES

    def examples():
        return (
            sorted(
                path.stem
                for path in EXAMPLES_DIR.glob("*.yaml")
                if not path.name.endswith(".meta.yaml")
            )
            if EXAMPLES_DIR.is_dir()
            else []
        )

    @app.get("/")
    def index():
        return send_from_directory(STATIC_DIR, "index.html")

    @app.get("/static/<path:filename>")
    def static_files(filename):
        return send_from_directory(STATIC_DIR, filename)

    @app.get("/api/catalog")
    def catalog():
        return jsonify(build_catalog(registry))

    @app.post("/api/validate")
    def validate():
        return jsonify(validate_graph(request.get_json(force=True)))

    @app.post("/api/export")
    def export():
        try:
            graph = request.get_json(force=True)
            bundle = graph_to_workflow_files(graph)
            # Retain the self-contained string for existing API clients.
            bundle["workflow_yaml"] = bundle["yaml"]
            bundle["yaml"] = graph_to_yaml(graph)
            return jsonify(bundle)
        except GraphError as exc:
            return jsonify({"errors": exc.errors}), 400
        except (TypeError, ValueError) as exc:
            return jsonify({"error": str(exc)}), 400

    @app.post("/api/import")
    def import_yaml():
        try:
            if request.is_json:
                payload = request.get_json()
                return jsonify(
                    yaml_to_graph(payload["yaml"], payload.get("metadata_yaml"))
                )
            return jsonify(yaml_to_graph(request.get_data(as_text=True)))
        except Exception as exc:  # Malformed user YAML of any kind.
            return jsonify({"error": f"Could not parse pipeline: {exc}"}), 400

    @app.get("/api/examples")
    def list_examples():
        return jsonify(["demo"] + examples())

    @app.get("/api/examples/<name>")
    def load_example(name):
        if name == "demo":
            return jsonify(pipeline_dict_to_graph(demo_pipeline()))
        # Only serve names from the directory listing, never arbitrary paths.
        if name not in examples():
            return jsonify({"error": f"Unknown example '{name}'"}), 404
        import yaml
        from ..config import ConfigParser

        path = EXAMPLES_DIR / f"{name}.yaml"
        data = yaml.safe_load(path.read_text())
        config = ConfigParser().parse_file(str(path))
        data["metadata"] = config.metadata.to_dict()
        graph = pipeline_dict_to_graph(data)
        graph["pipeline"]["base_dir"] = str(EXAMPLES_DIR)
        return jsonify(graph)

    return app
