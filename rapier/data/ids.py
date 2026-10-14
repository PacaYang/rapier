"""Molecule identifiers shared by every reader and writer."""

import re
from typing import Any

_UNSAFE = re.compile(r"[^A-Za-z0-9_.-]+")


def molecule_id(name: Any, fallback: str = "") -> str:
    """Stable, filename-safe id for a molecule name (``"lig b"`` -> ``"lig_b"``).

    Molecule files and score tables are joined on this id, so every method
    that names molecules (docking runners, filters, scorers) derives ids here.
    """
    normalized = _UNSAFE.sub("_", str(name or "").strip()).strip("._")
    return normalized or fallback
