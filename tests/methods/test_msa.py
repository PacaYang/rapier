"""Contracts shared by ColabFold, AF3 and Boltz-2."""

import pytest

from rapier.data import File, ProteinSequence
from rapier.methods import AF3Prediction
from rapier.methods.msa import protein_records, resolve_msas, validate_a3m


def test_chain_resolution_flattens_records_and_preserves_identity():
    fasta = ProteinSequence.from_string(
        ">old\nacde\n>other\nGGGG\n", {"chain_ids": ["C", "D"]}
    )
    bare = ProteinSequence.from_string("FGHI")
    records = protein_records([fasta, bare])
    assert [(record["chain_id"], record["sequence"]) for record in records] == [
        ("C", "ACDE"),
        ("D", "GGGG"),
        ("A", "FGHI"),
    ]
    assert records[0]["source_artifact_id"] == fasta.artifact_id
    assert records[1]["record_index"] == 1
    assert records[2]["source_artifact_id"] == bare.artifact_id


def test_chain_fallback_skips_explicit_ids_and_extends_past_z():
    values = [ProteinSequence.from_string("ACDE") for _ in range(27)]
    values.append(ProteinSequence.from_string("FGHI", {"chain_id": "A"}))
    records = protein_records(values)
    assert records[0]["chain_id"] == "B"
    assert records[-2]["chain_id"] == "AB"
    assert records[-1]["chain_id"] == "A"


@pytest.mark.parametrize(
    "text, metadata, message",
    [
        (">A\n\n>B\nACDE", {}, "empty"),
        (">A\nACDE\n>A\nGGGG", {}, "unique"),
        (">bad\nACDE", {}, "uppercase"),
        ("AC-D", {}, "unsupported symbols"),
        (">A\nACDE\n>B\nGGGG", {"chain_ids": ["C"]}, "record count"),
        ("ACDE", {"chain_id": ""}, "uppercase"),
    ],
)
def test_protein_contract_rejects_invalid_records(text, metadata, message):
    with pytest.raises(ValueError, match=message):
        protein_records(ProteinSequence.inline(text, "inline", metadata))


@pytest.mark.parametrize(
    "text, message",
    [
        ("", "empty"),
        ("ACDE", "header"),
        (">A\nACDE\n>hit\nACD", "width"),
        (">A\nACDE\n>hit\nAC*E", "symbols"),
        (">A\nAAAA", "query sequence"),
    ],
)
def test_a3m_contract_rejects_incomplete_or_wrong_alignments(text, message):
    with pytest.raises(ValueError, match=message):
        validate_a3m(text, "ACDE")


def test_a3m_insertions_and_deletions_remain_intact():
    validate_a3m(">query\nACDE\n>hit\nACdD-\n", "ACDE")


def test_msa_query_headers_match_multiple_chains_after_staging(tmp_path):
    msas = [File.inline(">B\nGGGG\n", "a3m"), File.inline(">A\nACDE\n", "a3m")]
    method = AF3Prediction()
    inputs = method.format_inputs(
        {"proteins": ["ACDE", "GGGG"], "ligands": ["CCO"], "msa": msas},
        tmp_path / "stage",
    )
    resolved = resolve_msas(inputs["msa"], protein_records(inputs["proteins"]))
    assert set(resolved) == {"A", "B"}
    assert resolved["B"].path.read_text() == ">B\nGGGG\n"


def test_msa_file_stems_survive_staging(tmp_path):
    paths = [tmp_path / "A.a3m", tmp_path / "B.a3m"]
    for path, sequence in zip(paths, ("ACDE", "GGGG")):
        path.write_text(f">query\n{sequence}\n")
    method = AF3Prediction()
    inputs = method.format_inputs(
        {"proteins": ["ACDE", "GGGG"], "ligands": ["CCO"], "msa": paths},
        tmp_path / "stage",
    )
    assert method.validate_inputs(inputs) == (True, [])


def test_single_chain_uses_same_resolver_for_scalar_and_list():
    protein = ProteinSequence.from_string("ACDE")
    msa = File.inline(">query\nACDE\n", "a3m")
    assert resolve_msas(msa, protein_records(protein)) == {"A": msa}
    assert resolve_msas([msa], protein_records([protein])) == {"A": msa}


def test_protein_records_preserves_legacy_id_metadata_alias():
    protein = ProteinSequence.from_string("ACDE", {"id": "Q"})
    assert protein_records(protein)[0]["chain_id"] == "Q"


def test_boltz_csv_requires_matching_query_and_alignment_width():
    records = protein_records(ProteinSequence.from_string("ACDE"))
    msa = File.inline("key,sequence\n-1,ACDE\n1,ACdD-\n", "csv")
    assert resolve_msas([msa], records) == {"A": msa}
    with pytest.raises(ValueError, match="query sequence"):
        resolve_msas(File.inline("key,sequence\n-1,AAAA\n", "csv"), records)


def test_boltz_csv_accepts_query_after_hits_and_rejects_missing_rows():
    records = protein_records(ProteinSequence.from_string("ACDE"))
    reordered = File.inline("key,sequence\n1,ACdD-\n-1,ACDE\n", "csv")
    assert resolve_msas(reordered, records) == {"A": reordered}
    malformed = File.inline("key,sequence\n1,\n", "csv")
    with pytest.raises(ValueError, match="without key or sequence"):
        resolve_msas(malformed, records)
