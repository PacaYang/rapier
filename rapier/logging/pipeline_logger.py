"""Pipeline-level logging for workflow execution."""

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

from ..core.pipeline import Pipeline
from ..core.task import Task
from ..core.enums import TaskStatus
from .log_manager import get_log_manager


class ExecutionResult:
    """Container for pipeline execution results."""

    def __init__(
        self,
        pipeline_id: str,
        status: str,
        tasks_completed: int,
        tasks_failed: int,
        duration: float,
        outputs: Optional[Dict[str, Any]] = None
    ):
        self.pipeline_id = pipeline_id
        self.status = status
        self.tasks_completed = tasks_completed
        self.tasks_failed = tasks_failed
        self.duration = duration
        self.outputs = outputs or {}


class TaskResult:
    """Container for task execution results."""

    def __init__(
        self,
        task_id: str,
        status: TaskStatus,
        outputs: Optional[Dict[str, Any]] = None,
        error: Optional[str] = None,
        duration: Optional[float] = None
    ):
        self.task_id = task_id
        self.status = status
        self.outputs = outputs or {}
        self.error = error
        self.duration = duration


class PipelineLogger:
    """Top-level pipeline logging for workflow execution."""

    def __init__(self, pipeline_id: str, log_dir: Optional[str] = None):
        """Initialize pipeline logger.

        Args:
            pipeline_id: Unique pipeline identifier
            log_dir: Directory for log files
        """
        self.pipeline_id = pipeline_id
        self.log_dir = Path(log_dir) if log_dir else None

        # Get logger from log manager
        self.log_manager = get_log_manager()
        if self.log_dir:
            self.log_manager.set_log_directory(str(self.log_dir))

        self.logger = self.log_manager.get_logger(f'pipeline.{pipeline_id}')

        # Create dedicated log files
        if self.log_dir:
            self.log_dir.mkdir(parents=True, exist_ok=True)
            self.structured_log_file = self.log_dir / f'pipeline_{pipeline_id}.log'
            self.summary_log_file = self.log_dir / f'pipeline_{pipeline_id}_summary.txt'
        else:
            self.structured_log_file = None
            self.summary_log_file = None

        self.start_time: Optional[datetime] = None

    def _write_structured_log(self, event: str, data: Dict[str, Any]) -> None:
        """Write structured JSON log entry.

        Args:
            event: Event type
            data: Event data
        """
        if not self.structured_log_file:
            return

        log_entry = {
            'timestamp': datetime.now().isoformat(),
            'pipeline_id': self.pipeline_id,
            'event': event,
            'data': data
        }

        with open(self.structured_log_file, 'a', encoding='utf-8') as f:
            f.write(json.dumps(log_entry) + '\n')

    def _write_summary(self, message: str) -> None:
        """Write human-readable summary log.

        Args:
            message: Summary message
        """
        if not self.summary_log_file:
            return

        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        with open(self.summary_log_file, 'a', encoding='utf-8') as f:
            f.write(f"[{timestamp}] {message}\n")

    def log_start(self, pipeline: Pipeline) -> None:
        """Log pipeline execution start.

        Args:
            pipeline: Pipeline instance
        """
        self.start_time = datetime.now()

        data = {
            'name': pipeline.name,
            'total_tasks': len(pipeline.tasks),
            'start_time': self.start_time.isoformat()
        }

        self._write_structured_log('pipeline_start', data)
        self._write_summary(f"Pipeline '{pipeline.name}' started with {len(pipeline.tasks)} tasks")

        self.logger.info(
            f"Pipeline execution started: {pipeline.name}",
            extra={'pipeline_id': self.pipeline_id, 'event': 'pipeline_start', 'data': data}
        )

    def log_task_submitted(self, task: Task) -> None:
        """Log task submission.

        Args:
            task: Task instance
        """
        data = {
            'task_id': task.task_id,
            'method': task.method.name if task.method else 'unknown',
            'status': task.status.value if hasattr(task.status, 'value') else str(task.status)
        }

        self._write_structured_log('task_submitted', data)
        self._write_summary(f"Task {task.task_id} submitted to SLURM")

        self.logger.info(
            f"Task submitted: {task.task_id}",
            extra={
                'pipeline_id': self.pipeline_id,
                'task_id': task.task_id,
                'event': 'task_submitted',
                'data': data
            }
        )

    def log_task_completed(self, task: Task, result: TaskResult) -> None:
        """Log task completion.

        Args:
            task: Task instance
            result: Task execution result
        """
        data = {
            'task_id': task.task_id,
            'method': task.method.name if task.method else 'unknown',
            'status': result.status.value if hasattr(result.status, 'value') else str(result.status),
            'duration': result.duration,
            'outputs': result.outputs
        }

        self._write_structured_log('task_completed', data)

        duration_str = f"{result.duration:.2f}s" if result.duration else "unknown"
        self._write_summary(f"Task {task.task_id} completed successfully in {duration_str}")

        self.logger.info(
            f"Task completed: {task.task_id} in {duration_str}",
            extra={
                'pipeline_id': self.pipeline_id,
                'task_id': task.task_id,
                'event': 'task_completed',
                'data': data
            }
        )

    def log_task_failed(self, task: Task, error: Exception) -> None:
        """Log task failure.

        Args:
            task: Task instance
            error: Exception that caused the failure
        """
        data = {
            'task_id': task.task_id,
            'method': task.method.name if task.method else 'unknown',
            'error_type': type(error).__name__,
            'error_message': str(error)
        }

        self._write_structured_log('task_failed', data)
        self._write_summary(f"Task {task.task_id} FAILED: {error}")

        self.logger.error(
            f"Task failed: {task.task_id} - {error}",
            extra={
                'pipeline_id': self.pipeline_id,
                'task_id': task.task_id,
                'event': 'task_failed',
                'data': data
            },
            exc_info=True
        )

    def log_pipeline_complete(self, result: ExecutionResult) -> None:
        """Log pipeline execution completion.

        Args:
            result: Pipeline execution result
        """
        data = {
            'status': result.status,
            'tasks_completed': result.tasks_completed,
            'tasks_failed': result.tasks_failed,
            'duration': result.duration,
            'outputs': result.outputs
        }

        self._write_structured_log('pipeline_complete', data)

        duration_str = f"{result.duration:.2f}s"
        summary = (
            f"Pipeline execution completed: {result.status}\n"
            f"  Tasks completed: {result.tasks_completed}\n"
            f"  Tasks failed: {result.tasks_failed}\n"
            f"  Total duration: {duration_str}"
        )
        self._write_summary(summary)

        self.logger.info(
            f"Pipeline execution completed: {result.status}",
            extra={
                'pipeline_id': self.pipeline_id,
                'event': 'pipeline_complete',
                'data': data
            }
        )

    def log_task_skipped(self, task: Task, reason: str) -> None:
        """Log task being skipped.

        Args:
            task: Task instance
            reason: Reason for skipping
        """
        data = {
            'task_id': task.task_id,
            'method': task.method.name if task.method else 'unknown',
            'reason': reason
        }

        self._write_structured_log('task_skipped', data)
        self._write_summary(f"Task {task.task_id} skipped: {reason}")

        self.logger.info(
            f"Task skipped: {task.task_id} - {reason}",
            extra={
                'pipeline_id': self.pipeline_id,
                'task_id': task.task_id,
                'event': 'task_skipped',
                'data': data
            }
        )

    def log_dependency_resolved(self, task_id: str, dependencies: list) -> None:
        """Log dependency resolution.

        Args:
            task_id: Task identifier
            dependencies: List of resolved dependency IDs
        """
        data = {
            'task_id': task_id,
            'dependencies': dependencies
        }

        self._write_structured_log('dependency_resolved', data)
        self._write_summary(f"Dependencies resolved for task {task_id}: {dependencies}")

        self.logger.debug(
            f"Dependencies resolved for task {task_id}",
            extra={
                'pipeline_id': self.pipeline_id,
                'task_id': task_id,
                'event': 'dependency_resolved',
                'data': data
            }
        )
