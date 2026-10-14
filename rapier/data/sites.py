"""Binding-site data type."""

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from ..artifacts.model import Artifact, ArtifactStorage


def load_box(site: Any) -> Optional[Dict[str, List[float]]]:
    """``{center, size}`` from a box artifact, a JSON file, a dict or ``None``."""
    if site is None:
        return None
    value = site
    if isinstance(site, Artifact):
        value = site.value if site.storage == ArtifactStorage.INLINE else json.loads(site.path.read_text())
    elif isinstance(site, (str, Path)):
        value = json.loads(Path(site).read_text())
    if isinstance(value, dict) and "box" in value:
        value = value["box"]
    if not (isinstance(value, dict) and len(value.get("center") or ()) == 3 and len(value.get("size") or ()) == 3):
        raise ValueError("binding_site must be a box with three-element 'center' and 'size'")
    return {"center": [float(v) for v in value["center"]], "size": [float(v) for v in value["size"]]}


class BindingSite(Artifact):
    """Binding site as a search box, a residue list, a reference ligand or JSON."""

    type_name = "binding_site"
    formats = ("box", "residues", "reference", "json")

    @classmethod
    def check_value(cls, artifact: Artifact) -> List[str]:
        if artifact.storage != ArtifactStorage.INLINE:
            return []
        value = artifact.value
        if artifact.format == "box":
            if not isinstance(value, dict):
                return ["Binding-site box must be an object with center and size"]
            center, size = value.get("center"), value.get("size")
            if not isinstance(center, (list, tuple)) or len(center) != 3:
                return ["Binding-site box center must contain three coordinates"]
            if not isinstance(size, (list, tuple)) or len(size) != 3:
                return ["Binding-site box size must contain three dimensions"]
            numbers = list(center) + list(size)
            if not all(isinstance(item, (int, float)) and not isinstance(item, bool) for item in numbers):
                return ["Binding-site box center and size must be numeric"]
            if any(item <= 0 for item in size):
                return ["Binding-site box dimensions must be positive"]
        elif artifact.format == "residues" and (not isinstance(value, list) or not value):
            return ["Residue binding site must be a non-empty list"]
        return []

    @classmethod
    def from_box(cls, center: Sequence[float], size: Sequence[float],
                 metadata: Optional[Dict[str, Any]] = None) -> "BindingSite":
        box = {"center": [float(v) for v in center], "size": [float(v) for v in size]}
        return cls.inline(box, "box", metadata)

    @classmethod
    def from_value(cls, value: Any, *, type_name: Optional[str] = None, **kwargs: Any) -> Artifact:
        if isinstance(value, dict) and "center" in value and "size" in value:
            return cls.inline({"center": list(value["center"]), "size": list(value["size"])}, "box", type_name=type_name)
        if isinstance(value, list):
            return cls.inline(value, "residues", type_name=type_name)
        return super().from_value(value, type_name=type_name, **kwargs)

    def box(self) -> Dict[str, List[float]]:
        """The site as ``{center, size}``; raises for non-box representations."""
        if self.format not in ("box", "json"):
            raise ValueError(f"binding_site/{self.format} has no box; provide a box or JSON box")
        return load_box(self)
