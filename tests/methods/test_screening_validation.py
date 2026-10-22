"""Semantic screening checks compare pooled artifacts and per-input outcomes."""

import copy
import json

import pytest

from rapier.core.method import RunContext
from rapier.core.task import Task
from rapier.data import BindingSite, Molecules, Report, Scores, Table
from rapier.methods.screening.ligand_filter import LigandFilter
from rapier.methods.screening.molecule_filter import MoleculeFilter
from rapier.methods.screening.similarity_search import SimilaritySearch


def run(method, inputs, tmp_path):
    task = Task("screen", method, inputs=inputs)
    task.work_dir = tmp_path
    ctx = RunContext.for_task(task)
    inputs = method.format_inputs(inputs, ctx.input_dir)
    assert method.check_dependencies(inputs, ctx) == []
    outputs = method.format_outputs(method.run(inputs, ctx), ctx)
    assert method.validate_outputs(inputs, outputs, ctx) == []
    return inputs, outputs, ctx


def context(method, tmp_path):
    task = Task("screen-check", method, inputs={})
    task.work_dir = tmp_path
    return RunContext.for_task(task)


def poses(tmp_path, names=("a", "a", "b")):
    blocks = [f"{name}\n  test\n\n  1  0  0  0  0  0  0  0  0  0999 V2000\n"
              "    1.0000    2.0000    3.0000 C   0  0  0  0  0  0  0  0  0  0  0  0\nM  END\n"
              for name in names]
    return Molecules.write_sdf(blocks, tmp_path / "input.sdf")


def test_ligand_filter_accounts_for_invalid_and_duplicate_records(tmp_path):
    pytest.importorskip("rdkit")
    method = LigandFilter()
    _, outputs, _ = run(method, {"ligands": ["CCO same", "CCN same", "bad_smiles same"]}, tmp_path)
    rows = outputs["properties"].rows()
    assert [(row["id"], row["input_occurrence"]) for row in rows] == [("same", "1"), ("same", "2"), ("same", "3")]
    assert outputs["statistics"].load() == {
        "total": 3, "passed": 2, "failed": 1, "failed_by_reason": {"unparsable": 1},
    }
    assert len(outputs["filtered_ligands"].records()) == 2


def test_ligand_filter_aggregates_property_inventory_and_statistics_errors(tmp_path):
    pytest.importorskip("rdkit")
    method = LigandFilter()
    inputs, outputs, ctx = run(method, {"ligands": ["CCO a", "CCN b"]}, tmp_path)
    rows = outputs["properties"].rows()
    rows[0].update(mw="nan", hba="1.5", reasons="wrong")
    rows[1]["input_occurrence"] = "9"
    outputs["properties"] = Scores.from_records(rows, tmp_path / "bad.csv")
    outputs["filtered_ligands"] = Molecules.write_sdf([], tmp_path / "empty.sdf")
    outputs["statistics"] = Report.inline({"total": 99, "passed": 0, "failed": 1, "failed_by_reason": {}})
    errors = method.validate_outputs(inputs, outputs, ctx)
    assert any("mw must be a finite" in error for error in errors)
    assert any("hba must be an integer" in error for error in errors)
    assert any("reasons disagree" in error for error in errors)
    assert any("properties input inventory: missing" in error for error in errors)
    assert any("filtered_ligands inventory: missing" in error for error in errors)
    assert any("statistics.total" in error for error in errors)


@pytest.mark.parametrize("file_backed", [False, True])
def test_ligand_filter_checks_wrong_object_rows_alongside_malformed_json_rows(tmp_path, file_backed):
    pytest.importorskip("rdkit")
    method = LigandFilter()
    inputs, outputs, ctx = run(method, {"ligands": ["CCO a"]}, tmp_path)
    wrong_row = outputs["properties"].rows()[0]
    wrong_row.update(id="unknown", input_occurrence="bad", mw="nan", hba="1.5", passed="maybe")
    payload = [None, wrong_row, 42]
    if file_backed:
        path = tmp_path / "partial-properties.json"
        path.write_text(json.dumps(payload))
        outputs["properties"] = Scores.from_file(path, "json")
    else:
        outputs["properties"] = Scores.inline(payload, "json")
    errors = method.validate_outputs(inputs, outputs, ctx)
    for fragment in ("invalid row(s): 1, 3", "positive input_occurrence", "mw must be a finite", "hba must be an integer", "passed must be a boolean"):
        assert any(fragment in error for error in errors), (fragment, errors)


