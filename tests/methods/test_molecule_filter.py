"""Tests for the score/position molecule filter."""

import csv
import json

import pytest

from rapier.artifacts import Artifact, ArtifactStorage
from rapier.config.parser import ConfigParser
from rapier.config.validator import ConfigValidator
from rapier.core.enums import TaskStatus
from rapier.core.task import Task
from rapier.methods.registry import get_registry
from rapier.methods.screening.molecule_filter import (
    MoleculeFilter,
    evaluate_rule,
    filter_poses,
    parse_sdf_record,
    split_sdf,
)


def mol_block(name, coords, props=None):
    """Minimal V2000 record with carbon atoms at the given coordinates (plus one H)."""
    atoms = coords + [(coords[0][0] + 1.0, coords[0][1], coords[0][2])]
    symbols = ["C"] * len(coords) + ["H"]
    lines = [name, "  test", "", f"{len(atoms):3d}  0  0  0  0  0  0  0  0  0999 V2000"]
    for (x, y, z), symbol in zip(atoms, symbols):
        lines.append(f"{x:10.4f}{y:10.4f}{z:10.4f} {symbol:<3} 0  0  0  0  0  0  0  0  0  0  0  0")
    lines.append("M  END")
    for key, value in (props or {}).items():
        lines += [f">  <{key}>", str(value), ""]
    return "\n".join(lines) + "\n$$$$\n"


BOX = {"center": [0.0, 0.0, 0.0], "size": [10.0, 10.0, 10.0]}
INSIDE = [(0.0, 0.0, 0.0), (1.0, 1.0, 0.0)]
OUTSIDE = [(20.0, 0.0, 0.0), (21.0, 0.0, 0.0)]
HALF = [(0.0, 0.0, 0.0), (20.0, 0.0, 0.0)]

# Two Vina-style poses for "lig_a", one each for "lig b" (space -> sanitized in CSV) and "lig_c".
SDF = (
    mol_block("lig_a", INSIDE)
    + mol_block("lig_a", OUTSIDE)
    + mol_block("lig b", HALF, {"CNNscore": 0.9})
    + mol_block("lig_c", INSIDE)
)
ROWS = [
    {"ligand": "lig_a", "mode": "2", "affinity_kcal_mol": "-6.0"},
    {"ligand": "lig_a", "mode": "1", "affinity_kcal_mol": "-9.0"},
    {"ligand": "lig_b", "mode": "1", "affinity_kcal_mol": "-8.0"},
    {"ligand": "lig_c", "mode": "1", "affinity_kcal_mol": "-5.0"},
]


def config(**overrides):
    return {**MoleculeFilter().filter_config, **overrides}


def pose_config(**overrides):
    """Config that passes every surviving pose through (no per-molecule collapse)."""
    return config(best_pose_only=False, **overrides)


def names(kept):
    return [(p["name"], p["pose_rank"]) for p in kept]


class TestParsing:
    def test_split_and_parse(self):
        records = split_sdf(SDF)
        assert len(records) == 4
        parsed = parse_sdf_record(records[2])
        assert parsed["title"] == "lig b"
        assert len(parsed["heavy"]) == 2  # hydrogen excluded
        assert parsed["properties"] == {"CNNscore": "0.9"}

    def test_v3000(self):
        record = "\n".join([
            "v3", "", "", "  0  0  0     0  0            999 V3000", "M  V30 BEGIN CTAB",
            "M  V30 COUNTS 2 0 0 0 0", "M  V30 BEGIN ATOM",
            "M  V30 1 C 1.0 2.0 3.0 0", "M  V30 2 H 0.0 0.0 0.0 0",
            "M  V30 END ATOM", "M  V30 END CTAB", "M  END",
        ]) + "\n"
        assert parse_sdf_record(record)["heavy"] == [(1.0, 2.0, 3.0)]

    @pytest.mark.parametrize("rule,value,expected", [
        ({"op": "<=", "value": -7}, "-7.0", True),
        ({"op": ">", "value": 0.5}, 0.4, False),
        ({"op": "between", "value": [1, 2]}, 1.5, True),
        ({"op": "in", "value": ["A", 3]}, "3", True),
        ({"op": "not_in", "value": ["A"]}, "A", False),
        ({"op": "==", "value": "vina"}, "vina", True),
        ({"op": "<", "value": 1}, "n/a", None),
        ({"op": "<", "value": 1}, None, None),
        ({"op": "exists"}, "", False),
    ])
    def test_evaluate_rule(self, rule, value, expected):
        assert evaluate_rule({"column": "x", **rule}, value) is expected


