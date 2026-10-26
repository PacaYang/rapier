"""Shared protein-chain and supplied-alignment contracts for predictors."""

import csv
import io
import re
from pathlib import Path

from ..data import ProteinSequence


def read_text(artifact):
    """Read either a file-backed or inline artifact without changing case."""
    return artifact.path.read_text() if artifact.is_file else str(artifact.value)


def chain_id(index):
    """Deterministic uppercase chain labels: A, B, ..., Z, AA, AB, ... ."""
    result = ""
    while index >= 0:
        result = chr(ord("A") + index % 26) + result
        index = index // 26 - 1
    return result


def _records(text):
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        raise ValueError("protein sequence cannot be empty")
    if not lines[0].startswith(">"):
        if any(line.startswith(">") for line in lines):
            raise ValueError("protein FASTA must start with a header")
        return [(None, "".join(lines))]
    records, header, residues = [], None, []
    for line in lines:
        if line.startswith(">"):
            if header is not None:
                records.append((header, "".join(residues)))
            header, residues = line[1:].strip(), []
            if not header:
                raise ValueError("protein FASTA contains an empty header")
        else:
            residues.append(line)
    records.append((header, "".join(residues)))
    return records


def protein_records(values):
    """Flatten sequence artifacts/FASTA into distinct, validated protein chains.

    Metadata chain IDs take precedence over the first token in FASTA headers.
    Bare sequences receive stable uppercase IDs, skipping explicitly named IDs.
    For a single sequence, ``chain_ids`` requests copies of the same protein.
    For a multi-record FASTA, it assigns one ID to each record.
    """
    artifacts = values if isinstance(values, (list, tuple)) else [values]
    pending = []
    for artifact in artifacts:
        if artifact is None:
            continue
        records = _records(read_text(artifact))
        record_count = len(records)
        if artifact.format == "fasta" and records[0][0] is None:
            raise ValueError("protein FASTA must start with a header")
        metadata = artifact.metadata
        ids = metadata.get("chain_id", metadata.get("chain_ids", metadata.get("id")))
        if isinstance(ids, (list, tuple)):
            if not ids:
                raise ValueError("protein chain IDs cannot be empty")
            if len(records) == 1:
                records = records * len(ids)
            elif len(ids) != len(records):
                raise ValueError("protein chain IDs must match the FASTA record count")
        elif ids is not None and len(records) != 1:
            raise ValueError(
                "use chain_ids to name every record of a multi-record FASTA"
            )
        for index, (header, sequence) in enumerate(records):
            sequence = re.sub(r"\s+", "", sequence).upper()
            if not sequence:
                raise ValueError("protein sequence cannot be empty")
            invalid = sorted(
                set(sequence) - set(ProteinSequence.alphabet.replace("*", ""))
            )
            if invalid:
                raise ValueError(
                    "protein sequence has unsupported symbols: " + ", ".join(invalid)
                )
            requested = ids[index] if isinstance(ids, (list, tuple)) else ids
            if requested is None and header:
                requested = header.split()[0]
            if requested is not None and (
                not isinstance(requested, str) or not re.fullmatch(r"[A-Z]+", requested)
            ):
                raise ValueError(
                    "protein chain IDs must be nonempty uppercase letter strings"
                )
            pending.append(
                {
                    "chain_id": requested,
                    "sequence": sequence,
                    "source_artifact_id": artifact.artifact_id,
                    "record_index": 0 if record_count == 1 else index,
                }
            )
    if not pending:
        raise ValueError("at least one protein sequence is required")
    explicit = [
        record["chain_id"] for record in pending if record["chain_id"] is not None
    ]
    if len(explicit) != len(set(explicit)):
        raise ValueError("protein chain IDs must be unique")
    used, index = set(explicit), 0
    for record in pending:
        if record["chain_id"] is None:
            while chain_id(index) in used:
                index += 1
            record["chain_id"] = chain_id(index)
            used.add(record["chain_id"])
            index += 1
    return pending