def test_ligand_filter_allows_valid_empty_result(tmp_path):
    pytest.importorskip("rdkit")
    _, outputs, _ = run(LigandFilter(config={"mw_max": 1}), {"ligands": ["CCO a"]}, tmp_path)
    assert outputs["filtered_ligands"].records() == []


@pytest.mark.parametrize("file_backed", [False, True])
@pytest.mark.parametrize("method,port", [(LigandFilter(), "statistics"), (MoleculeFilter(), "report")])
def test_screening_rejects_null_required_report(tmp_path, method, port, file_backed):
    if isinstance(method, LigandFilter):
        pytest.importorskip("rdkit")
        raw = {"ligands": ["CCO a"]}
    else:
        raw = {"molecules": poses(tmp_path)}
    inputs, outputs, ctx = run(method, raw, tmp_path)
    if file_backed:
        path = tmp_path / "null.json"
        path.write_text("null\n")
        outputs[port] = Report.from_file(path, "json")
    else:
        outputs[port] = Report.inline(None, "json")
    errors = method.validate_outputs(inputs, outputs, ctx)
    assert f"Output '{port}' must not be null" in errors


@pytest.mark.parametrize("input_format", ["smiles", "sdf"])
def test_ligand_filter_rejects_substitution_of_failed_duplicate_occurrence(tmp_path, input_format):
    Chem = pytest.importorskip("rdkit.Chem")
    method = LigandFilter(config={"mw_max": 100})
    ligands = Molecules.from_smiles(["CCO same", "CCCCCCCCCCCC same"])
    if input_format == "sdf":
        from rapier.methods.utils import rdkit_molecules, write_rdkit_sdf

        ligands = Molecules.from_file(write_rdkit_sdf([mol for _, mol in rdkit_molecules(ligands)], tmp_path / "source.sdf"))
    inputs, outputs, ctx = run(method, {"ligands": ligands}, tmp_path)
    assert [record.properties["input_occurrence"] for record in outputs["filtered_ligands"].records()] == ["1"]
    wrong = Chem.MolFromSmiles("CCCCCCCCCCCC")
    wrong.SetProp("_Name", "same")
    wrong.SetIntProp("input_occurrence", 2)
    outputs["filtered_ligands"] = Molecules.write_sdf([Chem.MolToMolBlock(wrong) +
        "\n> <input_occurrence>\n2\n"], tmp_path / "wrong.sdf")
    errors = method.validate_outputs(inputs, outputs, ctx)
    assert any("filtered_ligands inventory: missing" in error for error in errors)
    assert any("filtered_ligands inventory: unexpected" in error for error in errors)


def test_ligand_filter_replaces_stale_occurrence_annotation_without_changing_structure(tmp_path):
    pytest.importorskip("rdkit")
    source = poses(tmp_path, names=("same", "same"))
    original = source.records()
    source = Molecules.write_sdf([record.block + "\n> <input_occurrence>\n99\n" for record in original], tmp_path / "stale.sdf")
    _, outputs, _ = run(LigandFilter(), {"ligands": source}, tmp_path)
    filtered = outputs["filtered_ligands"].records()
    assert [record.properties["input_occurrence"] for record in filtered] == ["1", "2"]
    assert [record.atoms for record in filtered] == [record.atoms for record in original]


@pytest.mark.parametrize("method,source,ports", [
    (LigandFilter(), "ligands", ("filtered_ligands", "properties", "statistics")),
    (MoleculeFilter(), "molecules", ("filtered_molecules", "filtered_scores", "report")),
    (SimilaritySearch(), "database", ("similar_molecules", "similarity_scores")),
])
def test_screening_validators_collect_independent_read_failures(tmp_path, method, source, ports):
    inputs = {source: poses(tmp_path)}
    if isinstance(method, SimilaritySearch):
        inputs["queries"] = inputs[source]
    bad_report = tmp_path / "broken.json"
    bad_report.write_text("{invalid json")
    outputs = {ports[0]: Molecules.from_file(tmp_path / "missing.sdf"),
               ports[1]: Scores.from_file(bad_report, "json")}
    if len(ports) > 2:
        outputs[ports[2]] = Report.from_file(bad_report, "json")
    ctx = context(method, tmp_path)
    errors = method.validate_outputs(inputs, outputs, ctx)
    assert any(f"Cannot read '{ports[0]}'" in error for error in errors)
    assert any(f"Cannot read '{ports[1]}'" in error for error in errors)
    if len(ports) > 2:
        assert any(f"Cannot read '{ports[2]}'" in error for error in errors)


