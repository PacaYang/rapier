"""Filter docked or predicted molecules by scores and binding position.

Unlike :mod:`ligand_filter`, which applies physicochemical rules with RDKit,
this method works on *results*: it joins a score table from an upstream method
(e.g. Vina ``docking_scores``) onto the poses, computes simple geometric
metrics against a binding-site box or reference ligand, and keeps poses that
satisfy user-defined criteria. It is pure Python and needs no RDKit.
"""

import csv
import json
import math
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from ...artifacts import Artifact, ArtifactStorage
from ...core.enums import TaskStatus
from ...core.specs import ConfigField, ConfigSpec, InputSpec, OutputSpec, PortSpec, ResourceSpec
from ...core.task import Task
from ..base_implementations import SingleStepMethod


OPERATORS = ("<", "<=", ">", ">=", "==", "!=", "between", "in", "not_in", "exists")
NUMERIC_OPERATORS = ("<", "<=", ">", ">=", "between")
POSITION_MODES = ("none", "centroid_in_box", "all_atoms_in_box", "fraction_in_box")
# Computed per-pose metrics and the input port each one needs.
METRICS = {
    "heavy_atoms": None,
    "pose_rank": None,
    "centroid_distance": "binding_site",
    "fraction_in_box": "binding_site",
    "centroid_in_box": "binding_site",
    "reference_distance": "reference_ligand",
}
COMMON_SCORE_COLUMNS = (
    "affinity_kcal_mol", "score", "minimizedAffinity", "CNNscore", "CNNaffinity",
    "rmsd_lb", "rmsd_ub", "mode", "similarity", "affinity_pred_value", "affinity_probability_binary",
)
_SAFE_NAME = re.compile(r"[^A-Za-z0-9_.-]+")  # matches the Vina runner's ligand naming


def _validate_criteria(criteria: Any) -> Optional[str]:
    if not isinstance(criteria, list):
        return "must be a list of {column, op, value} rules"
    for index, rule in enumerate(criteria):
        where = f"rule {index + 1}"
        if not isinstance(rule, dict):
            return f"{where} must be a mapping"
        unknown = set(rule) - {"column", "op", "value"}
        if unknown:
            return f"{where} has unknown key(s) {sorted(unknown)}"
        if not isinstance(rule.get("column"), str) or not rule["column"].strip():
            return f"{where} needs a non-empty 'column'"
        op = rule.get("op")
        if op not in OPERATORS:
            return f"{where} has invalid op {op!r}; use one of {list(OPERATORS)}"
        value = rule.get("value")
        if op == "exists":
            continue
        if op == "between":
            if not (isinstance(value, (list, tuple)) and len(value) == 2 and all(_is_number(v) for v in value)):
                return f"{where} 'between' needs [low, high] numbers"
            if value[0] > value[1]:
                return f"{where} 'between' low must be <= high"
        elif op in ("in", "not_in"):
            if not isinstance(value, (list, tuple)) or not value:
                return f"{where} '{op}' needs a non-empty list"
        elif op in NUMERIC_OPERATORS and not _is_number(value):
            return f"{where} '{op}' needs a numeric value"
        elif value is None:
            return f"{where} needs a 'value'"
    return None


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _to_number(value: Any) -> Optional[float]:
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, (int, float)):
        return float(value)
    try:
        number = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def rule_label(rule: Dict[str, Any]) -> str:
    op, value = rule["op"], rule.get("value")
    if op == "exists":
        return f"{rule['column']} exists"
    if op == "between":
        return f"{value[0]} <= {rule['column']} <= {value[1]}"
    return f"{rule['column']} {op} {value}"
