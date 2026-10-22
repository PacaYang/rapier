"""Generic scheduler entry point for any :class:`~rapier.core.method.Method`.

``Method.get_command`` writes ``<work_dir>/task.json`` (method import path,
step name, config and the already-staged inputs) and returns
``python -m rapier.methods.runner --task <work_dir>/task.json``. The job
rebuilds the method, runs the same ``execute`` used locally and writes the
formatted outputs to ``<work_dir>/outputs/outputs.json``, which
``Method.collect_outputs`` reads back once the job has finished.
"""

import importlib
import json
import shlex
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from rapier.artifacts import artifact_from_data, artifact_to_data
from rapier.core.enums import TaskStatus
from rapier.core.method import OUTPUT_VALIDATION_VERSION

OUTPUTS_FILE = "outputs.json"


def write_task_file(method: Any, task: Any) -> Path:
    """Serialize everything a job needs to rerun ``method`` on ``task``."""
    work_dir = Path(task.work_dir or (Path.cwd() / "runs" / task.task_id)).resolve()
    work_dir.mkdir(parents=True, exist_ok=True)
    task.work_dir = work_dir
    task.outputs = {}
    task.error_message = None
    task.metadata.pop("output_validation_version", None)
    stale = work_dir / "outputs" / OUTPUTS_FILE
    if stale.exists():
        stale.unlink()
    payload = {
        "runner_version": 1,
        "task_id": task.task_id,
        "method": {
            "module": type(method).__module__,
            "class": type(method).__qualname__,
            "name": method.name,
            "config": method.config,
        },
        "inputs": artifact_to_data(task.inputs),
        "work_dir": str(work_dir),
        "input_dir": str(task.input_dir) if task.input_dir else None,
        "resources": task.metadata.get("resources", {}),
    }
    path = work_dir / "task.json"
    path.write_text(json.dumps(payload, indent=2, default=str))
    return path


def runner_command(method: Any, task: Any) -> str:
    path = write_task_file(method, task)
    return shlex.join([sys.executable, "-m", "rapier.methods.runner", "--task", str(path)])


def read_outputs(task: Any) -> Dict[str, Any]:
    """Outputs written by a scheduler job; falls back to in-process outputs."""
    path = Path(task.work_dir) / "outputs" / OUTPUTS_FILE if task.work_dir else None
    if path is not None and path.exists():
        payload = json.loads(path.read_text())
        if payload.get("status") != "completed":
            raise RuntimeError(payload.get("error") or "Method run failed")
        if payload.get("validation_version") != OUTPUT_VALIDATION_VERSION:
            raise RuntimeError("Output receipt has no supported pooled validation version; rerun this task")
        if payload.get("task_id") != task.task_id:
            raise RuntimeError("Output receipt belongs to a different task; rerun this task")
        outputs = artifact_from_data(payload["outputs"])
    elif task.metadata.get("output_validation_version") == OUTPUT_VALIDATION_VERSION:
        outputs = task.outputs
    else:
        raise FileNotFoundError(f"No validated {OUTPUTS_FILE} for task '{task.task_id}'; did the job run?")
    if task.method:
        valid, errors = task.method.output_spec.validate(outputs)
        if not valid:
            raise ValueError("Collected outputs are invalid: " + "; ".join(errors))
    return outputs


def run_task_file(path: Path) -> Dict[str, Any]:
    """Rebuild and execute the method described by a ``task.json``."""
    from rapier.core.task import Task

    payload = json.loads(Path(path).read_text())
    spec = payload["method"]
    method_class = getattr(importlib.import_module(spec["module"]), spec["class"])
    method = method_class(name=spec["name"], config=spec["config"])
    task = Task(payload["task_id"], method, artifact_from_data(payload["inputs"]),
                metadata={"resources": payload.get("resources") or {}})
    task.work_dir = Path(payload["work_dir"])
    if payload.get("input_dir"):
        task.input_dir = Path(payload["input_dir"])
    status = method.execute(task)
    if (status == TaskStatus.COMPLETED
            and task.metadata.get("output_validation_version") != OUTPUT_VALIDATION_VERSION):
        status = TaskStatus.FAILED
        task.outputs = {}
        task.error_message = "Method execution bypassed pooled output validation; implement run() instead of execute()"
    result = {
        "status": "completed" if status == TaskStatus.COMPLETED else "failed",
        "error": task.error_message,
        "outputs": artifact_to_data(task.outputs),
        "task_id": task.task_id,
        "validation_version": (OUTPUT_VALIDATION_VERSION if status == TaskStatus.COMPLETED else None),
    }
    output_path = task.work_dir / "outputs" / OUTPUTS_FILE
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(result, indent=2, default=str))
    return result


def main(argv: Optional[List[str]] = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Run a Rapier method task file")
    parser.add_argument("--task", type=Path, required=True)
    args = parser.parse_args(argv)
    result = run_task_file(args.task)
    if result["status"] != "completed":
        print(result["error"], file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised by the scheduler
    raise SystemExit(main())