def test_molecule_filter_dependency_checks_all_conditional_inputs(tmp_path):
    method = MoleculeFilter(config={"position_mode": "centroid_in_box",
                                   "criteria": [{"column": "reference_distance", "op": "<", "value": 2}]})
    inputs = {"molecules": poses(tmp_path), "scores": [Table.inline([{"score": 1}], "json")],
              "binding_site": BindingSite.inline({"center": [0, 0, 0], "size": [1, -1, 1]}, "box"),
              "reference_ligand": Molecules.write_sdf([], tmp_path / "reference.sdf")}
    ctx = context(method, tmp_path)
    errors = method.check_dependencies(inputs, ctx)
    assert any("Cannot join scores[0]" in error for error in errors)
    assert any("binding_site" in error for error in errors)
    assert any("reference_ligand" in error for error in errors)


def test_molecule_filter_rejects_empty_input_and_duplicate_score_keys(tmp_path):
    method = MoleculeFilter()
    ctx = context(method, tmp_path)
    errors = method.check_dependencies({"molecules": poses(tmp_path), "scores": [Scores.inline([
        {"id": "a", "pose": 1}, {"id": "a", "pose": 1}], "json")]}, ctx)
    assert any("duplicate molecule/pose keys" in error for error in errors)
    errors = method.check_dependencies({"molecules": Molecules.write_sdf([], tmp_path / "empty.sdf")}, ctx)
    assert "molecules must contain at least one record" in errors


def test_molecule_filter_validates_selected_inventory_and_complete_report(tmp_path):
    method = MoleculeFilter()
    inputs, outputs, ctx = run(method, {"molecules": poses(tmp_path)}, tmp_path)
    report = outputs["report"].load()
    assert len(report["outcomes"]) == 3
    rows = outputs["filtered_scores"].rows()
    rows[0]["pose_rank"] = "99"
    outputs["filtered_scores"] = Scores.from_records(rows, tmp_path / "bad.csv")
    report.update(total_poses=99, rejected_by={"fake rejection": 5})
    report["gate"]["limit"] = 999
    outputs["report"] = Report.inline(report)
    errors = method.validate_outputs(inputs, outputs, ctx)
    assert any("unknown input pose keys" in error for error in errors)
    assert any("selected outcome/score inventory" in error for error in errors)
    assert any("report.total_poses" in error for error in errors)
    assert any("report.rejected_by" in error for error in errors)
    assert any("report.gate limit" in error for error in errors)


def test_molecule_filter_empty_output_obeys_configuration(tmp_path):
    method = MoleculeFilter(config={"criteria": [{"column": "heavy_atoms", "op": ">", "value": 99}]})
    inputs, outputs, ctx = run(method, {"molecules": poses(tmp_path)}, tmp_path)
    assert outputs["filtered_scores"].rows() == []
    method.config["fail_if_empty"] = True
    assert "No poses passed the filter" in method.validate_outputs(inputs, outputs, ctx)


def test_molecule_filter_accounts_for_an_explicitly_empty_score_table(tmp_path):
    method = MoleculeFilter()
    inputs, outputs, ctx = run(method, {"molecules": poses(tmp_path), "scores": [Scores.inline([], "json")]}, tmp_path)
    report = outputs["report"].load()
    assert report["poses_without_scores"] == 3
    assert all(outcome["missing_scores"] for outcome in report["outcomes"])
    report["poses_without_scores"] = 0
    for outcome in report["outcomes"]:
        outcome["missing_scores"] = False
    outputs["report"] = Report.inline(report, "json")
    errors = method.validate_outputs(inputs, outputs, ctx)
    assert any("score flags disagree" in error for error in errors)


def test_molecule_filter_allows_absent_coordinates_and_rejects_nonfinite_coordinates(tmp_path):
    method = MoleculeFilter()
    missing = Molecules.write_sdf(["no_coordinates\n  test\n\n  0  0  0  0  0  0  0  0  0  0999 V2000\nM  END\n"], tmp_path / "zero.sdf")
    _, outputs, _ = run(method, {"molecules": missing}, tmp_path / "missing")
    assert outputs["report"].load()["poses_without_coordinates"] == 1
    inputs, outputs, ctx = run(method, {"molecules": poses(tmp_path)}, tmp_path / "finite")
    text = outputs["filtered_molecules"].path.read_text().replace("    1.0000", "       nan", 1)
    outputs["filtered_molecules"].path.write_text(text)
    errors = method.validate_outputs(inputs, outputs, ctx)
    assert any("coordinates must be finite" in error for error in errors)


