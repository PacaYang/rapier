"""
Execution state tracking and persistence.

Enables pipeline resumption after interruption.
"""

import json
from dataclasses import dataclass, field
from typing import Dict, List, Any, Optional
from datetime import datetime
from pathlib import Path
from enum import Enum


class ExecutionPhase(Enum):
    """Pipeline execution phases."""
    INITIALIZING = "initializing"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class ExecutionState:
    """
    Tracks pipeline execution state for persistence and resumption.

    Attributes:
        pipeline_id: Unique pipeline identifier
        pipeline_name: Pipeline name
        start_time: Execution start time
        phase: Current execution phase
        tasks_pending: List of pending task IDs
        tasks_running: List of running task IDs
        tasks_completed: List of completed task IDs
        tasks_failed: List of failed task IDs
        tasks_skipped: List of skipped task IDs
        task_statuses: Detailed status for each task
        last_update: Time of last state update
    """

    pipeline_id: str
    pipeline_name: str
    start_time: datetime
    phase: ExecutionPhase = ExecutionPhase.INITIALIZING
    tasks_pending: List[str] = field(default_factory=list)
    tasks_running: List[str] = field(default_factory=list)
    tasks_completed: List[str] = field(default_factory=list)
    tasks_failed: List[str] = field(default_factory=list)
    tasks_skipped: List[str] = field(default_factory=list)
    task_statuses: Dict[str, str] = field(default_factory=dict)
    task_metadata: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    last_update: datetime = field(default_factory=datetime.now)

    @property
    def total_tasks(self) -> int:
        """Get total number of tasks."""
        return (len(self.tasks_pending) + len(self.tasks_running) +
                len(self.tasks_completed) + len(self.tasks_failed) +
                len(self.tasks_skipped))

    @property
    def is_finished(self) -> bool:
        """Check if execution is finished."""
        return self.phase in (ExecutionPhase.COMPLETED, ExecutionPhase.FAILED, ExecutionPhase.CANCELLED)

    @property
    def is_running(self) -> bool:
        """Check if execution is currently running."""
        return self.phase == ExecutionPhase.RUNNING

    def update_task_status(self, task_id: str, status: str, metadata: Optional[Dict[str, Any]] = None):
        """
        Update status of a task.

        Args:
            task_id: Task identifier
            status: New status
            metadata: Optional metadata to store
        """
        old_status = self.task_statuses.get(task_id)

        # Remove from old status list
        if old_status == 'PENDING' and task_id in self.tasks_pending:
            self.tasks_pending.remove(task_id)
        elif old_status == 'RUNNING' and task_id in self.tasks_running:
            self.tasks_running.remove(task_id)

        # Add to new status list
        self.task_statuses[task_id] = status

        if status == 'PENDING':
            if task_id not in self.tasks_pending:
                self.tasks_pending.append(task_id)
        elif status == 'RUNNING':
            if task_id not in self.tasks_running:
                self.tasks_running.append(task_id)
        elif status == 'COMPLETED':
            if task_id not in self.tasks_completed:
                self.tasks_completed.append(task_id)
        elif status == 'FAILED':
            if task_id not in self.tasks_failed:
                self.tasks_failed.append(task_id)
        elif status == 'SKIPPED':
            if task_id not in self.tasks_skipped:
                self.tasks_skipped.append(task_id)

        # Update metadata
        if metadata:
            if task_id not in self.task_metadata:
                self.task_metadata[task_id] = {}
            self.task_metadata[task_id].update(metadata)

        self.last_update = datetime.now()

    def get_progress(self) -> float:
        """
        Get execution progress as percentage.

        Returns:
            Progress percentage (0-100)
        """
        total = self.total_tasks
        if total == 0:
            return 0.0

        finished = len(self.tasks_completed) + len(self.tasks_failed) + len(self.tasks_skipped)
        return (finished / total) * 100.0

    def to_dict(self) -> Dict[str, Any]:
        """
        Serialize state to dictionary.

        Returns:
            Dictionary representation
        """
        return {
            'pipeline_id': self.pipeline_id,
            'pipeline_name': self.pipeline_name,
            'start_time': self.start_time.isoformat(),
            'phase': self.phase.value,
            'tasks_pending': self.tasks_pending,
            'tasks_running': self.tasks_running,
            'tasks_completed': self.tasks_completed,
            'tasks_failed': self.tasks_failed,
            'tasks_skipped': self.tasks_skipped,
            'task_statuses': self.task_statuses,
            'task_metadata': self.task_metadata,
            'last_update': self.last_update.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'ExecutionState':
        """
        Deserialize state from dictionary.

        Args:
            data: Dictionary representation

        Returns:
            ExecutionState instance
        """
        return cls(
            pipeline_id=data['pipeline_id'],
            pipeline_name=data['pipeline_name'],
            start_time=datetime.fromisoformat(data['start_time']),
            phase=ExecutionPhase(data['phase']),
            tasks_pending=data.get('tasks_pending', []),
            tasks_running=data.get('tasks_running', []),
            tasks_completed=data.get('tasks_completed', []),
            tasks_failed=data.get('tasks_failed', []),
            tasks_skipped=data.get('tasks_skipped', []),
            task_statuses=data.get('task_statuses', {}),
            task_metadata=data.get('task_metadata', {}),
            last_update=datetime.fromisoformat(data['last_update']),
        )

    def save(self, output_path: Path):
        """
        Save state to JSON file.

        Args:
            output_path: Path to output file
        """
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        with open(output_path, 'w') as f:
            json.dump(self.to_dict(), f, indent=2)

    @classmethod
    def load(cls, input_path: Path) -> 'ExecutionState':
        """
        Load state from JSON file.

        Args:
            input_path: Path to input file

        Returns:
            ExecutionState instance
        """
        with open(input_path, 'r') as f:
            data = json.load(f)
        return cls.from_dict(data)

    @classmethod
    def create_initial(cls, pipeline_id: str, pipeline_name: str, task_ids: List[str]) -> 'ExecutionState':
        """
        Create initial execution state for a pipeline.

        Args:
            pipeline_id: Pipeline identifier
            pipeline_name: Pipeline name
            task_ids: List of all task IDs

        Returns:
            Initial ExecutionState
        """
        state = cls(
            pipeline_id=pipeline_id,
            pipeline_name=pipeline_name,
            start_time=datetime.now(),
            phase=ExecutionPhase.INITIALIZING
        )

        # Initialize all tasks as pending
        for task_id in task_ids:
            state.update_task_status(task_id, 'PENDING')

        return state
