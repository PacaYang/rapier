"""Workflow location metadata and the future task output-directory contract."""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional

OUTPUT_DIR_TEMPLATE = "{working_dir}/{region}/{task_id}/outputs"


@dataclass
class WorkflowMetadata:
    """Describe a workflow's location without creating execution directories.

    ``region`` identifies the compute location or cluster for the workflow.
    Relative working directories are resolved against the metadata file's
    directory, or the project directory for inline metadata. Backend routing
    and output routing remain placeholders.
    """

    working_dir: str = "."
    region: str = "default"
    base_dir: Optional[str] = field(default=None, repr=False, compare=False)

    def __post_init__(self):
        for name in ("working_dir", "region"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(
                    f"Workflow metadata '{name}' must be a non-empty string"
                )

    @property
    def resolved_working_dir(self) -> str:
        """Return an absolute path without touching the filesystem."""
        path = Path(self.working_dir).expanduser()
        if not path.is_absolute():
            path = Path(self.base_dir or Path.cwd()) / path
        return str(path.resolve())

    def to_dict(self) -> Dict[str, str]:
        return {"working_dir": self.working_dir, "region": self.region}

    def output_directory_placeholder(self) -> Dict[str, Any]:
        """Describe planned output placement; executors do not consume it yet."""
        return {
            "template": OUTPUT_DIR_TEMPLATE,
            "working_dir": self.resolved_working_dir,
            "region": self.region,
            "status": "placeholder",
        }

    @classmethod
    def from_dict(
        cls, data: Dict[str, Any], base_dir: Optional[str] = None
    ) -> "WorkflowMetadata":
        if not isinstance(data, dict):
            raise ValueError("Workflow metadata must be a mapping")
        missing = {"working_dir", "region"} - data.keys()
        if missing:
            raise ValueError(
                "Workflow metadata is missing: " + ", ".join(sorted(missing))
            )
        return cls(
            working_dir=data["working_dir"], region=data["region"], base_dir=base_dir
        )
