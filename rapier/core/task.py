"""
Task class representing a single method execution instance.
"""

import uuid
from typing import Dict, Any, Optional
from pathlib import Path
from datetime import datetime

from rapier.core.enums import TaskStatus, FailurePolicy
from rapier.artifacts import artifact_from_data, artifact_to_data


class Task:
    """
    Represents a single execution instance of a method.

    Tasks are stateful runtime instances, while Methods are stateless definitions.
    """

    def __init__(
        self,
        task_id: str,
        method: Optional['BaseMethod'],
        inputs: Dict[str, Any],
        metadata: Optional[Dict[str, Any]] = None,
    ):
        """
        Initialize task.

        Args:
            task_id: Unique task identifier
            method: Method to execute
            inputs: Input values for the method
            metadata: Additional task metadata
        """
        self.task_id = task_id
        self.method = method
        self.inputs = inputs
        self.outputs: Dict[str, Any] = {}
        self.intermediate_outputs: Dict[str, Any] = {}
        self.status = TaskStatus.PENDING
        self.metadata = metadata or {}
        self.pipeline: Optional['Pipeline'] = None

        # Execution context
        self.work_dir: Optional[Path] = None
        self.input_dir: Optional[Path] = None
        self.output_dir: Optional[Path] = None
        self.log_dir: Optional[Path] = None
        self.log_file: Optional[Path] = None

        # SLURM integration (added in Stage 3)
        self.slurm_job_id: Optional[str] = None
        self.slurm_script_path: Optional[Path] = None

        # Timing
        self.created_at = datetime.now()
        self.started_at: Optional[datetime] = None
        self.completed_at: Optional[datetime] = None

        # Failure handling
        self.failure_policy = FailurePolicy.STOP
        self.max_retries = 0
        self.retry_count = 0
        self.error_message: Optional[str] = None

    @property
    def duration(self) -> Optional[float]:
        """Get task duration in seconds."""
        if self.started_at and self.completed_at:
            return (self.completed_at - self.started_at).total_seconds()
        return None

    def to_dict(self) -> Dict[str, Any]:
        """
        Serialize task to dictionary.

        Returns:
            Dictionary representation
        """
        return {
            'task_id': self.task_id,
            'method_name': self.method.name if self.method else None,
            'inputs': artifact_to_data(self.inputs),
            'outputs': artifact_to_data(self.outputs),
            'intermediate_outputs': artifact_to_data(self.intermediate_outputs),
            'status': self.status.name,
            'metadata': self.metadata,
            'work_dir': str(self.work_dir) if self.work_dir else None,
            'slurm_job_id': self.slurm_job_id,
            'created_at': self.created_at.isoformat(),
            'started_at': self.started_at.isoformat() if self.started_at else None,
            'completed_at': self.completed_at.isoformat() if self.completed_at else None,
            'duration': self.duration,
            'failure_policy': self.failure_policy.name,
            'retry_count': self.retry_count,
            'max_retries': self.max_retries,
            'error_message': self.error_message,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any], method: Optional['BaseMethod'] = None) -> 'Task':
        """
        Deserialize task from dictionary.

        Args:
            data: Dictionary representation
            method: Method instance (if available)

        Returns:
            Task instance
        """
        task = cls(
            task_id=data['task_id'],
            method=method,
            inputs=artifact_from_data(data['inputs']),
            metadata=data.get('metadata', {}),
        )
        task.outputs = artifact_from_data(data.get('outputs', {}))
        task.intermediate_outputs = artifact_from_data(data.get('intermediate_outputs', {}))
        task.status = TaskStatus[data['status']]
        task.slurm_job_id = data.get('slurm_job_id')
        task.retry_count = data.get('retry_count', 0)
        task.max_retries = data.get('max_retries', 0)
        task.error_message = data.get('error_message')

        if data.get('work_dir'):
            task.work_dir = Path(data['work_dir'])

        if data.get('failure_policy'):
            task.failure_policy = FailurePolicy[data['failure_policy']]

        if data.get('created_at'):
            task.created_at = datetime.fromisoformat(data['created_at'])
        if data.get('started_at'):
            task.started_at = datetime.fromisoformat(data['started_at'])
        if data.get('completed_at'):
            task.completed_at = datetime.fromisoformat(data['completed_at'])

        return task

    def update_from_slurm_job(self, job: 'SlurmJob') -> None:
        """
        Update task from SLURM job information.

        Args:
            job: SLURM job instance
        """
        from rapier.slurm import SlurmJobStatus

        # Update job ID and script path
        self.slurm_job_id = job.job_id
        self.slurm_script_path = job.script_path

        # Update timing information
        if job.start_time:
            self.started_at = job.start_time
        if job.end_time:
            self.completed_at = job.end_time

        # Map SLURM status to Task status
        status_map = {
            SlurmJobStatus.PENDING: TaskStatus.PENDING,
            SlurmJobStatus.RUNNING: TaskStatus.RUNNING,
            SlurmJobStatus.COMPLETED: TaskStatus.COMPLETED if job.succeeded else TaskStatus.FAILED,
            SlurmJobStatus.FAILED: TaskStatus.FAILED,
            SlurmJobStatus.CANCELLED: TaskStatus.FAILED,
            SlurmJobStatus.TIMEOUT: TaskStatus.FAILED,
            SlurmJobStatus.OUT_OF_MEMORY: TaskStatus.FAILED,
            SlurmJobStatus.NODE_FAIL: TaskStatus.FAILED,
        }

        if job.status in status_map:
            self.status = status_map[job.status]

        # Store exit code in metadata if failed
        if job.exit_code is not None and job.exit_code != 0:
            self.error_message = f"Job exited with code {job.exit_code}"

    def __repr__(self) -> str:
        method_name = self.method.name if self.method else "None"
        return f"Task(id={self.task_id}, method={method_name}, status={self.status.name})"
