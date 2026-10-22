"""Tests for the typed data classes in rapier.data."""

import copy
import dataclasses
import json

import pytest

from rapier.artifacts import Artifact, ArtifactStorage, PortSpec, get_artifact_registry, get_converter_registry
from rapier.data import BindingSite, Molecule, Molecules, Poses, ProteinSequence, Report, Scores, molecule_id

SDF = (
    "lig a\n  test\n\n  1  0  0  0  0  0  0  0  0  0999 V2000\n"
    "    1.0000    2.0000    3.0000 C   0  0  0  0  0  0  0  0  0  0  0  0\nM  END\n> <score>\n-7.1\n\n$$$$\n"
    "lig a\n  test\n\n  1  0  0  0  0  0  0  0  0  0999 V2000\n"
    "    0.0000    0.0000    0.0000 C   0  0  0  0  0  0  0  0  0  0  0  0\nM  END\n$$$$\n"
)


class TestDispatch:
    def test_type_string_selects_data_class(self, tmp_path):
        assert type(Artifact("table.scores", "csv", tmp_path / "s.csv")) is Scores
        assert type(Artifact("molecule.poses", "sdf", tmp_path / "p.sdf")) is Poses
        # Unknown dotted subtypes fall back to their registered prefix.
        assert type(Artifact("table.scores.custom", "csv", tmp_path / "s.csv")) is Scores
        assert type(Artifact("not.registered", "x", tmp_path / "x")) is Artifact

    def test_copy_replace_and_round_trip_keep_class(self, tmp_path):
        scores = Scores.from_file(tmp_path / "s.csv", metadata={"columns": {"x": {"better": "higher"}}})
        assert type(dataclasses.replace(scores, format="tsv")) is Scores
        assert type(copy.deepcopy(scores)) is Scores
        restored = Artifact.from_dict(scores.to_dict())
        assert type(restored) is Scores and restored.directions()["x"] == "higher"

    def test_class_rejects_foreign_type(self, tmp_path):
        with pytest.raises(ValueError, match="cannot hold"):
            Molecule("molecule.collection", "sdf", tmp_path / "x.sdf")
        with pytest.raises(ValueError, match="cannot hold"):
            Scores("table", "csv", tmp_path / "x.csv")

    def test_registry_mirrors_class_hierarchy(self):
        registry = get_artifact_registry()
        assert registry.get("molecule.poses").parents == ("molecule.collection",)
        assert registry.is_compatible("table.scores", "table")
        assert not registry.is_compatible("table", "table.scores")
        assert registry.get("table.scores").description.startswith("Scores keyed")


class TestSequencesAndSites:
    def test_sequence_coercion_and_validation(self, tmp_path):
        seq = PortSpec(ProteinSequence, ("inline", "fasta")).coerce("mktay")
        assert isinstance(seq, ProteinSequence) and seq.storage == ArtifactStorage.INLINE
        assert seq.sequence == "MKTAY"
        fasta = tmp_path / "t.fasta"
        fasta.write_text(">t\nMKT\nAYI\n")
        assert ProteinSequence.from_file(fasta).sequence == "MKTAYI"
        assert not get_artifact_registry().validate(ProteinSequence.from_string("MK1"))[0]

    def test_binding_site_box(self):
        site = PortSpec(BindingSite, "box").coerce({"center": [1, 2, 3], "size": [10, 10, 10]})
        assert isinstance(site, BindingSite)
        assert site.box() == {"center": [1.0, 2.0, 3.0], "size": [10.0, 10.0, 10.0]}
        bad = BindingSite.inline({"center": [0, 0], "size": [1, 1, 1]}, "box")
        assert not get_artifact_registry().validate(bad)[0]


class TestMolecules:
    def test_smiles_lists_and_files(self, tmp_path):
        inline = PortSpec(Molecules, ("smiles", "sdf")).coerce(["CCO ethanol", "c1ccccc1O"])
        assert [(r.id, r.smiles) for r in inline.records()] == [("ethanol", "CCO"), ("mol_2", "c1ccccc1O")]
        path = tmp_path / "lib.smi"
        path.write_text("CCO ethanol\nCC(=O)O acetic acid\n")
        records = Molecules.coerce(str(path)).records()
        assert [r.id for r in records] == ["ethanol", "acetic_acid"]
        assert records[1].name == "acetic acid"

    def test_sdf_records_rank_poses_by_id(self, tmp_path):
        path = tmp_path / "poses.sdf"
        path.write_text(SDF)
        records = Poses.from_file(path).records()
        assert [(r.id, r.pose) for r in records] == [("lig_a", 1), ("lig_a", 2)]
        assert records[0].properties == {"score": "-7.1"}
        assert records[0].heavy_atoms == [(1.0, 2.0, 3.0)]
        assert molecule_id("lig a") == "lig_a"

    def test_write_sdf_round_trips_blocks(self, tmp_path):
        path = tmp_path / "poses.sdf"
        path.write_text(SDF)
        blocks = [r.block for r in Molecules.from_file(path).records()]
        out = Molecules.write_sdf(blocks, tmp_path / "out.sdf")
        assert [r.properties for r in out.records()] == [{"score": "-7.1"}, {}]
        assert out.path.read_text().count("$$$$") == 2

    def test_poses_convert_to_smiles_for_collection_ports(self):
        path = get_converter_registry().find_path("molecule.poses", "sdf", ("molecule.collection",), ("smiles",))
        if path is None:
            pytest.skip("RDKit converter unavailable")
        assert [c.name for c in path] == ["rdkit.molecule.collection.sdf_to_smiles"]


