"""Small, dependency-free structure checks used by prediction contracts."""

import math
import shlex
from pathlib import Path
from typing import List, Tuple


def structure_errors(path: Path, format: str, label: str) -> List[str]:
    """Require readable atom coordinates without adding a Gemmi dependency."""
    errors: List[str] = []
    try:
        text = path.read_text()
    except (OSError, UnicodeError) as exc:
        return [f"Could not read {label} '{path}': {exc}"]
    if not text.strip():
        return [f"{label} '{path}' is empty"]
    coordinates: List[Tuple[float, float, float]] = []
    try:
        if format in ("pdb", "pdbqt"):
            for line in text.splitlines():
                if line.startswith(("ATOM  ", "HETATM")):
                    coordinates.append(tuple(float(line[start:start + 8]) for start in (30, 38, 46)))
        elif format in ("cif", "mmcif"):
            tokens = shlex.split(text, comments=True)
            index = 0
            while index < len(tokens):
                if tokens[index].lower() != "loop_":
                    index += 1
                    continue
                index += 1
                columns = []
                while index < len(tokens) and tokens[index].startswith("_"):
                    columns.append(tokens[index])
                    index += 1
                positions = [columns.index(key) if key in columns else -1 for key in (
                    "_atom_site.Cartn_x", "_atom_site.Cartn_y", "_atom_site.Cartn_z",
                )]
                rows = []
                while index < len(tokens) and not tokens[index].startswith("_") and tokens[index].lower() not in ("loop_", "stop_") and not tokens[index].lower().startswith("data_"):
                    rows.append(tokens[index])
                    index += 1
                if columns and all(position >= 0 for position in positions):
                    if len(rows) % len(columns):
                        errors.append(f"{label} '{path}' contains an incomplete atom_site row")
                    for offset in range(0, len(rows) - len(columns) + 1, len(columns)):
                        coordinates.append(tuple(float(rows[offset + position]) for position in positions))
        else:
            errors.append(f"{label} '{path}' has unsupported format '{format}'")
    except (ValueError, IndexError) as exc:
        errors.append(f"Could not parse {label} '{path}' atom coordinates: {exc}")
    if not coordinates:
        errors.append(f"{label} '{path}' has no readable atom coordinates")
    elif any(not all(math.isfinite(number) for number in point) for point in coordinates):
        errors.append(f"{label} '{path}' contains non-finite atom coordinates")
    return errors
