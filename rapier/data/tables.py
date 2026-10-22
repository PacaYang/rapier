"""Tabular, score, report and generic file data types."""

import csv
import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from ..artifacts.model import Artifact, format_from_suffix
from .ids import molecule_id

# Direction of well-known score columns, used when a table does not declare one.
KNOWN_DIRECTIONS = {
    "affinity_kcal_mol": "lower", "minimizedAffinity": "lower", "score": "lower", "rmsd_lb": "lower",
    "rmsd_ub": "lower", "CNNscore": "higher", "CNNaffinity": "higher", "similarity": "higher",
    "confidence_score": "higher", "affinity_pred_value": "lower", "affinity_probability_binary": "higher",
    "centroid_distance": "lower", "reference_distance": "lower", "fraction_in_box": "higher",
    "filter_rank": "lower", "pose_rank": "lower", "pose": "lower",
}
# Key columns accepted when reading score tables written by older or external tools.
ID_COLUMNS = ("id", "ligand", "name", "molecule", "mol_id", "compound", "title")
POSE_COLUMNS = ("pose", "mode", "pose_rank", "model")


def _table_rows_with_errors(data: Any) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Keep independent JSON row objects and account for every malformed entry."""
    if isinstance(data, dict):
        if "rows" in data:
            data = data["rows"]
        elif "records" in data:
            data = data["records"]
        else:
            data = [data]
    if not isinstance(data, list):
        return [], ["Table payload must be a list of row objects"]
    invalid = [str(index + 1) for index, row in enumerate(data) if not isinstance(row, dict)]
    errors = ["Table rows must be objects; invalid row(s): " + ", ".join(invalid)] if invalid else []
    return [dict(row) for row in data if isinstance(row, dict)], errors


def _table_rows(data: Any) -> List[Dict[str, Any]]:
    """Read JSON rows without silently losing malformed records."""
    rows, errors = _table_rows_with_errors(data)
    if errors:
        raise ValueError("; ".join(errors))
    return rows


def load_table(path: Path, fmt: str) -> List[Dict[str, Any]]:
    text = Path(path).read_text()
    if fmt == "json":
        return _table_rows(json.loads(text))
    delimiter = "\t" if fmt == "tsv" else ","
    return [dict(row) for row in csv.DictReader(text.splitlines(), delimiter=delimiter)]


def write_rows(rows: Iterable[Dict[str, Any]], path: Path, fmt: str = "csv",
               columns: Optional[Sequence[str]] = None) -> Path:
    """Write rows as CSV/TSV (union of keys, first-seen order) or a JSON list."""
    rows = [dict(row) for row in rows]
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if fmt == "json":
        path.write_text(json.dumps(rows, indent=2, default=str) + "\n")
        return path
    names = list(columns or [])
    for row in rows:
        for key in row:
            if key not in names:
                names.append(key)
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=names, extrasaction="ignore",
                                delimiter="\t" if fmt == "tsv" else ",")
        writer.writeheader()
        for row in rows:
            writer.writerow({key: "" if value is None else value for key, value in row.items()})
    return path


class Table(Artifact):
    """Rows of named values (CSV, TSV, or a JSON list of objects)."""

    type_name = "table"
    formats = ("csv", "tsv", "json")

    def rows(self) -> List[Dict[str, Any]]:
        if self.is_file:
            return load_table(self.path, self.format)
        return _table_rows(self.value)

    def rows_with_errors(self) -> Tuple[List[Dict[str, Any]], List[str]]:
        """Read a pool for validation, retaining valid JSON siblings of bad rows.

        Errors include every malformed JSON row position (one-based), or the
        file/JSON parsing failure. Callers must include these errors in their
        validation result; :meth:`rows` remains the strict general reader.
        """
        try:
            if self.is_file:
                if self.format == "json":
                    return _table_rows_with_errors(json.loads(self.path.read_text()))
                return self.rows(), []
            return _table_rows_with_errors(self.value)
        except Exception as exc:
            return [], [str(exc)]

    @classmethod
    def from_rows(cls, rows: Iterable[Dict[str, Any]], path: Optional[Path] = None,
                  format: Optional[str] = None, columns: Optional[Sequence[str]] = None,
                  metadata: Optional[Dict[str, Any]] = None, type_name: Optional[str] = None) -> Artifact:
        """Write rows to ``path`` (format from its suffix, default CSV) or keep them inline."""
        rows = [dict(row) for row in rows]
        if path is None:
            return cls.inline(rows, "json", metadata, type_name=type_name)
        fmt = format or format_from_suffix(path) or "csv"
        write_rows(rows, path, fmt, columns)
        return cls.from_file(path, fmt, metadata, type_name=type_name)

    @classmethod
    def from_value(cls, value: Any, *, type_name: Optional[str] = None, formats: Sequence[str] = (),
                   directory: Optional[Path] = None, stem: str = "data") -> Artifact:
        rows = value.get("rows") if isinstance(value, dict) and "rows" in value else value
        if isinstance(rows, list) and all(isinstance(row, dict) for row in rows):
            if directory is None:
                return cls.from_rows(rows, type_name=type_name)
            fmt = next((item for item in (tuple(formats) or cls.formats) if item in ("csv", "tsv", "json")), "csv")
            return cls.from_rows(rows, Path(directory) / f"{stem}.{fmt}", fmt, type_name=type_name)
        return super().from_value(value, type_name=type_name, formats=formats, directory=directory, stem=stem)


class Scores(Table):
    """Scores keyed by molecule ``id`` (plus ``pose`` for per-pose rows).

    ``metadata["columns"]`` declares score semantics, e.g.
    ``{"affinity_kcal_mol": {"better": "lower", "unit": "kcal/mol"}}``; ranking
    and filtering blocks read it to know which direction is better.
    """

    type_name = "table.scores"

    @classmethod
    def from_rows(cls, rows: Iterable[Dict[str, Any]], path: Optional[Path] = None,
                  format: Optional[str] = None, columns: Optional[Sequence[str]] = None,
                  metadata: Optional[Dict[str, Any]] = None, type_name: Optional[str] = None) -> Artifact:
        rows = [dict(row) for row in rows]
        keys = [key for key in ("id", "pose") if any(key in row for row in rows)]
        return super().from_rows(rows, path, format, list(columns or keys), metadata, type_name)

    @classmethod
    def from_records(cls, records: Iterable[Dict[str, Any]], path: Optional[Path] = None,
                     directions: Optional[Dict[str, str]] = None, units: Optional[Dict[str, str]] = None,
                     format: Optional[str] = None, metadata: Optional[Dict[str, Any]] = None) -> Artifact:
        """Canonical score table plus per-column ``better``/``unit`` metadata."""
        meta = dict(metadata or {})
        specs: Dict[str, Dict[str, str]] = {key: dict(value) for key, value in (meta.get("columns") or {}).items()}
        for column, better in (directions or {}).items():
            specs.setdefault(column, {})["better"] = better
        for column, unit in (units or {}).items():
            specs.setdefault(column, {})["unit"] = unit
        if specs:
            meta["columns"] = specs
        return cls.from_rows(records, path, format, metadata=meta)

    def records(self, id_column: str = "") -> List[Dict[str, Any]]:
        """Rows with canonical ``id`` (sanitized) and integer ``pose`` keys added."""
        rows = self.rows()
        if not rows:
            return []
        columns = list(rows[0])
        key = next((name for name in ((id_column,) if id_column else ()) + ID_COLUMNS if name in columns), None)
        if key is None:
            raise ValueError(f"Score table has no id column (columns: {columns}); set id_column")
        pose_key = next((name for name in POSE_COLUMNS if name in columns), None)
        records = []
        for row in rows:
            record = dict(row, id=molecule_id(row.get(key)))
            if pose_key and str(row.get(pose_key, "")).strip().lstrip("-").isdigit():
                record["pose"] = int(row[pose_key])
            records.append(record)
        return records

    def directions(self, include_known: bool = True) -> Dict[str, str]:
        """``better`` direction per column: declared metadata over well-known names."""
        declared = {
            name: spec["better"] for name, spec in (self.metadata.get("columns") or {}).items()
            if isinstance(spec, dict) and spec.get("better") in ("lower", "higher")
        }
        return {**KNOWN_DIRECTIONS, **declared} if include_known else declared


class Report(Artifact):
    """Run summary or statistics (JSON, text, HTML or CSV)."""

    type_name = "report"
    formats = ("json", "text", "html", "csv")

    def load(self) -> Any:
        if not self.is_file:
            return self.value
        text = self.path.read_text()
        return json.loads(text) if self.format == "json" else text

    @classmethod
    def from_value(cls, value: Any, *, type_name: Optional[str] = None, formats: Sequence[str] = (),
                   directory: Optional[Path] = None, stem: str = "data") -> Artifact:
        if isinstance(value, (dict, list)) and directory is not None:
            path = Path(directory) / f"{stem}.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(value, indent=2, default=str) + "\n")
            return cls.from_file(path, "json", type_name=type_name)
        return super().from_value(value, type_name=type_name, formats=formats, directory=directory, stem=stem)


class File(Artifact):
    """Any file; ports of this type accept every file-backed artifact."""

    type_name = "file"
    formats = ("*",)