def evaluate_rule(rule: Dict[str, Any], value: Any) -> Optional[bool]:
    """True/False for a present value; None when the value is missing or not comparable."""
    op = rule["op"]
    if op == "exists":
        return value is not None and value != ""
    if value is None or value == "":
        return None
    target = rule.get("value")
    if op in ("==", "!="):
        a, b = _to_number(value), _to_number(target)
        equal = a == b if a is not None and b is not None else str(value) == str(target)
        return equal if op == "==" else not equal
    if op in ("in", "not_in"):
        number = _to_number(value)
        members = {str(item) for item in target}
        hit = str(value) in members or (number is not None and any(_to_number(item) == number for item in target))
        return hit if op == "in" else not hit
    number = _to_number(value)
    if number is None:
        return None
    if op == "between":
        return float(target[0]) <= number <= float(target[1])
    return {"<": number < target, "<=": number <= target, ">": number > target, ">=": number >= target}[op]


# ---- parsing ---------------------------------------------------------------

def split_sdf(text: str) -> List[str]:
    """SDF records without their ``$$$$`` terminator (verbatim otherwise)."""
    records = []
    # Only trailing whitespace is trimmed: the first line is the (possibly blank) title.
    for raw in re.split(r"^\$\$\$\$[^\n]*\n?", text, flags=re.MULTILINE):
        if raw.strip():
            records.append(raw.rstrip() + "\n")
    return records


def parse_sdf_record(record: str) -> Dict[str, Any]:
    """Title, heavy-atom coordinates and data properties of one SDF record."""
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
                    atoms.append((parts[1], float(parts[2]), float(parts[3]), float(parts[4])))
        elif len(lines) > 3:
            count = int(lines[3][:3])
            for line in lines[4:4 + count]:
                try:
                    atoms.append((line[31:34].strip(), float(line[0:10]), float(line[10:20]), float(line[20:30])))
                except (ValueError, IndexError):
                    parts = line.split()
                    atoms.append((parts[3], float(parts[0]), float(parts[1]), float(parts[2])))
    except (ValueError, IndexError):
        atoms = []
    properties: Dict[str, str] = {}
    try:
        start = next(i for i, line in enumerate(lines) if line.startswith("M  END")) + 1
    except StopIteration:
        start = len(lines)
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
    return {"title": title, "heavy": heavy, "properties": properties}


def load_table(path: Path, fmt: str) -> List[Dict[str, Any]]:
    text = Path(path).read_text()
    if fmt == "json":
        data = json.loads(text)
        if isinstance(data, dict):
            data = data.get("rows") or data.get("records") or [data]
        return [dict(row) for row in data if isinstance(row, dict)]
    delimiter = "\t" if fmt == "tsv" else ","
    return [dict(row) for row in csv.DictReader(text.splitlines(), delimiter=delimiter)]


def load_box(site: Any) -> Optional[Dict[str, List[float]]]:
    """``{center, size}`` from an inline box artifact or a JSON file."""
    if site is None:
        return None
    value = site
    if isinstance(site, Artifact):
        value = site.value if site.storage == ArtifactStorage.INLINE else json.loads(site.path.read_text())
    elif isinstance(site, (str, Path)):
        value = json.loads(Path(site).read_text())
    if isinstance(value, dict) and "box" in value:
        value = value["box"]
    if not (isinstance(value, dict) and len(value.get("center", ())) == 3 and len(value.get("size", ())) == 3):
        raise ValueError("binding_site must be a box with three-element 'center' and 'size'")
    return {"center": [float(v) for v in value["center"]], "size": [float(v) for v in value["size"]]}


def _path(item: Any) -> Path:
    return item.path if isinstance(item, Artifact) else Path(item)


def _format(item: Any, default: str) -> str:
    if isinstance(item, Artifact):
        return item.format
    suffix = Path(item).suffix.lower().lstrip(".")
    return suffix if suffix in ("csv", "tsv", "json") else default


def _as_list(value: Any) -> List[Any]:
    if value is None:
        return []
    return list(value) if isinstance(value, (list, tuple)) else [value]


def _centroid(points: List[Tuple[float, float, float]]) -> Optional[Tuple[float, float, float]]:
    if not points:
        return None
    n = len(points)
    return tuple(sum(p[i] for p in points) / n for i in range(3))


def _inside(point: Iterable[float], box: Dict[str, List[float]], padding: float) -> bool:
    return all(abs(p - c) <= s / 2 + padding for p, c, s in zip(point, box["center"], box["size"]))