class TestScores:
    @pytest.mark.parametrize("file_backed", [False, True])
    def test_json_rows_do_not_silently_drop_malformed_entries(self, tmp_path, file_backed):
        if file_backed:
            path = tmp_path / "malformed.json"
            path.write_text('[{"id":"a"}, null, 42]')
            table = Scores.from_file(path, "json")
        else:
            table = Scores.inline([{"id": "a"}, None, 42], "json")
        with pytest.raises(ValueError, match=r"invalid row\(s\): 2, 3"):
            table.rows()

    def test_empty_json_row_wrappers_remain_empty(self):
        assert Scores.inline({"rows": []}, "json").rows() == []
        assert Scores.inline({"records": []}, "json").rows() == []

    @pytest.mark.parametrize("file_backed", [False, True])
    @pytest.mark.parametrize("wrapper", [None, "rows", "records"])
    def test_validation_reader_keeps_object_rows_and_reports_all_invalid_positions(self, tmp_path, file_backed, wrapper):
        payload = [None, {"id": "a", "score": "wrong"}, 42, {"id": "b"}]
        if wrapper is not None:
            payload = {wrapper: payload}
        if file_backed:
            path = tmp_path / "partial.json"
            path.write_text(json.dumps(payload))
            scores = Scores.from_file(path, "json")
        else:
            scores = Scores.inline(payload, "json")
        rows, errors = scores.rows_with_errors()
        assert rows == [{"id": "a", "score": "wrong"}, {"id": "b"}]
        assert errors == ["Table rows must be objects; invalid row(s): 1, 3"]
        with pytest.raises(ValueError, match=r"invalid row\(s\): 1, 3"):
            scores.rows()

    def test_validation_reader_reports_file_parse_and_payload_failures(self, tmp_path):
        malformed = tmp_path / "broken.json"
        malformed.write_text("{broken json")
        for scores in (Scores.from_file(malformed, "json"),
                       Scores.from_file(tmp_path / "missing.json", "json"),
                       Scores.inline({"rows": None}, "json")):
            rows, errors = scores.rows_with_errors()
            assert rows == [] and errors
        assert Scores.inline({"rows": []}, "json").rows_with_errors() == ([], [])

    def test_validation_reader_preserves_csv_rows(self, tmp_path):
        scores = Scores.from_records([{"id": "a", "score": 1}], tmp_path / "scores.csv")
        assert scores.rows_with_errors() == (scores.rows(), [])

    def test_from_records_writes_canonical_table_with_directions(self, tmp_path):
        scores = Scores.from_records(
            [{"affinity_kcal_mol": -7.0, "pose": 1, "id": "a"}], tmp_path / "s.csv",
            directions={"affinity_kcal_mol": "lower"}, units={"affinity_kcal_mol": "kcal/mol"},
        )
        assert scores.path.read_text().splitlines()[0] == "id,pose,affinity_kcal_mol"
        assert scores.metadata["columns"]["affinity_kcal_mol"] == {"better": "lower", "unit": "kcal/mol"}

    def test_legacy_key_columns_are_normalized(self, tmp_path):
        path = tmp_path / "old.csv"
        path.write_text("ligand,mode,affinity_kcal_mol\nlig b,2,-6.5\n")
        assert Scores.from_file(path).records() == [
            {"ligand": "lig b", "mode": "2", "affinity_kcal_mol": "-6.5", "id": "lig_b", "pose": 2}
        ]

    def test_rows_become_scores_on_disk_through_a_port(self, tmp_path):
        artifact = PortSpec(Scores, "csv").coerce([{"id": "a", "s": 1}], tmp_path, "scores")
        assert isinstance(artifact, Scores) and artifact.path == tmp_path / "scores.csv"
        inline = PortSpec(Scores, "csv").coerce([{"id": "a", "s": 1}])
        assert inline.storage == ArtifactStorage.INLINE and inline.format == "json"

    def test_table_formats_convert_and_reports_load(self, tmp_path):
        path = get_converter_registry().find_path("table.scores", "json", ("table",), ("csv",))
        assert [c.name for c in path] == ["core.table.json_to_csv"]
        report = PortSpec(Report, "json").coerce({"kept": 3}, tmp_path, "report")
        assert report.load() == {"kept": 3}
