"""Tests for the workflow builder backend (catalog, graph conversion, API)."""

import pytest
import yaml

from rapier.core.task import Task
from rapier.core.enums import TaskStatus
from rapier.methods.registry import get_registry
from rapier.workflow_builder import (
    DEMO_METHODS,
    GraphError,
    build_catalog,
    demo_pipeline,
    graph_to_pipeline_dict,
    graph_to_yaml,
    pipeline_dict_to_graph,
    register_demo_methods,
    validate_graph,
    yaml_to_graph,
)
from rapier.workflow_builder.demo_methods import DemoStructurePrediction


@pytest.fixture
def registry():
    # Other tests clear the singleton registry, so rebuild it explicitly.
    reg = get_registry()
    reg.auto_discover()
    register_demo_methods(reg)
    return reg


@pytest.fixture
def catalog(registry):
    return build_catalog(registry)


def demo_graph():
    graph = pipeline_dict_to_graph(demo_pipeline())
    for i, node in enumerate(graph["nodes"]):
        node["x"], node["y"] = i * 10, i * 5
    return graph


class TestDemoMethods:
    def test_registration_is_idempotent(self, registry):
        assert register_demo_methods(registry) == []
        for method_class in DEMO_METHODS:
            assert registry.get(method_class.register_name) is method_class

    def test_not_auto_discovered(self):
        reg = get_registry()
        reg.clear()
        reg.auto_discover()
        assert not any(name.startswith("demo_") for name in reg.list_available())

    def test_structure_prediction_ports_match_contract(self):
        method = DemoStructurePrediction()
        assert list(method.input_spec.ports) == ["sequence", "msa"]
        assert method.output_spec.ports["structure"].formats == ("cif",)
        assert method.output_spec.ports["confidence"].types == ("report",)

    @pytest.mark.parametrize("method_class", DEMO_METHODS, ids=lambda c: c.register_name)
    def test_execute_writes_valid_outputs(self, method_class, tmp_path):
        method = method_class()
        task = Task("t", method, inputs={})
        task.work_dir = tmp_path
        assert method.execute(task) == TaskStatus.COMPLETED
        valid, errors = method.output_spec.validate(task.outputs)
        assert valid, errors


class TestCatalog:
    def test_describes_ports_and_config(self, catalog):
        fold = catalog["methods"]["demo_structure_prediction"]
        assert fold["category"] == "prediction"
        assert [p["name"] for p in fold["outputs"]] == ["structure", "confidence"]
        fields = {f["name"]: f for f in fold["config"]}
        assert fields["use_templates"]["widget"] == "checkbox"
        assert fields["accelerator"]["widget"] == "select"
        assert fields["num_recycles"] == {**fields["num_recycles"], "widget": "number", "integer": True, "maximum": 20}

    def test_builtin_methods_present_and_alias_flagged(self, catalog):
        assert "vina_docking" in catalog["methods"]
        assert catalog["methods"]["boltz2_prediction"]["alias_of"] == "boltz2"
        assert catalog["methods"]["boltz2"]["alias_of"] is None

    def test_compatibility_direct_hierarchy_and_none(self, catalog):
        compat = catalog["compat"]
        backbone_sig = "structure|pdb,cif"  # demo_sequence_design.backbone
        assert compat["structure.protein/cif"][backbone_sig]["status"] == "direct"
        assert backbone_sig not in compat["report/json"]

    def test_compatibility_via_converter(self, catalog):
        if not catalog["converters"]:
            pytest.skip("no converters available")
        compat = catalog["compat"]
        if "rdkit.molecule.collection.smiles_to_sdf" in catalog["converters"]:
            hit = compat["molecule.collection/smiles"]["molecule.collection|sdf"]
            assert hit == {"status": "convert", "path": ["rdkit.molecule.collection.smiles_to_sdf"]}

    def test_file_type_gets_concrete_formats(self, catalog):
        assert "a3m" in catalog["types"]["file"]["formats"]
        assert catalog["compat"]["file/a3m"]["file|a3m"]["status"] == "direct"

    def test_filter_method_exposes_rule_widget_hints(self, catalog):
        method = catalog["methods"]["molecule_filter"]
        assert method["category"] == "filter" and method["error"] is None
        fields = {f["name"]: f for f in method["config"]}
        assert fields["criteria"]["widget"] == "rules"
        assert fields["criteria"]["metrics"]["fraction_in_box"] == "binding_site"
        assert "affinity_kcal_mol" in fields["rank_by"]["suggestions"]
        assert fields["top_percent"]["card_format"] == "top {value}% of {percent_of}"
        assert fields["top_percent"]["maximum"] == 100
        # Docking poses and score tables plug straight into the filter.
        assert catalog["compat"]["molecule.collection/sdf"]["molecule.collection|sdf"]["status"] == "direct"
        assert catalog["compat"]["table/csv"]["table|csv,tsv,json"]["status"] == "direct"

    def test_demo_filter_requires_binding_site_when_position_mode_set(self, registry):
        graph = demo_graph()
        graph["edges"] = [e for e in graph["edges"] if e["to"] != {"node": "m_filter_hits", "port": "binding_site"}]
        errors = validate_graph(graph)["errors"]
        assert {"node": "m_filter_hits", "port": "binding_site"}.items() <= errors[0].items()


