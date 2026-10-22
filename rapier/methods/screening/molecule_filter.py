"""Filter docked or predicted molecules by scores and binding position.

Unlike :mod:`ligand_filter`, which applies physicochemical rules with RDKit,
this method works on *results*: it joins id-keyed score tables from upstream
methods (Vina ``docking_scores``, ligand_filter ``properties``, ...) onto the
poses, computes simple geometric metrics against a binding-site box or
reference ligand, keeps poses that satisfy user-defined criteria and gates the
survivors by rank. It is pure Python and needs no RDKit.
"""

import math
import re
from typing import Any, Dict, Iterable, List, Optional, Tuple

from ...core.method import Method, RunContext
from ...core.specs import ConfigField, EnvironmentSpec, PortSpec, ResourceSpec
from ...data import (
    KNOWN_DIRECTIONS, BindingSite, Molecule, Molecules, Report, Scores, Table,
    load_box, load_table, molecule_id, parse_sdf_record, split_sdf,
)
from ...data.tables import ID_COLUMNS, POSE_COLUMNS
from ._validation import compare_inventory, integer, number, read_output, read_records


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
    "rmsd_lb", "rmsd_ub", "pose", "similarity", "confidence_score", "affinity_pred_value",
    "affinity_probability_binary", "mw", "logp", "tpsa",
)
ORDERS = ("auto", "ascending", "descending")
# Re-exported for callers that parse filter inputs directly.
__all__ = ["MoleculeFilter", "filter_poses", "evaluate_rule", "annotate_record",
           "split_sdf", "parse_sdf_record", "load_table", "load_box"]


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


# ---- geometry and joins -------------------------------------------------

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
    return molecule_id(name)


def _key_column(rows: List[Dict[str, Any]], preferred: str) -> Optional[str]:
    columns = list(rows[0]) if rows else []
    for candidate in (preferred,) + ID_COLUMNS:
        if candidate in columns:
            return candidate
    return None


