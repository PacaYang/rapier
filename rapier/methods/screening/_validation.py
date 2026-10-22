"""Small, RDKit-free readers for pooled screening result validation."""

import math
from collections import Counter
from typing import Any, Dict, List, Optional


def read_output(outputs: Dict[str, Any], name: str, reader: str, errors: List[str], **kwargs: Any) -> Any:
    value = outputs.get(name)
    if value is None:
        errors.append(f"Missing output '{name}'")
        return None
    try:
        if reader == "rows":
            result, row_errors = value.rows_with_errors()
            errors.extend(f"Cannot read '{name}': {error}" for error in row_errors)
        else:
            result = getattr(value, reader)(**kwargs)
        if result is None:
            errors.append(f"Output '{name}' must not be null")
        return result
    except Exception as exc:
        errors.append(f"Cannot read '{name}': {exc}")
        return None


def read_records(inputs: Dict[str, Any], name: str, errors: List[str], **kwargs: Any) -> Any:
    value = inputs.get(name)
    if value is None:
        errors.append(f"Missing input '{name}'")
        return None
    try:
        return value.records(**kwargs)
    except Exception as exc:
        errors.append(f"Cannot read input '{name}': {exc}")
        return None


def number(value: Any) -> Optional[float]:
    if isinstance(value, bool) or value is None or str(value).strip() == "":
        return None
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return result if math.isfinite(result) else None


def integer(value: Any, minimum: int = 0) -> Optional[int]:
    result = number(value)
    return int(result) if result is not None and result.is_integer() and result >= minimum else None


def boolean(value: Any) -> Optional[bool]:
    if value is True or value == "True":
        return True
    if value is False or value == "False":
        return False
    return None


def compare_inventory(actual: Any, expected: Any, label: str, errors: List[str]) -> None:
    actual, expected = Counter(actual), Counter(expected)
    missing, unexpected = expected - actual, actual - expected
    if missing:
        errors.append(f"{label}: missing {dict(missing)}")
    if unexpected:
        errors.append(f"{label}: unexpected or duplicate {dict(unexpected)}")


def occurrence_inventory(records: Any) -> List[Dict[str, Any]]:
    """Keep unique ids stable and disambiguate duplicates without id collisions."""
    counts = Counter(record.id for record in records)
    reserved = set(counts)
    inventory = []
    for record in records:
        identifier = record.id
        if counts[identifier] > 1:
            candidate = f"{identifier}__{record.pose}"
            while candidate in reserved:
                candidate += "x"
            identifier = candidate
            reserved.add(identifier)
        inventory.append({"index": record.index, "input_id": record.id,
                          "occurrence": record.pose, "id": identifier})
    return inventory