class TestGraphConversion:
    def test_demo_graph_is_valid(self, registry):
        assert validate_graph(demo_graph()) == {"valid": True, "errors": []}

    def test_round_trip_preserves_pipeline_and_layout(self, registry):
        graph = demo_graph()
        data = yaml.safe_load(graph_to_yaml(graph))
        for key in ("name", "inputs", "outputs"):
            assert data[key] == demo_pipeline()[key]
        assert [m["inputs"] for m in data["methods"]] == [m["inputs"] for m in demo_pipeline()["methods"]]
        again = yaml_to_graph(graph_to_yaml(graph))
        positions = {n["id"]: (n["x"], n["y"]) for n in graph["nodes"]}
        assert {n["id"]: (n["x"], n["y"]) for n in again["nodes"]} == positions

    def test_imported_yaml_without_layout_has_null_positions(self):
        graph = pipeline_dict_to_graph(demo_pipeline())
        assert all(n["x"] is None for n in graph["nodes"])

    def test_structural_errors_are_attributed(self, registry):
        graph = demo_graph()
        graph["nodes"][0]["name"] = "bad name"
        with pytest.raises(GraphError) as info:
            graph_to_pipeline_dict(graph)
        assert info.value.errors[0]["node"] == graph["nodes"][0]["id"]

    def test_duplicate_input_edge_rejected(self, registry):
        graph = demo_graph()
        graph["edges"].append({"id": "dup", "from": {"node": "in_target_sequence", "port": "artifact"},
                               "to": {"node": "m_fold", "port": "sequence"}})
        result = validate_graph(graph)
        assert not result["valid"]
        assert result["errors"][0] == {**result["errors"][0], "node": "m_fold", "port": "sequence"}

    def test_validator_errors_mapped_to_node_and_port(self, registry):
        graph = demo_graph()
        graph["edges"] = [e for e in graph["edges"] if e["to"]["port"] != "receptor"]
        next(n for n in graph["nodes"] if n["id"] == "m_fold")["config"] = {"num_recycles": 99}
        errors = validate_graph(graph)["errors"]
        located = {(e["node"], e["port"]) for e in errors}
        assert ("m_dock", "receptor") in located
        assert ("m_fold", None) in located

    def test_incompatible_connection_reported(self, registry):
        graph = demo_graph()
        edge = next(e for e in graph["edges"] if e["to"] == {"node": "m_dock", "port": "ligands"})
        edge["from"] = {"node": "m_fold", "port": "confidence"}
        errors = validate_graph(graph)["errors"]
        assert any(e["node"] == "m_dock" and e["port"] == "ligands" and "incompatible" in e["message"] for e in errors)

    def test_passthrough_keys_survive(self):
        data = {**demo_pipeline(), "pooling_rules": {"dock": {"strategy": "best", "metric": "score"}}}
        graph = pipeline_dict_to_graph(data)
        assert graph_to_pipeline_dict(graph)["pooling_rules"] == data["pooling_rules"]


class TestServer:
    @pytest.fixture
    def client(self, registry):
        pytest.importorskip("flask")
        from rapier.workflow_builder.server import create_app
        return create_app(registry).test_client()

    def test_index_and_static(self, client):
        assert b"workflow builder" in client.get("/").data
        assert client.get("/static/js/main.js").status_code == 200

    def test_catalog_and_examples(self, client):
        assert "demo_docking" in client.get("/api/catalog").get_json()["methods"]
        names = client.get("/api/examples").get_json()
        assert names[0] == "demo" and "07_boltz2_vina" in names
        assert client.get("/api/examples/..%2Fpyproject").status_code == 404

    def test_example_sources_resolve_against_examples_dir(self, client):
        graph = client.get("/api/examples/03_vina_docking").get_json()
        errors = client.post("/api/validate", json=graph).get_json()["errors"]
        assert not any("not found" in e["message"] for e in errors)

    def test_export_import_cycle(self, client):
        text = client.post("/api/export", json=demo_graph()).get_json()["yaml"]
        graph = client.post("/api/import", data=text).get_json()
        assert {n["name"] for n in graph["nodes"]} >= {"fold", "pockets", "dock", "rescore"}
        assert client.post("/api/import", data="- just a list").status_code == 400
