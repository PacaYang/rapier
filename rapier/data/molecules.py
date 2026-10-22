"""Small-molecule data types and RDKit-free SDF/SMILES parsing."""

import errno
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from ..artifacts.model import Artifact
from .ids import molecule_id

_MOLECULE_SUFFIXES = (
    ".sdf",
    ".sd",
    ".mol",
    ".mol2",
    ".smi",
    ".smiles",
    ".pdbqt",
    ".txt",
)


@dataclass(frozen=True)
class MoleculeRecord:
    """One molecule (or pose) of a molecule artifact.

    ``id`` is the sanitized join key (see :func:`molecule_id`), ``name`` the
    original title, ``pose`` the 1-based rank among records sharing the id.
    """

    id: str
    name: str
    index: int
    pose: int = 1
    smiles: Optional[str] = None
    block: Optional[str] = None
    properties: Dict[str, str] = field(default_factory=dict)
    atoms: List[Tuple[str, float, float, float]] = field(default_factory=list)

    @property
    def heavy_atoms(self) -> List[Tuple[float, float, float]]:
        return [
            (x, y, z)
            for symbol, x, y, z in self.atoms
            if symbol.upper() not in ("H", "D")
        ]


def split_sdf(text: str) -> List[str]:
    """SDF records without their ``$$$$`` terminator (verbatim otherwise)."""
    records = []
    # Only trailing whitespace is trimmed: the first line is the (possibly blank) title.
    for raw in re.split(r"^\$\$\$\$[^\n]*\n?", text, flags=re.MULTILINE):
        if raw.strip():
            records.append(raw.rstrip() + "\n")
    return records


def parse_sdf_record(record: str) -> Dict[str, Any]:
    """Title, atoms (symbol, x, y, z), heavy-atom coordinates and data properties."""
    lines = record.splitlines()
    title = lines[0].strip() if lines else ""
    atoms: List[Tuple[str, float, float, float]] = []
    try:
        if len(lines) > 3 and "V3000" in lines[3]:
            in_atoms = False
            for line in lines[4:]:
                body = line[6:].strip() if line.startswith("M  V30") else ""
                if body.startswith("BEGIN ATOM"):
                    in_atoms = True
                elif body.startswith("END ATOM"):
                    break
                elif in_atoms and body:
                    parts = body.split()
                    atoms.append(
                        (parts[1], float(parts[2]), float(parts[3]), float(parts[4]))
                    )
        elif len(lines) > 3:
            count = int(lines[3][:3])
            for line in lines[4 : 4 + count]:
                try:
                    atoms.append(
                        (
                            line[31:34].strip(),
                            float(line[0:10]),
                            float(line[10:20]),
                            float(line[20:30]),
                        )
                    )
                except (ValueError, IndexError):
                    parts = line.split()
                    atoms.append(
                        (parts[3], float(parts[0]), float(parts[1]), float(parts[2]))
                    )
    except (ValueError, IndexError):
        atoms = []
    properties: Dict[str, str] = {}
    start = next(
        (i + 1 for i, line in enumerate(lines) if line.startswith("M  END")), len(lines)
    )
    current: Optional[str] = None
    buffer: List[str] = []
    for line in lines[start:] + [""]:
        match = re.match(r"^>.*<([^>]+)>", line)
        if match:
            current, buffer = match.group(1), []
        elif current is not None and line.strip() == "":
            properties[current] = "\n".join(buffer).strip()
            current = None
        elif current is not None:
            buffer.append(line)
    heavy = [(x, y, z) for symbol, x, y, z in atoms if symbol.upper() not in ("H", "D")]
    return {"title": title, "atoms": atoms, "heavy": heavy, "properties": properties}


def parse_smiles_lines(text: str) -> List[Tuple[str, str]]:
    """``(smiles, name)`` pairs from ``SMILES [name]`` lines."""
    pairs = []
    for line in text.splitlines():
        parts = line.split(None, 1)
        if parts and not parts[0].startswith("#"):
            pairs.append((parts[0], parts[1].strip() if len(parts) > 1 else ""))
    return pairs


def sdf_terminated(block: str) -> str:
    """An SDF record with exactly one ``$$$$`` terminator, ready to concatenate."""
    return (
        block.rstrip("\n")
        + "\n"
        + ("\n" if not block.rstrip().endswith("M  END") else "")
        + "$$$$\n"
    )