def _a3m_records(text):
    records, header, residues = [], None, []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith(">"):
            if header is not None:
                records.append((header, "".join(residues)))
            header, residues = line[1:].strip(), []
            if not header:
                raise ValueError("A3M has an empty header")
        elif header is None:
            raise ValueError("A3M must start with a FASTA header")
        else:
            residues.append(line)
    if header is not None:
        records.append((header, "".join(residues)))
    if not records or any(not sequence for _, sequence in records):
        raise ValueError("A3M contains an empty alignment or record")
    return records


def validate_a3m(text, sequence):
    """Require the exact query sequence and consistent A3M aligned widths."""
    records = _a3m_records(text)
    if records[0][1].upper() != sequence.upper():
        raise ValueError("MSA query sequence does not match the protein sequence")
    for _, row in records:
        if not re.fullmatch(r"[A-Za-z.\-]+", row):
            raise ValueError("A3M contains invalid alignment symbols")
        aligned = re.sub(r"[a-z.]", "", row)
        if len(aligned) != len(sequence):
            raise ValueError("A3M alignment width does not match the protein sequence")


def _validate_csv(text, sequence):
    rows = list(csv.DictReader(io.StringIO(text)))
    if not rows or "sequence" not in rows[0] or "key" not in rows[0]:
        raise ValueError(
            "Boltz MSA CSV requires key and sequence columns and a query row"
        )
    if any(
        not isinstance(row.get("key"), str)
        or not isinstance(row.get("sequence"), str)
        or not row["sequence"].strip()
        for row in rows
    ):
        raise ValueError("Boltz MSA CSV contains a row without key or sequence")
    query_index = next(
        (
            index
            for index, row in enumerate(rows)
            if row["sequence"].upper() == sequence.upper()
        ),
        None,
    )
    if query_index is None:
        raise ValueError("Boltz MSA CSV does not contain a query sequence row")
    rows = [rows[query_index]] + rows[:query_index] + rows[query_index + 1 :]
    validate_a3m(
        "".join(f">{index}\n{row['sequence']}\n" for index, row in enumerate(rows)),
        sequence,
    )


def resolve_msas(values, records):
    """Map one supplied MSA to every protein chain, with no missing/extra IDs.

    Prefer ``metadata.chain_id``; otherwise use an A3M query header or file
    stem matching a chain ID. An untagged MSA for one chain is unambiguous.
    """
    artifacts = values if isinstance(values, (list, tuple)) else [values]
    expected = {record["chain_id"]: record["sequence"] for record in records}
    resolved = {}
    for artifact in artifacts:
        text = read_text(artifact)
        if artifact.format not in ("a3m", "csv"):
            raise ValueError("MSA must have a3m or csv format")
        requested = artifact.metadata.get("chain_id")
        if requested is None and artifact.format == "a3m":
            header = _a3m_records(text)[0][0].split()[0]
            if header in expected:
                requested = header
        if (
            requested is None
            and artifact.is_file
            and Path(artifact.metadata.get("staged_from", artifact.path)).stem
            in expected
        ):
            requested = Path(artifact.metadata.get("staged_from", artifact.path)).stem
        if requested is None and len(expected) == 1:
            requested = next(iter(expected))
        if not isinstance(requested, str) or requested not in expected:
            raise ValueError(
                f"MSA chain_id {requested!r} does not match a protein chain; "
                "tag every MSA with chain_id"
            )
        if requested in resolved:
            raise ValueError(f"Duplicate MSA for protein chain {requested}")
        if artifact.format == "a3m":
            validate_a3m(text, expected[requested])
        else:
            _validate_csv(text, expected[requested])
        resolved[requested] = artifact
    missing = set(expected) - set(resolved)
    if missing:
        raise ValueError(
            "MSA input must cover every protein chain; missing: "
            + ", ".join(sorted(missing))
        )
    return resolved