def _join_scores(poses: List[Dict[str, Any]], rows: List[Dict[str, Any]], id_column: str) -> int:
    """Attach table rows to poses in place; returns the number of poses left unmatched.

    Rows are grouped by molecule id (``id``, or a legacy ``ligand``/``name``
    column). A single row per id applies to every pose of that molecule;
    several rows pair with poses in order, sorted by ``pose`` (or ``mode``).
    """
    if not rows:
        for pose in poses:
            pose["missing_score_tables"] = pose.get("missing_score_tables", 0) + 1
        return len(poses)
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
        order_key = next((name for name in POSE_COLUMNS
                          if all(_to_number(row.get(name)) is not None for row in group)), None)
        if order_key:
            group.sort(key=lambda row: _to_number(row[order_key]))
    unmatched = 0
    for pose in poses:
        group = groups.get(_normalize_name(pose["name"]))
        if not group:
            unmatched += 1
            pose["missing_score_tables"] = pose.get("missing_score_tables", 0) + 1
            continue
        row = group[0] if len(group) == 1 else (group[pose["pose_rank"] - 1] if pose["pose_rank"] <= len(group) else None)
        if row is None:
            unmatched += 1
            pose["missing_score_tables"] = pose.get("missing_score_tables", 0) + 1
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
    directions: Optional[Dict[str, str]] = None,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Core filtering logic, independent of tasks and artifacts.

    ``directions`` maps score columns to ``"lower"``/``"higher"`` (from the
    score tables' metadata); ``order: auto`` ranks by it. Returns
    ``(kept_poses, report)``; kept poses are ordered by rank.
    """
    poses: List[Dict[str, Any]] = []
    seen: Dict[str, int] = {}
    for index, record in enumerate(split_sdf(sdf_text)):
        parsed = parse_sdf_record(record)
        name = parsed["properties"].get(config["name_property"]) if config["name_property"] else None
        name = name or parsed["title"] or f"mol_{index + 1}"
        identifier = molecule_id(name, f"mol_{index + 1}")
        # Ranks count per id, matching Molecules.records() and the score-row keys.
        seen[identifier] = seen.get(identifier, 0) + 1
        poses.append({"index": index, "name": name, "id": identifier,
                      "pose_rank": seen[identifier], "record": record, "heavy": parsed["heavy"],
                      "properties": parsed["properties"], "scores": {}, "metrics": {}})

    for rows in tables:
        _join_scores(poses, rows, config["id_column"])

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

    selected, gate = _gate(poses, passed, config, directions)
    for rank, pose in enumerate(selected, start=1):
        pose["metrics"]["filter_rank"] = rank

    present = set()
    for pose in poses:
        present.update(pose["metrics"], pose["scores"], pose["properties"])
    present_lower = {name.lower() for name in present}
    selected_indices = {pose["index"] for pose in selected}
    report = {
        "gate": gate,
        "total_poses": len(poses),
        "total_molecules": len({p["name"] for p in poses}),
        "passed_criteria": len(passed),
        "passed_molecules": len({p["name"] for p in passed}),
        "kept": len(selected),
        "kept_molecules": len({p["name"] for p in selected}),
        "rejected_by": rejected_by,
        "poses_without_scores": sum(1 for p in poses if p.get("missing_score_tables")),
        "poses_without_coordinates": sum(1 for p in poses if not p["heavy"]),
        "unknown_columns": sorted({r["column"] for r in rules if r["column"].lower() not in present_lower}),
        "criteria": labels,
        "position_mode": mode,
        "binding_site": box,
        "outcomes": [
            {"index": p["index"], "id": p["id"], "pose": p["pose_rank"],
             "passed": not p["failures"], "selected": p["index"] in selected_indices,
             "failures": p["failures"], "has_coordinates": bool(p["heavy"]),
             "missing_scores": bool(p.get("missing_score_tables"))}
            for p in poses
        ],
    }
    return selected, report


def _rank_order(config: Dict[str, Any], directions: Optional[Dict[str, str]]) -> Tuple[str, str]:
    """``(ascending|descending, why)`` for ranking by ``rank_by``.

    ``order: auto`` uses the score table's declared direction for the column
    (or the well-known direction of common score names), else ascending.
    """
    order = config.get("order") or "auto"
    if order == "auto" and "ascending" in config:  # Raw configs that predate ``order``.
        order = "ascending" if config["ascending"] else "descending"
    if order in ("ascending", "descending"):
        return order, "config"
    known = {**KNOWN_DIRECTIONS, **(directions or {})}
    rank_by = config.get("rank_by") or ""
    better = known.get(rank_by) or next(
        (value for name, value in known.items() if name.lower() == rank_by.lower()), None
    )
    if better:
        return ("ascending" if better == "lower" else "descending"), f"{better} is better"
    return "ascending", "default (no direction declared)"


def _gate(
    poses: List[Dict[str, Any]],
    passed: List[Dict[str, Any]],
    config: Dict[str, Any],
    directions: Optional[Dict[str, str]] = None,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Rank survivors and admit at most top_n / top_percent candidates to the next round.

    A candidate is a molecule (all of its poses) when ``limit_by == "molecules"``,
    otherwise a single pose. Molecules are ranked by their best pose. The percent
    cap is taken of the candidates that *entered* the filter by default, so
    ``top_percent: 1`` on 10,000 molecules admits at most 100, however many
    survive the criteria.
    """
    rank_by = config["rank_by"]
    order, order_source = _rank_order(config, directions)
    sign = 1 if order == "ascending" else -1

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
        "rank_by": rank_by or None,
        "order": order,
        "order_source": order_source,
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


class MoleculeFilter(Method):
    """Filter poses/molecules by upstream scores and binding position.

    Joins upstream score tables (Vina, Smina, similarity, ligand_filter
    properties, ML scorers) onto the molecules by ``id``, computes geometry
    metrics against a binding-site box or reference ligand, then keeps poses
    that satisfy every (or any) criterion. It then acts as a gate between
    screening rounds: survivors are ranked by ``rank_by`` and at most
    ``top_percent`` of the candidates that entered (and/or ``top_n``) are
    admitted, counted per molecule with only each molecule's best pose passed
    on by default. ``order: auto`` ranks in the direction the score table
    declares for ``rank_by`` (lower affinity, higher similarity, ...).

    Criteria are ``{column, op, value}`` rules. ``column`` can be a score-table
    column, an SDF data property, or a computed metric: heavy_atoms, pose_rank,
    centroid_distance, fraction_in_box, centroid_in_box (needs binding_site) or
    reference_distance (needs reference_ligand).
    """

    register_name = "molecule_filter"
    display_name = "Molecule Filter"
    category = "filter"
    inputs = {
        "molecules": PortSpec(Molecules, "sdf", description="Poses or molecules to filter"),
        "scores": PortSpec(Table, ("csv", "tsv", "json"), required=False, cardinality="many",
                           description="Score table(s) joined by molecule id"),
        "binding_site": PortSpec(BindingSite, ("box", "json"), required=False,
                                 description="Box for position filters and distance metrics"),
        "reference_ligand": PortSpec((Molecule, Molecules), "sdf", required=False,
                                     description="Reference pose for reference_distance"),
    }
    outputs = {
        "filtered_molecules": PortSpec(Molecules, "sdf", description="Poses that passed, ranked"),
        "filtered_scores": PortSpec(Scores, "csv", description="Scores and metrics of kept poses"),
        "report": PortSpec(Report, "json", description="Counts and rejection reasons"),
    }
    resources = ResourceSpec(cpus=1, memory="2GB", time="00:30:00", partition="cpu")
    environment = EnvironmentSpec(executables=["python"])
    # Widget hints consumed by the workflow builder (ignored elsewhere).
    ui_hints = {
        "criteria": {"widget": "rules", "operators": list(OPERATORS),
                     "metrics": METRICS, "suggestions": list(COMMON_SCORE_COLUMNS)},
        "rank_by": {"suggestions": list(COMMON_SCORE_COLUMNS) + [m for m in METRICS if m != "pose_rank"], "card": True},
        "order": {"card": True, "card_format": "{value} order"},
        "position_mode": {"card": True},
        "top_percent": {"card": True, "card_format": "top {value}% of {percent_of}"},
        "top_n": {"card": True, "card_format": "max {value} {limit_by}"},
        "best_pose_only": {"card": True, "card_format": "all poses"},
        "id_column": {"suggestions": list(ID_COLUMNS[:4])},
    }
    config_fields = {
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
        "order": ConfigField(str, default="auto", choices=ORDERS,
                             description="Ranking direction; auto uses the score table's declared direction"),
        "top_percent": ConfigField((int, float), default=0, minimum=0, maximum=100,
                                   description="Admit at most this % of candidates to the next round (0 = no cap)"),
        "percent_of": ConfigField(str, default="input", choices=("input", "passed"),
                                  description="Percent of all candidates that entered, or of those passing the criteria"),
        "top_n": ConfigField(int, default=0, minimum=0, description="Admit at most N candidates (0 = no cap); the tighter cap wins"),
        "limit_by": ConfigField(str, default="molecules", choices=("molecules", "poses"),
                                description="Count candidates as unique molecules (ranked by best pose) or as poses"),
        "best_pose_only": ConfigField(bool, default=True, description="Pass on only each admitted molecule's best pose"),
        "id_column": ConfigField(str, default="id", description="Score-table column holding molecule ids"),
        "name_property": ConfigField(str, default="", description="SDF property to use as name instead of the title"),
        "annotate": ConfigField(bool, default=True, description="Write scores and metrics into output SDF"),
        "fail_if_empty": ConfigField(bool, default=False, description="Fail the task when nothing passes"),
    }

    def migrate_config(self, config: Dict[str, Any]) -> Dict[str, Any]:
        if "ascending" in config:  # Superseded by `order`.
            ascending = config.pop("ascending")
            config.setdefault("order", "ascending" if ascending else "descending")
        return config

    def check_config(self, config: Dict[str, Any]) -> List[str]:
        if (config.get("top_n") or config.get("top_percent")) and not config.get("rank_by"):
            return ["top_n/top_percent need rank_by: a gate must know which candidates are best"]
        return []

    def validate_bindings(self, bound_ports: Iterable[str]) -> List[str]:
        """Config-dependent port requirements, checked statically by the validator."""
        bound = set(bound_ports)
        needs: Dict[str, List[str]] = {}
        if self.config["position_mode"] != "none":
            needs.setdefault("binding_site", []).append(f"position_mode={self.config['position_mode']}")
        for rule in self.config["criteria"] + [{"column": self.config["rank_by"]}]:
            port = METRICS.get(rule["column"])
            if port:
                needs.setdefault(port, []).append(rule["column"])
        return [
            f"input port '{port}' must be connected for {', '.join(sorted(set(reasons)))}"
            for port, reasons in needs.items() if port not in bound
        ]

    def check_dependencies(self, inputs: Dict[str, Any], ctx: RunContext) -> List[str]:
        errors = self.validate_bindings(name for name, value in inputs.items() if value is not None)
        records = read_records(inputs, "molecules", errors, name_property=self.config["name_property"])
        if records is not None and not records:
            errors.append("molecules must contain at least one record")
        poses = [{"name": record.name, "pose_rank": record.pose, "scores": {}} for record in records or []]
        for index, table in enumerate(inputs.get("scores") or []):
            try:
                rows = table.rows()
                _join_scores(poses, rows, self.config["id_column"])
                key = _key_column(rows, self.config["id_column"])
                pose_key = next((name for name in POSE_COLUMNS if rows and name in rows[0]), None)
                if key and pose_key:
                    keys = [(_normalize_name(row.get(key)), integer(row.get(pose_key), 1)) for row in rows]
                    if any(rank is None for _, rank in keys) or len(set(keys)) != len(keys):
                        errors.append(f"scores[{index}]: invalid or duplicate molecule/pose keys")
            except Exception as exc:
                errors.append(f"Cannot join scores[{index}]: {exc}")
        if inputs.get("binding_site") is not None:
            try:
                box = load_box(inputs["binding_site"])
                if box is None or any(
                    not isinstance(box.get(key), (list, tuple)) or len(box[key]) != 3
                    or any(number(value) is None or (key == "size" and number(value) <= 0) for value in box[key])
                    for key in ("center", "size")
                ):
                    errors.append("binding_site needs three finite center coordinates and positive size dimensions")
            except Exception as exc:
                errors.append(f"Cannot read binding_site: {exc}")
        if inputs.get("reference_ligand") is not None:
            reference = read_records(inputs, "reference_ligand", errors)
            if reference is not None and (not reference or not reference[0].heavy_atoms
                    or any(number(value) is None for point in reference[0].heavy_atoms for value in point)):
                errors.append("reference_ligand has no finite heavy-atom coordinates")
        return errors

    def validate_outputs(self, inputs: Dict[str, Any], outputs: Dict[str, Any], ctx: RunContext) -> List[str]:
        errors: List[str] = []
        source = read_records(inputs, "molecules", errors, name_property=self.config["name_property"])
        filtered = read_output(outputs, "filtered_molecules", "records", errors, name_property=self.config["name_property"])
        rows = read_output(outputs, "filtered_scores", "rows", errors)
        report = read_output(outputs, "report", "load", errors)
        row_keys, source_keys = [], []
        for index, row in enumerate(rows or []):
            where = f"filtered_scores row {index + 1}"
            identifier, pose = row.get("id"), integer(row.get("pose"), 1)
            original_pose = integer(row.get("pose_rank"), 1)
            if not isinstance(identifier, str) or not identifier or pose is None or original_pose is None:
                errors.append(f"{where}: needs id and positive pose/pose_rank keys")
            else:
                row_keys.append((identifier, pose))
                source_keys.append((identifier, original_pose))
            if integer(row.get("filter_rank"), 1) != index + 1:
                errors.append(f"{where}: filter_rank must equal {index + 1}")
            for metric in ("heavy_atoms", "centroid_distance", "fraction_in_box", "reference_distance"):
                if metric in row and number(row[metric]) is None:
                    errors.append(f"{where}: {metric} must be finite")
        if filtered is not None and rows is not None:
            compare_inventory(row_keys, [(record.id, record.pose) for record in filtered], "filtered molecule/score keys", errors)
        for record in filtered or []:
            if any(number(value) is None for point in record.heavy_atoms for value in point):
                errors.append(f"filtered_molecules record {record.index + 1}: coordinates must be finite")
        if source is not None:
            available = {(record.id, record.pose) for record in source}
            if len(set(source_keys)) != len(source_keys) or any(key not in available for key in source_keys):
                errors.append("filtered_scores contains duplicate or unknown input pose keys")
        if self.config["fail_if_empty"] and filtered is not None and not filtered:
            errors.append("No poses passed the filter")
        if report is None:
            return errors
        if not isinstance(report, dict):
            errors.append("report must be a mapping")
            return errors
        total = len(source) if source is not None else integer(report.get("total_poses"))
        total = total if total is not None else 0
        kept = len(filtered) if filtered is not None else len(rows or [])
        expected = {"total_poses": total, "kept": kept}
        if source is not None:
            expected["total_molecules"] = len({record.name for record in source})
            expected["poses_without_coordinates"] = sum(not record.heavy_atoms for record in source)
        if filtered is not None:
            expected["kept_molecules"] = len({record.name for record in filtered})
        outcomes = report.get("outcomes")
        good_outcomes = []
        if not isinstance(outcomes, list):
            errors.append("report.outcomes must account for every input pose")
        else:
            for index, outcome in enumerate(outcomes):
                if not isinstance(outcome, dict) or not isinstance(outcome.get("id"), str) or integer(outcome.get("pose"), 1) is None or not all(isinstance(outcome.get(key), bool)
                        for key in ("passed", "selected", "has_coordinates", "missing_scores")) or not isinstance(outcome.get("failures"), list):
                    errors.append(f"report.outcomes[{index}]: invalid outcome flags or failures")
                    continue
                failures = outcome["failures"]
                if any(not isinstance(failure, str) or not failure for failure in failures):
                    errors.append(f"report.outcomes[{index}]: failures must be nonempty strings")
                    continue
                if outcome["passed"] != (not failures) or outcome["selected"] and not outcome["passed"]:
                    errors.append(f"report.outcomes[{index}]: inconsistent selection/failure flags")
                good_outcomes.append(outcome)
            if source is not None:
                compare_inventory([(outcome.get("id"), integer(outcome.get("pose"), 1)) for outcome in good_outcomes],
                                  [(record.id, record.pose) for record in source], "report outcome inventory", errors)
                source_lookup = {(record.id, record.pose): record for record in source}
                joined = [{"name": record.name, "pose_rank": record.pose, "scores": {}} for record in source]
                for index, table in enumerate(inputs.get("scores") or []):
                    try:
                        _join_scores(joined, table.rows(), self.config["id_column"])
                    except Exception as exc:
                        errors.append(f"Cannot verify input score accounting for scores[{index}]: {exc}")
                missing_scores = {(record.id, record.pose): bool(pose.get("missing_score_tables"))
                                  for record, pose in zip(source, joined)}
                for outcome in good_outcomes:
                    key = (outcome["id"], integer(outcome["pose"], 1))
                    record = source_lookup.get(key)
                    if record is not None and (integer(outcome.get("index")) != record.index
                            or outcome["has_coordinates"] != bool(record.heavy_atoms)
                            or outcome["missing_scores"] != missing_scores[key]):
                        errors.append(f"report outcome {outcome['id']}/{outcome['pose']}: input index/coordinate/score flags disagree")
            selected = [outcome for outcome in good_outcomes if outcome["selected"]]
            passed = [outcome for outcome in good_outcomes if outcome["passed"]]
            if rows is not None:
                compare_inventory(source_keys, [(outcome.get("id"), integer(outcome.get("pose"), 1)) for outcome in selected],
                                  "selected outcome/score inventory", errors)
            expected["passed_criteria"] = len(passed)
            expected["poses_without_scores"] = sum(outcome["missing_scores"] for outcome in good_outcomes)
            if source is not None:
                names = {(record.id, record.pose): record.name for record in source}
                expected["passed_molecules"] = len({names.get((outcome.get("id"), integer(outcome.get("pose"), 1))) for outcome in passed})
            rejected: Dict[str, int] = {}
            for outcome in good_outcomes:
                for failure in outcome["failures"]:
                    rejected[failure] = rejected.get(failure, 0) + 1
            if report.get("rejected_by") != rejected:
                errors.append("report.rejected_by disagrees with per-pose outcomes")
        for key, value in expected.items():
            if integer(report.get(key)) != value:
                errors.append(f"report.{key} must equal {value}")
        passed_count = integer(report.get("passed_criteria"))
        if passed_count is None or not kept <= passed_count <= total:
            errors.append("report counts must satisfy kept <= passed_criteria <= total_poses")
        gate = report.get("gate")
        if not isinstance(gate, dict):
            errors.append("report.gate must be a mapping")
        else:
            unit = "molecules" if self.config["limit_by"] == "molecules" else "poses"
            entered = expected.get("total_molecules", 0) if unit == "molecules" else total
            admitted = expected.get("kept_molecules", 0) if unit == "molecules" else kept
            if gate.get("unit") != unit or integer(gate.get("entered")) != entered or integer(gate.get("admitted")) != admitted:
                errors.append("report.gate unit/entered/admitted disagree with inventories")
            candidates, dropped = integer(gate.get("passed_criteria")), integer(gate.get("dropped_by_limit"))
            if candidates is None or dropped is None or candidates - admitted != dropped:
                errors.append("report.gate candidates must equal admitted + dropped_by_limit")
            if isinstance(outcomes, list):
                candidate_count = expected.get("passed_molecules") if unit == "molecules" or self.config["best_pose_only"] else len(passed)
                if candidate_count is not None and candidates != candidate_count:
                    errors.append("report.gate passed_criteria disagrees with per-pose outcomes")
            basis = entered if self.config["percent_of"] == "input" else candidates
            limits = [self.config["top_n"]] if self.config["top_n"] else []
            if self.config["top_percent"] and basis is not None:
                limits.append(math.ceil(round(basis * self.config["top_percent"] / 100.0, 9)))
            limit = min(limits) if limits else None
            if gate.get("limit") != limit or limit is not None and admitted > limit:
                errors.append("report.gate limit disagrees with configured gate")
            percent = round(100.0 * admitted / entered, 4) if entered else 0.0
            if number(gate.get("admitted_percent")) != percent:
                errors.append("report.gate admitted_percent disagrees with counts")
        return errors

    def run(self, inputs: Dict[str, Any], ctx: RunContext) -> Dict[str, Any]:
        molecules = inputs["molecules"]
        tables = list(inputs.get("scores") or [])
        # Directions declared by upstream score tables; well-known names fill the gaps.
        directions: Dict[str, str] = {}
        for table in tables:
            if isinstance(table, Scores):
                directions.update(table.directions(include_known=False))
        reference = None
        if inputs.get("reference_ligand") is not None:
            records = inputs["reference_ligand"].records()
            reference = _centroid(records[0].heavy_atoms) if records else None
            if reference is None:
                raise ValueError("reference_ligand has no heavy-atom coordinates")
        kept, report = filter_poses(
            molecules.path.read_text(), [table.rows() for table in tables], self.config,
            load_box(inputs.get("binding_site")), reference, directions,
        )
        return self._outputs(ctx, molecules, kept, report, directions)

    def _outputs(self, ctx: RunContext, molecules: Any, kept: List[Dict[str, Any]],
                 report: Dict[str, Any], directions: Dict[str, str]) -> Dict[str, Any]:
        # Poses stay Poses; the output port accepts any Molecules subtype.
        output_class = type(molecules) if isinstance(molecules, Molecules) else Molecules
        blocks = [annotate_record(pose) if self.config["annotate"] else pose["record"] for pose in kept]
        rows = []
        # `pose` numbers poses as they appear in the output SDF, so a chained gate
        # pairs rows with the right records; the input rank stays in `pose_rank`.
        output_pose: Dict[str, int] = {}
        for pose in kept:
            output_pose[pose["id"]] = output_pose.get(pose["id"], 0) + 1
            row = {"id": pose["id"], "pose": output_pose[pose["id"]], "name": pose["name"],
                   "filter_rank": pose["metrics"].get("filter_rank")}
            for name, value in {**pose["scores"], **pose["metrics"]}.items():
                if name not in ("id", "pose"):
                    row[name] = _format_value(value)
            rows.append(row)
        # Carry score directions forward so a chained gate can rank with order: auto.
        present = {name for row in rows for name in row}
        carried = {name: better for name, better in {**KNOWN_DIRECTIONS, **directions}.items() if name in present}
        return {
            "filtered_molecules": output_class.write_sdf(blocks, ctx.output_dir / "filtered.sdf", {"kept": report["kept"]}),
            "filtered_scores": Scores.from_records(rows, ctx.output_dir / "filtered_scores.csv", directions=carried),
            "report": report,
        }