def test_molecule_filter_rejects_malformed_outcome_without_losing_other_errors(tmp_path):
    method = MoleculeFilter()
    inputs, outputs, ctx = run(method, {"molecules": poses(tmp_path)}, tmp_path)
    report = outputs["report"].load()
    report["outcomes"][0]["id"] = ["invalid"]
    report["gate"]["admitted_percent"] = "nan"
    outputs["report"] = Report.inline(report)
    errors = method.validate_outputs(inputs, outputs, ctx)
    assert any("invalid outcome" in error for error in errors)
    assert any("admitted_percent" in error for error in errors)


def test_similarity_accounts_for_duplicates_invalid_unmatched_and_truncated(tmp_path):
    pytest.importorskip("rdkit")
    method = SimilaritySearch(config={"similarity_threshold": 0.9, "max_results": 1})
    _, outputs, _ = run(method, {
        "queries": ["CCO query", "bad_smiles bad_query"],
        "database": ["CCO duplicate", "CCO duplicate", "CCO duplicate__1", "c1ccccc1 unrelated", "bad_smiles invalid"],
    }, tmp_path)
    inventory = outputs["similarity_scores"].metadata["search_inventory"]
    assert inventory["candidate_pairs"] == 3 and inventory["truncated_pairs"] == 2
    assert [item["status"] for item in inventory["database"]] == ["matched", "truncated", "truncated", "unmatched", "invalid"]
    ids = [item["id"] for item in inventory["database"]]
    assert len(set(ids)) == 5
    assert ids[0] != "duplicate__1"  # The suffix must not collide with an actual input name.
    assert inventory["queries"][1]["status"] == "invalid"
    assert outputs["similar_molecules"].ids() == [ids[0]]


def test_similarity_rejects_invalid_query_before_execution(tmp_path):
    pytest.importorskip("rdkit")
    method = SimilaritySearch()
    ctx = context(method, tmp_path)
    errors = method.check_dependencies({"queries": Molecules.from_smiles(["bad_smiles"]),
                                        "database": Molecules.from_file(tmp_path / "missing.sdf")}, ctx)
    assert "No valid query molecules" in errors
    assert any("Cannot read input 'database'" in error for error in errors)


def test_similarity_empty_matches_have_complete_inventory(tmp_path):
    pytest.importorskip("rdkit")
    _, outputs, _ = run(SimilaritySearch(config={"similarity_threshold": 1}), {
        "queries": ["CCO query"], "database": ["c1ccccc1 aromatic", "bad_smiles invalid"],
    }, tmp_path)
    assert outputs["similar_molecules"].records() == []
    assert outputs["similarity_scores"].rows() == []
    assert [item["status"] for item in outputs["similarity_scores"].metadata["search_inventory"]["database"]] == ["unmatched", "invalid"]


def test_similarity_aggregates_semantic_pair_and_accounting_errors(tmp_path):
    pytest.importorskip("rdkit")
    method = SimilaritySearch(config={"similarity_threshold": 0})
    inputs, outputs, ctx = run(method, {"queries": ["CCO query"], "database": ["CCO a", "CCN b"]}, tmp_path)
    rows = outputs["similarity_scores"].rows()
    rows[0]["similarity"] = "nan"
    rows[1]["query_id"] = "unknown"
    rows.append(dict(rows[1]))
    metadata = copy.deepcopy(outputs["similarity_scores"].metadata)
    metadata["search_inventory"]["database"][0]["status"] = "unmatched"
    outputs["similarity_scores"] = Scores.from_records(rows, tmp_path / "bad.csv", metadata=metadata)
    outputs["similar_molecules"] = Molecules.write_sdf([], tmp_path / "empty.sdf")
    errors = method.validate_outputs(inputs, outputs, ctx)
    for fragment in ("unknown query", "similarity must be finite", "duplicate (id, query_id)",
                     "similar_molecules inventory", "returned_pairs disagrees", "status disagrees", "inventories disagree"):
        assert any(fragment in error for error in errors), (fragment, errors)