class _MoleculeData(Artifact):
    """Shared readers for single molecules and collections."""

    collection = True

    def records(self, name_property: str = "") -> List[MoleculeRecord]:
        """Read molecules/poses with IDs, SMILES/SDF blocks, properties and atoms."""
        if self.format == "smiles":
            if self.is_file:
                pairs = parse_smiles_lines(self.path.read_text())
            else:
                values = self.value if isinstance(self.value, list) else [self.value]
                pairs = [
                    pair for item in values for pair in parse_smiles_lines(str(item))
                ]
            return self._number(
                (pair[1] or f"mol_{index + 1}", index, {"smiles": pair[0]})
                for index, pair in enumerate(pairs)
            )
        if self.format == "sdf":
            text = self.path.read_text() if self.is_file else str(self.value)
            rows = []
            for index, block in enumerate(split_sdf(text)):
                parsed = parse_sdf_record(block)
                name = (
                    parsed["properties"].get(name_property) if name_property else None
                ) or parsed["title"]
                rows.append(
                    (
                        name or f"mol_{index + 1}",
                        index,
                        {
                            "block": block,
                            "properties": parsed["properties"],
                            "atoms": parsed["atoms"],
                        },
                    )
                )
            return self._number(rows)
        raise ValueError(
            f"Cannot read {self.type}/{self.format} records "
            "without conversion to sdf or smiles"
        )

    @staticmethod
    def _number(
        rows: Iterable[Tuple[str, int, Dict[str, Any]]],
    ) -> List[MoleculeRecord]:
        seen: Dict[str, int] = {}
        records = []
        for name, index, fields in rows:
            identifier = molecule_id(name, f"mol_{index + 1}")
            seen[identifier] = seen.get(identifier, 0) + 1
            records.append(
                MoleculeRecord(identifier, name, index, seen[identifier], **fields)
            )
        return records

    def ids(self) -> List[str]:
        """Unique molecule ids in file order."""
        return list(dict.fromkeys(record.id for record in self.records()))

    @classmethod
    def from_smiles(
        cls,
        smiles: Sequence[str],
        names: Optional[Sequence[str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> "Artifact":
        names = list(names or [])
        lines = [
            f"{s} {names[i]}".strip() if i < len(names) else s
            for i, s in enumerate(smiles)
        ]
        value = lines if cls.collection else lines[0]
        return cls.inline(value, "smiles", metadata)

    @classmethod
    def write_sdf(
        cls,
        blocks: Iterable[str],
        path: Path,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> "Artifact":
        """Write SDF blocks (with or without terminators) and wrap the file."""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "".join(
                sdf_terminated(re.sub(r"\$\$\$\$\s*$", "", block)) for block in blocks
            )
        )
        return cls.from_file(path, "sdf", metadata)

    @classmethod
    def coerce(cls, value: Any, **kwargs: Any) -> Artifact:
        # Inline SMILES ("CCO ethanol" or a list of them) versus a molecule file path.
        is_path = isinstance(value, Path)
        if isinstance(value, str):
            is_path = value.lower().endswith(_MOLECULE_SUFFIXES)
            if not is_path:
                try:
                    is_path = Path(value).is_file()
                except OSError as exc:
                    if exc.errno != errno.ENAMETOOLONG:
                        raise
        if isinstance(value, str) and not is_path:
            return cls.inline(
                [value] if cls.collection else value,
                "smiles",
                type_name=kwargs.get("type_name"),
            )
        if (
            isinstance(value, (list, tuple))
            and value
            and all(isinstance(item, str) for item in value)
        ):
            return cls.inline(
                list(value) if cls.collection else value[0],
                "smiles",
                type_name=kwargs.get("type_name"),
            )
        return super().coerce(value, **kwargs)


class Molecule(_MoleculeData):
    """A single small molecule (SMILES, SDF, MOL2, PDBQT or a CCD code)."""

    type_name = "molecule"
    formats = ("smiles", "sdf", "mol2", "pdbqt", "ccd")
    collection = False


class Molecules(_MoleculeData):
    """A collection of small molecules; SMILES and SDF interconvert automatically."""

    type_name = "molecule.collection"
    formats = ("smiles", "sdf", "mol2", "pdbqt", "ccd")


class Poses(Molecules):
    """Docked/predicted poses; a shared ID identifies ranked poses of a molecule."""

    type_name = "molecule.poses"
    formats = ("sdf",)