def _format_value(value: Any) -> str:
    if isinstance(value, float):
        return f"{value:.4g}" if abs(value) < 1e5 else f"{value:.6g}"
    return "" if value is None else str(value)
def _normalize_name(name: Any) -> str:
    return _SAFE_NAME.sub("_", str(name or "").strip()).strip("._")


def _key_column(rows: List[Dict[str, Any]], preferred: str) -> Optional[str]:
    columns = list(rows[0]) if rows else []
    for candidate in (preferred, "ligand", "name", "id", "molecule", "title"):
        if candidate in columns:
            return candidate
    return None


def _join_scores(poses: List[Dict[str, Any]], rows: List[Dict[str, Any]], id_column: str) -> int:
    """Attach table rows to poses in place; returns the number of poses left unmatched.

    Rows are grouped by molecule name. A single row per name applies to every
    pose of that molecule; several rows (e.g. Vina modes) pair with poses in
    order, sorted by a ``mode`` column when present.
    """
    if not rows:
        return 0
    key = _key_column(rows, id_column)
    if key is None:
        if len(rows) != len(poses):
            raise ValueError(
                f"Score table has no '{id_column}' column (columns: {list(rows[0])}) "
                f"and {len(rows)} rows for {len(poses)} poses; set id_column"
            )
        for pose, row in zip(poses, rows):
            pose["scores"].update(row)
        return 0
    groups: Dict[str, List[Dict[str, Any]]] = {}
    for row in rows:
        groups.setdefault(_normalize_name(row.get(key)), []).append(row)
    for group in groups.values():
        if all(_to_number(row.get("mode")) is not None for row in group):
            group.sort(key=lambda row: _to_number(row["mode"]))
    unmatched = 0
    for pose in poses:
        group = groups.get(_normalize_name(pose["name"]))
        if not group:
            unmatched += 1
            continue
        row = group[0] if len(group) == 1 else (group[pose["pose_rank"] - 1] if pose["pose_rank"] <= len(group) else None)
        if row is None:
            unmatched += 1
        else:
            pose["scores"].update(row)
    return unmatched


def lookup(pose: Dict[str, Any], column: str) -> Any:
    for source in (pose["metrics"], pose["scores"], pose["properties"]):
        if column in source:
            return source[column]
    lowered = column.lower()
    for source in (pose["metrics"], pose["scores"], pose["properties"]):
        for name, value in source.items():
            if name.lower() == lowered:
                return value
    return None


