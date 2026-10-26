"""Validated resource requests owned by individual method executions."""

import re
from collections.abc import Mapping
from dataclasses import asdict
from typing import Any, Dict, Iterator, List, Optional

from rapier.core.specs import ResourceSpec


class ResourceController(Mapping):
    """Describe one method's requested compute resources.

    Controllers are immutable mappings. They describe requests only; executors
    remain responsible for allocating resources and running a task.
    """

    def __init__(self, resources: Optional[Dict[str, Any]] = None):
        values = {} if resources is None else resources
        errors = self.validation_errors(values)
        if errors:
            raise ValueError("Invalid resource controller: " + "; ".join(errors))
        self._resources = dict(values)

    @staticmethod
    def validation_errors(resources: Any) -> List[str]:
        """Return errors for a single flat resource mapping."""
        if not isinstance(resources, Mapping):
            return ["resource_controller must be a single resource mapping"]

        errors = []
        for name, value in resources.items():
            if not isinstance(name, str) or not name:
                errors.append("Resource field names must be non-empty strings")
            elif value is not None and not isinstance(value, (str, int, float, bool)):
                errors.append(f"Resource field '{name}' must be a scalar value")

        for name, minimum in (("cpus", 1), ("nodes", 1), ("gpus", 0)):
            if name in resources:
                value = resources[name]
                if type(value) is not int or value < minimum:
                    requirement = "positive" if minimum else "non-negative"
                    errors.append(f"{name} must be a {requirement} integer")

        for name in ("gpu_type", "partition", "account", "qos", "constraint"):
            if name in resources:
                value = resources[name]
                if not isinstance(value, str) or not value.strip():
                    errors.append(f"{name} must be a non-empty string")

        if "exclusive" in resources and type(resources["exclusive"]) is not bool:
            errors.append("exclusive must be a boolean")

        if "memory" in resources:
            memory = resources["memory"]
            match = (
                re.fullmatch(r"(\d+(?:\.\d+)?)(?:KB|MB|GB|TB)", memory)
                if isinstance(memory, str)
                else None
            )
            if match is None or float(match.group(1)) <= 0:
                errors.append(
                    "memory must be a positive value with a unit (KB, MB, GB, TB)"
                )

        if "time_limit" in resources:
            time_limit = resources["time_limit"]
            valid_time = False
            if isinstance(time_limit, str):
                simple = re.fullmatch(r"(\d+)(s|m|h|d)", time_limit)
                clock = re.fullmatch(r"(?:(\d+)-)?(?:(\d+):)?(\d+):(\d+)", time_limit)
                if simple:
                    valid_time = int(simple.group(1)) > 0
                elif clock:
                    days, hours, minutes, seconds = (
                        int(part or 0) for part in clock.groups()
                    )
                    valid_time = (
                        minutes < 60
                        and seconds < 60
                        and days + hours + minutes + seconds > 0
                    )
            if not valid_time:
                errors.append(
                    "time_limit must be a positive duration (e.g. '1h' or '01:00:00')"
                )

        return errors

    @classmethod
    def resolve(
        cls,
        defaults: Optional[ResourceSpec] = None,
        overrides: Optional[Dict[str, Any]] = None,
    ) -> "ResourceController":
        """Combine a method's ResourceSpec defaults and configured overrides."""
        values = asdict(defaults if defaults is not None else ResourceSpec())
        values["time_limit"] = values.pop("time")
        values = {name: value for name, value in values.items() if value is not None}
        if overrides is not None:
            values.update(cls(overrides))
        return cls(values)

    @property
    def resources(self) -> Dict[str, Any]:
        """Return an independent resource dictionary for existing consumers."""
        return self.to_dict()

    def to_dict(self) -> Dict[str, Any]:
        """Serialize this controller as the canonical flat resource mapping."""
        return dict(self._resources)

    def __getitem__(self, key: str) -> Any:
        return self._resources[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._resources)

    def __len__(self) -> int:
        return len(self._resources)