class TestFiltering:
    def test_score_join_pairs_modes_in_order(self):
        kept, report = filter_poses(SDF, [ROWS], pose_config())
        assert report["poses_without_scores"] == 0
        assert [p["scores"]["affinity_kcal_mol"] for p in kept] == ["-9.0", "-6.0", "-8.0", "-5.0"]

    def test_score_threshold(self):
        kept, report = filter_poses(SDF, [ROWS], config(criteria=[{"column": "affinity_kcal_mol", "op": "<=", "value": -7}]))
        assert names(kept) == [("lig_a", 1), ("lig b", 1)]
        assert report["rejected_by"] == {"affinity_kcal_mol <= -7": 2}

    def test_sdf_property_and_combine_any(self):
        rules = [{"column": "CNNscore", "op": ">=", "value": 0.8}, {"column": "affinity_kcal_mol", "op": "<", "value": -8.5}]
        kept, _ = filter_poses(SDF, [ROWS], config(criteria=rules, combine="any"))
        assert names(kept) == [("lig_a", 1), ("lig b", 1)]
        kept, report = filter_poses(SDF, [ROWS], config(criteria=rules))
        assert kept == [] and report["rejected_by"]["CNNscore missing"] == 3

    def test_on_missing_pass(self):
        rules = [{"column": "CNNscore", "op": ">=", "value": 0.8}]
        kept, _ = filter_poses(SDF, [ROWS], pose_config(criteria=rules, on_missing="pass"))
        assert len(kept) == 4

    @pytest.mark.parametrize("mode", ["centroid_in_box", "all_atoms_in_box", "fraction_in_box"])
    def test_position_modes(self, mode):
        # Only the INSIDE poses qualify: OUTSIDE is far away; HALF has its centroid at x=10
        # (outside a 10 Å box) and only half its heavy atoms inside.
        kept, report = filter_poses(SDF, [ROWS], config(position_mode=mode), box=BOX)
        assert names(kept) == [("lig_a", 1), ("lig_c", 1)]
        assert sum(report["rejected_by"].values()) == 2

    def test_fraction_threshold_and_padding(self):
        kept, _ = filter_poses(SDF, [ROWS], config(position_mode="fraction_in_box", min_fraction_in_box=0.5), box=BOX)
        assert ("lig b", 1) in names(kept)
        kept, _ = filter_poses(SDF, [ROWS], config(position_mode="centroid_in_box", box_padding=6.0), box=BOX)
        assert ("lig b", 1) in names(kept)

    def test_distance_metrics_as_rules(self):
        rules = [{"column": "centroid_distance", "op": "<", "value": 3.0},
                 {"column": "reference_distance", "op": "<", "value": 3.0}]
        kept, _ = filter_poses(SDF, [ROWS], config(criteria=rules), box=BOX, reference=(0.5, 0.5, 0.0))
        assert names(kept) == [("lig_a", 1), ("lig_c", 1)]
        assert kept[0]["metrics"]["centroid_distance"] == pytest.approx(0.7071, abs=1e-3)

    def test_gate_counts_molecules_and_keeps_best_pose(self):
        kept, report = filter_poses(SDF, [ROWS], config(rank_by="affinity_kcal_mol", top_n=2))
        assert names(kept) == [("lig_a", 1), ("lig b", 1)]
        assert [p["metrics"]["filter_rank"] for p in kept] == [1, 2]
        assert report["gate"] == {**report["gate"], "unit": "molecules", "entered": 3, "limit": 2,
                                  "admitted": 2, "dropped_by_limit": 1}
        # all_poses: the admitted molecule brings every surviving pose along.
        kept, _ = filter_poses(SDF, [ROWS], pose_config(rank_by="affinity_kcal_mol", top_n=1))
        assert names(kept) == [("lig_a", 1), ("lig_a", 2)]
        kept, _ = filter_poses(SDF, [ROWS], config(rank_by="affinity_kcal_mol", ascending=False, top_n=1))
        assert names(kept) == [("lig_c", 1)]

    def test_gate_by_poses(self):
        kept, report = filter_poses(SDF, [ROWS], pose_config(rank_by="affinity_kcal_mol", top_n=2, limit_by="poses"))
        assert names(kept) == [("lig_a", 1), ("lig b", 1)] and report["gate"]["entered"] == 4
        # best_pose_only drops duplicate poses before the cap, so they don't use up slots.
        kept, _ = filter_poses(SDF, [ROWS], config(rank_by="affinity_kcal_mol", top_n=3, limit_by="poses"))
        assert names(kept) == [("lig_a", 1), ("lig b", 1), ("lig_c", 1)]

    def test_top_percent_of_input_vs_passed(self):
        rule = [{"column": "affinity_kcal_mol", "op": "<=", "value": -5.5}]  # lig_c fails
        kept, report = filter_poses(SDF, [ROWS], config(criteria=rule, rank_by="affinity_kcal_mol", top_percent=34))
        assert report["gate"]["limit"] == 2  # ceil(34% of 3 entered)
        kept, report = filter_poses(SDF, [ROWS], config(criteria=rule, rank_by="affinity_kcal_mol",
                                                        top_percent=34, percent_of="passed"))
        assert report["gate"]["limit"] == 1  # ceil(34% of 2 passed)
        # Tighter of top_n and top_percent wins.
        kept, report = filter_poses(SDF, [ROWS], config(rank_by="affinity_kcal_mol", top_percent=100, top_n=1))
        assert report["gate"]["limit"] == 1

    def test_one_percent_of_ten_thousand(self):
        """The gating use case: 10,000 candidates in, exactly the best 100 out."""
        n = 10_000
        sdf = "".join(mol_block(f"m{i}", INSIDE) for i in range(n))
        rows = [{"ligand": f"m{i}", "affinity_kcal_mol": f"{-((i * 7919) % n) / 1000:.3f}"} for i in range(n)]
        kept, report = filter_poses(sdf, [rows], config(rank_by="affinity_kcal_mol", top_percent=1))
        assert report["gate"]["entered"] == n and report["gate"]["admitted"] == 100
        assert report["gate"]["admitted_percent"] == 1.0
        scores = [float(p["scores"]["affinity_kcal_mol"]) for p in kept]
        assert scores == sorted(scores)
        assert max(scores) <= min(float(r["affinity_kcal_mol"]) for r in rows if r["ligand"] not in {p["name"] for p in kept})

    def test_gate_needs_rank_by(self):
        with pytest.raises(ValueError, match="rank_by"):
            MoleculeFilter(parameters={"top_percent": 1})

    def test_single_row_applies_to_all_poses_and_unknown_column_reported(self):
        rows = [{"ligand": "lig_a", "affinity_kcal_mol": "-9"}]
        kept, report = filter_poses(SDF, [rows], pose_config(criteria=[{"column": "typo", "op": "<", "value": 1}], on_missing="pass"))
        assert [p["scores"].get("affinity_kcal_mol") for p in kept[:2]] == ["-9", "-9"]
        assert report["poses_without_scores"] == 2
        assert report["unknown_columns"] == ["typo"]

    def test_table_without_key_requires_matching_length(self):
        with pytest.raises(ValueError, match="id_column"):
            filter_poses(SDF, [[{"score": 1}]], config())
        kept, _ = filter_poses(SDF, [[{"score": i} for i in range(4)]], pose_config())
        assert [p["scores"]["score"] for p in kept] == [0, 1, 2, 3]