def filter_poses(
    sdf_text: str,
    tables: List[List[Dict[str, Any]]],
    config: Dict[str, Any],
    box: Optional[Dict[str, List[float]]] = None,
    reference: Optional[Tuple[float, float, float]] = None,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Core filtering logic, independent of tasks and artifacts.

    Returns ``(kept_poses, report)``; kept poses are ordered by rank.
    """
    poses: List[Dict[str, Any]] = []
    seen: Dict[str, int] = {}
    for index, record in enumerate(split_sdf(sdf_text)):
        parsed = parse_sdf_record(record)
        name = parsed["properties"].get(config["name_property"]) if config["name_property"] else None
        name = name or parsed["title"] or f"mol_{index + 1}"
        seen[name] = seen.get(name, 0) + 1
        poses.append({"index": index, "name": name, "pose_rank": seen[name], "record": record,
                      "heavy": parsed["heavy"], "properties": parsed["properties"], "scores": {}, "metrics": {}})

    unmatched = 0
    for rows in tables:
        unmatched = max(unmatched, _join_scores(poses, rows, config["id_column"]))

    padding = config["box_padding"]
    for pose in poses:
        heavy = pose["heavy"]
        metrics = pose["metrics"]
        metrics["heavy_atoms"] = len(heavy)
        metrics["pose_rank"] = pose["pose_rank"]
        centroid = _centroid(heavy)
        if box is not None and centroid is not None:
            metrics["centroid_distance"] = math.dist(centroid, box["center"])
            metrics["fraction_in_box"] = sum(_inside(p, box, padding) for p in heavy) / len(heavy)
            metrics["centroid_in_box"] = _inside(centroid, box, padding)
        if reference is not None and centroid is not None:
            metrics["reference_distance"] = math.dist(centroid, reference)

    rules = config["criteria"]
    labels = [rule_label(rule) for rule in rules]
    mode = config["position_mode"]
    position_label = {
        "centroid_in_box": "centroid in binding site",
        "all_atoms_in_box": "all heavy atoms in binding site",
        "fraction_in_box": f"fraction_in_box >= {config['min_fraction_in_box']}",
    }.get(mode)
    rejected_by: Dict[str, int] = {}
    passed: List[Dict[str, Any]] = []
    for pose in poses:
        failures: List[str] = []  # position check always applies; rules combine via all/any
        if position_label:
            fraction = pose["metrics"].get("fraction_in_box")
            ok = {
                "centroid_in_box": pose["metrics"].get("centroid_in_box"),
                "all_atoms_in_box": None if fraction is None else fraction >= 1.0,
                "fraction_in_box": None if fraction is None else fraction >= config["min_fraction_in_box"],
            }[mode]
            if not ok:
                failures.append(position_label if ok is not None else "no coordinates")
        rule_failures = []
        for rule, label in zip(rules, labels):
            result = evaluate_rule(rule, lookup(pose, rule["column"]))
            if result is None:
                result = config["on_missing"] == "pass"
                label = f"{rule['column']} missing"
            if not result:
                rule_failures.append(label)
        if config["combine"] == "all" or len(rule_failures) == len(rules):
            failures.extend(rule_failures)
        pose["failures"] = failures
        for failure in failures:
            rejected_by[failure] = rejected_by.get(failure, 0) + 1
        if not failures:
            passed.append(pose)

    selected, gate = _gate(poses, passed, config)
    for rank, pose in enumerate(selected, start=1):
        pose["metrics"]["filter_rank"] = rank

    present = set()
    for pose in poses:
        present.update(pose["metrics"], pose["scores"], pose["properties"])
    present_lower = {name.lower() for name in present}
    report = {
        "gate": gate,
        "total_poses": len(poses),
        "total_molecules": len({p["name"] for p in poses}),
        "passed_criteria": len(passed),
        "passed_molecules": len({p["name"] for p in passed}),
        "kept": len(selected),
        "kept_molecules": len({p["name"] for p in selected}),
        "rejected_by": rejected_by,
        "poses_without_scores": unmatched,
        "poses_without_coordinates": sum(1 for p in poses if not p["heavy"]),
        "unknown_columns": sorted({r["column"] for r in rules if r["column"].lower() not in present_lower}),
        "criteria": labels,
        "position_mode": mode,
        "binding_site": box,
    }
    return selected, report


def _gate(poses: List[Dict[str, Any]], passed: List[Dict[str, Any]], config: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Rank survivors and admit at most top_n / top_percent candidates to the next round.

    A candidate is a molecule (all of its poses) when ``limit_by == "molecules"``,
    otherwise a single pose. Molecules are ranked by their best pose. The percent
    cap is taken of the candidates that *entered* the filter by default, so
    ``top_percent: 1`` on 10,000 molecules admits at most 100, however many
    survive the criteria.
    """
    rank_by = config["rank_by"]
    sign = 1 if config["ascending"] else -1

    def rank_key(pose):
        value = _to_number(lookup(pose, rank_by)) if rank_by else None
        return (value is None, sign * value if value is not None else 0, pose["pose_rank"], pose["index"])

    ranked = sorted(passed, key=rank_key)
    by_molecule = config["limit_by"] == "molecules"
    if not by_molecule and config["best_pose_only"]:
        # Drop lower-ranked duplicate poses before the cap so they don't use up slots.
        seen = set()
        ranked = [p for p in ranked if not (p["name"] in seen or seen.add(p["name"]))]
    # Candidate groups in rank order; each group's first pose is its best.
    groups: Dict[Any, List[Dict[str, Any]]] = {}
    for pose in ranked:
        groups.setdefault(pose["name"] if by_molecule else pose["index"], []).append(pose)

    entered = len({p["name"] for p in poses}) if by_molecule else len(poses)
    basis = entered if config["percent_of"] == "input" else len(groups)
    limits = []
    if config["top_n"]:
        limits.append(config["top_n"])
    if config["top_percent"]:
        # round() first so 1% of 10,000 is exactly 100, not 101 from float error.
        limits.append(math.ceil(round(basis * config["top_percent"] / 100.0, 9)))
    limit = min(limits) if limits else None
    admitted = list(groups.values())[:limit]

    selected: List[Dict[str, Any]] = []
    for group in admitted:
        selected.extend(group[:1] if config["best_pose_only"] else group)

    unit = "molecules" if by_molecule else "poses"
    gate = {
        "unit": unit,
        "entered": entered,
        "passed_criteria": len(groups),
        "limit": limit,
        "admitted": len(admitted),
        "admitted_percent": round(100.0 * len(admitted) / entered, 4) if entered else 0.0,
        "dropped_by_limit": len(groups) - len(admitted),
        "summary": (
            f"{len(admitted)} of {entered} {unit} admitted"
            f" ({round(100.0 * len(admitted) / entered, 2) if entered else 0}%)"
        ),
    }
    return selected, gate


def annotate_record(pose: Dict[str, Any]) -> str:
    """The original record plus score/metric data items, terminated by ``$$$$``."""
    extra = {**pose["scores"], **pose["metrics"]}
    existing = set(pose["properties"])
    body = pose["record"].rstrip("\n")
    items = [f"> <{name}>\n{_format_value(value)}\n" for name, value in extra.items()
             if name not in existing and re.fullmatch(r"[^<>\n]+", str(name))]
    if items:
        # A blank line must separate the previous block (M  END or a data value) from new items.
        body += "\n" + ("\n" if existing else "") + "\n".join(items)
    # Data items are each followed by a blank line, including the last one.
    trailer = "\n" if (items or existing) else ""
    return body.rstrip("\n") + "\n" + trailer + "$$$$\n"
def _validate_gate(config: Dict[str, Any]) -> List[str]:
    if (config.get("top_n") or config.get("top_percent")) and not config.get("rank_by"):
        return ["top_n/top_percent need rank_by: a gate must know which candidates are best"]
    return []


class MoleculeFilter(SingleStepMethod):
    """Filter poses/molecules by upstream scores and binding position.

    Joins an upstream score table (Vina, Smina, similarity, ML scorers) onto
    an SDF of poses by molecule name, computes geometry metrics against a
    binding-site box or reference ligand, then keeps poses that satisfy every
    (or any) criterion. It then acts as a gate between screening rounds:
    survivors are ranked by ``rank_by`` and at most ``top_percent`` of the
    candidates that entered (and/or ``top_n``) are admitted, counted per
    molecule with only each molecule's best pose passed on by default.

    Criteria are ``{column, op, value}`` rules. ``column`` can be a score-table
    column, an SDF data property, or a computed metric: heavy_atoms, pose_rank,
    centroid_distance, fraction_in_box, centroid_in_box (needs binding_site) or
    reference_distance (needs reference_ligand).
    """

    register_name = "molecule_filter"
    display_name = "Molecule Filter"
    category = "filter"
    contract_version = 2
    # Widget hints consumed by the workflow builder (ignored elsewhere).
    ui_hints = {
        "criteria": {"widget": "rules", "operators": list(OPERATORS),
                     "metrics": METRICS, "suggestions": list(COMMON_SCORE_COLUMNS)},
        "rank_by": {"suggestions": list(COMMON_SCORE_COLUMNS) + [m for m in METRICS if m != "pose_rank"], "card": True},
        "position_mode": {"card": True},
        "top_percent": {"card": True, "card_format": "top {value}% of {percent_of}"},
        "top_n": {"card": True, "card_format": "max {value} {limit_by}"},
        "best_pose_only": {"card": True, "card_format": "all poses"},
        "id_column": {"suggestions": ["ligand", "name", "id", "molecule"]},
    }

    def __init__(self, name: str = "molecule_filter", version: str = "1.0", parameters: Optional[Dict[str, Any]] = None):
        super().__init__(name=name, version=version, parameters=parameters)
        self.config_spec = ConfigSpec(fields={
            "criteria": ConfigField(list, default=[], validator=_validate_criteria,
                                    description="Rules of the form {column, op, value}"),
            "combine": ConfigField(str, default="all", choices=("all", "any"),
                                   description="Pass when all rules hold, or when any rule holds"),
            "on_missing": ConfigField(str, default="reject", choices=("reject", "pass"),
                                      description="How to treat a pose lacking a rule's column"),
            "position_mode": ConfigField(str, default="none", choices=POSITION_MODES,
                                         description="Binding-position filter against the binding_site box"),
            "min_fraction_in_box": ConfigField((int, float), default=0.8, minimum=0, maximum=1,
                                               description="Used by position_mode=fraction_in_box"),
            "box_padding": ConfigField((int, float), default=0.0, minimum=0, description="Å added to each box half-width"),
            "rank_by": ConfigField(str, default="", description="Column used to rank survivors; required for top_n/top_percent"),
            "ascending": ConfigField(bool, default=True, description="Lower is better (true for docking energies)"),
            "top_percent": ConfigField((int, float), default=0, minimum=0, maximum=100,
                                       description="Admit at most this % of candidates to the next round (0 = no cap)"),
            "percent_of": ConfigField(str, default="input", choices=("input", "passed"),
                                      description="Percent of all candidates that entered, or of those passing the criteria"),
            "top_n": ConfigField(int, default=0, minimum=0, description="Admit at most N candidates (0 = no cap); the tighter cap wins"),
            "limit_by": ConfigField(str, default="molecules", choices=("molecules", "poses"),
                                    description="Count candidates as unique molecules (ranked by best pose) or as poses"),
            "best_pose_only": ConfigField(bool, default=True, description="Pass on only each admitted molecule's best pose"),
            "id_column": ConfigField(str, default="ligand", description="Score-table column holding molecule names"),
            "name_property": ConfigField(str, default="", description="SDF property to use as name instead of the title"),
            "annotate": ConfigField(bool, default=True, description="Write scores and metrics into output SDF"),
            "fail_if_empty": ConfigField(bool, default=False, description="Fail the task when nothing passes"),
        }, validator=_validate_gate)
        self.input_spec = InputSpec(ports={
            "molecules": PortSpec("molecule.collection", "sdf", description="Poses or molecules to filter"),
            "scores": PortSpec("table", ("csv", "tsv", "json"), required=False, cardinality="many",
                               description="Score table(s) joined by molecule name"),
            "binding_site": PortSpec("binding_site", ("box", "json"), required=False,
                                     description="Box for position filters and distance metrics"),
            "reference_ligand": PortSpec(("molecule", "molecule.collection"), "sdf", required=False,
                                         description="Reference pose for reference_distance"),
        })
        self.output_spec = OutputSpec(ports={
            "filtered_molecules": PortSpec("molecule.collection", "sdf", description="Poses that passed, ranked"),
            "filtered_scores": PortSpec("table", "csv", description="Scores and metrics of kept poses"),
            "report": PortSpec("report", "json", description="Counts and rejection reasons"),
        })
        self.resource_spec = ResourceSpec(cpus=1, memory="2GB", time="00:30:00", partition="cpu")
        self.env_requirements = {"software": ["python"], "modules": [], "conda_env": None}
        self.filter_config = self.normalize_config(parameters)

    def validate_bindings(self, bound_ports: Iterable[str]) -> List[str]:
        """Config-dependent port requirements, checked statically by the validator."""
        bound = set(bound_ports)
        errors = []
        needs: Dict[str, List[str]] = {}
        if self.filter_config["position_mode"] != "none":
            needs.setdefault("binding_site", []).append(f"position_mode={self.filter_config['position_mode']}")
        for rule in self.filter_config["criteria"] + [{"column": self.filter_config["rank_by"]}]:
            port = METRICS.get(rule["column"])
            if port:
                needs.setdefault(port, []).append(rule["column"])
        for port, reasons in needs.items():
            if port not in bound:
                errors.append(f"input port '{port}' must be connected for {', '.join(sorted(set(reasons)))}")
        return errors

    def validate_inputs(self, inputs: Dict[str, Any]) -> Tuple[bool, List[str]]:
        errors = []
        if "molecules" not in inputs:
            errors.append("Missing required input 'molecules'")
        errors.extend(self.validate_bindings(inputs))
        for item in _as_list(inputs.get("molecules")) + _as_list(inputs.get("scores")):
            if not isinstance(item, Artifact) or item.storage == ArtifactStorage.FILE:
                if not _path(item).exists():
                    errors.append(f"Input file not found: {_path(item)}")
        return not errors, errors

    def validate_environment(self) -> Tuple[bool, List[str]]:
        return True, []

    def execute(self, task: Task) -> TaskStatus:
        try:
            valid, errors = self.validate_inputs(task.inputs)
            if not valid:
                task.error_message = f"Input validation failed: {errors}"
                return TaskStatus.FAILED
            sdf_text = "".join(_path(item).read_text() for item in _as_list(task.inputs["molecules"]))
            tables = [load_table(_path(item), _format(item, "csv")) for item in _as_list(task.inputs.get("scores"))]
            box = load_box(task.inputs.get("binding_site"))
            reference = None
            if task.inputs.get("reference_ligand") is not None:
                records = split_sdf(_path(task.inputs["reference_ligand"]).read_text())
                reference = _centroid(parse_sdf_record(records[0])["heavy"]) if records else None
                if reference is None:
                    raise ValueError("reference_ligand has no heavy-atom coordinates")
            kept, report = filter_poses(sdf_text, tables, self.filter_config, box, reference)
            self._write_outputs(task, kept, report)
            if self.filter_config["fail_if_empty"] and not kept:
                task.error_message = f"No poses passed the filter: {report['rejected_by']}"
                return TaskStatus.FAILED
            return TaskStatus.COMPLETED
        except Exception as exc:
            task.error_message = f"Molecule filter failed: {exc}"
            return TaskStatus.FAILED

    def _write_outputs(self, task: Task, kept: List[Dict[str, Any]], report: Dict[str, Any]) -> None:
        output_dir = Path(task.work_dir) / "outputs"
        output_dir.mkdir(parents=True, exist_ok=True)
        sdf_path = output_dir / "filtered.sdf"
        with sdf_path.open("w") as handle:
            for pose in kept:
                handle.write(annotate_record(pose) if self.filter_config["annotate"] else pose["record"] + "$$$$\n")
        columns = ["name", "filter_rank", "pose_rank"]
        for pose in kept:
            for name in list(pose["scores"]) + list(pose["metrics"]):
                if name not in columns:
                    columns.append(name)
        scores_path = output_dir / "filtered_scores.csv"
        with scores_path.open("w", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
            writer.writeheader()
            for pose in kept:
                row = {"name": pose["name"], **pose["scores"], **pose["metrics"]}
                writer.writerow({key: _format_value(value) for key, value in row.items()})
        report_path = output_dir / "filter_report.json"
        report_path.write_text(json.dumps(report, indent=2))
        task.outputs = {
            "filtered_molecules": Artifact("molecule.collection", "sdf", sdf_path, metadata={"kept": report["kept"]}),
            "filtered_scores": Artifact("table", "csv", scores_path),
            "report": Artifact("report", "json", report_path),
        }

    def collect_outputs(self, task: Task) -> Dict[str, Any]:
        return task.outputs

    @classmethod
    def from_config(cls, config: Dict[str, Any]) -> "MoleculeFilter":
        return cls(
            name=config.get("name", "molecule_filter"),
            version=config.get("version", "1.0"),
            parameters=config.get("config") or config.get("parameters") or {},
        )