class TestConfigAndBindings:
    @pytest.mark.parametrize("criteria,message", [
        ([{"column": "x", "op": "~", "value": 1}], "invalid op"),
        ([{"column": "x", "op": "<", "value": "a"}], "numeric"),
        ([{"column": "x", "op": "between", "value": [3, 1]}], "low must be"),
        ([{"op": "<", "value": 1}], "column"),
        ([{"column": "x", "op": "<", "value": 1, "extra": 1}], "unknown key"),
    ])
    def test_invalid_criteria(self, criteria, message):
        with pytest.raises(ValueError, match=message):
            MoleculeFilter(parameters={"criteria": criteria})

    def test_validate_bindings(self):
        method = MoleculeFilter(parameters={
            "position_mode": "centroid_in_box",
            "criteria": [{"column": "reference_distance", "op": "<", "value": 2}],
        })
        errors = method.validate_bindings(["molecules"])
        assert any("'binding_site'" in e for e in errors)
        assert any("'reference_ligand'" in e for e in errors)
        assert method.validate_bindings(["molecules", "binding_site", "reference_ligand"]) == []

    def test_registered(self):
        registry = get_registry()
        registry.auto_discover()
        assert registry.get("molecule_filter") is MoleculeFilter


class TestExecute:
    def test_execute_writes_annotated_outputs(self, tmp_path):
        sdf = tmp_path / "poses.sdf"
        sdf.write_text(SDF)
        table = tmp_path / "scores.csv"
        with table.open("w", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(ROWS[0]))
            writer.writeheader()
            writer.writerows(ROWS)
        method = MoleculeFilter(parameters={
            "criteria": [{"column": "affinity_kcal_mol", "op": "<=", "value": -7}],
            "position_mode": "centroid_in_box", "rank_by": "affinity_kcal_mol",
        })
        task = Task("filter", method, inputs={
            "molecules": Artifact("molecule.collection", "sdf", sdf),
            "scores": [Artifact("table", "csv", table)],
            "binding_site": Artifact("binding_site", "box", BOX, storage=ArtifactStorage.INLINE),
        })
        task.work_dir = tmp_path / "work"
        assert method.execute(task) == TaskStatus.COMPLETED, task.error_message
        valid, errors = method.output_spec.validate(task.outputs)
        assert valid, errors

        out = split_sdf(task.outputs["filtered_molecules"].path.read_text())
        assert len(out) == 1
        props = parse_sdf_record(out[0])["properties"]
        assert props["affinity_kcal_mol"] == "-9.0" and props["filter_rank"] == "1"
        rows = list(csv.DictReader(task.outputs["filtered_scores"].path.open()))
        assert rows[0]["name"] == "lig_a" and rows[0]["centroid_in_box"] == "True"
        report = json.loads(task.outputs["report"].path.read_text())
        assert report["kept"] == 1 and report["total_molecules"] == 3

    def test_rdkit_reads_annotated_sdf(self, tmp_path):
        Chem = pytest.importorskip("rdkit.Chem")
        kept, _ = filter_poses(SDF, [ROWS], pose_config())
        from rapier.methods.screening.molecule_filter import annotate_record
        path = tmp_path / "out.sdf"
        path.write_text("".join(annotate_record(p) for p in kept))
        mols = [m for m in Chem.SDMolSupplier(str(path), sanitize=False)]
        assert len(mols) == 4 and all(m is not None for m in mols)
        assert mols[2].GetProp("CNNscore") == "0.9" and mols[2].GetProp("affinity_kcal_mol") == "-8.0"

    def test_fail_if_empty(self, tmp_path):
        sdf = tmp_path / "poses.sdf"
        sdf.write_text(SDF)
        method = MoleculeFilter(parameters={"criteria": [{"column": "heavy_atoms", "op": ">", "value": 99}], "fail_if_empty": True})
        task = Task("f", method, inputs={"molecules": Artifact("molecule.collection", "sdf", sdf)})
        task.work_dir = tmp_path
        assert method.execute(task) == TaskStatus.FAILED
        assert "No poses passed" in task.error_message


def test_validator_reports_missing_binding_site():
    text = """
name: p
inputs:
  ligands: {type: molecule.collection, format: sdf, value: placeholder}
methods:
  - id: f
    type: molecule_filter
    inputs: {molecules: pipeline.ligands}
    config: {position_mode: centroid_in_box}
"""
    config = ConfigParser().parse_string(text)
    errors = ConfigValidator().validate_contracts(config)
    assert any("Method 'f': input port 'binding_site' must be connected" in e for e in errors)
