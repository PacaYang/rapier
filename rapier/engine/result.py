"""
Execution result representation.

Represents the outcome of a pipeline execution.
"""

from dataclasses import dataclass, field
from typing import Dict, List, Any, Optional
from datetime import datetime
from pathlib import Path
from rapier.artifacts import artifact_from_data, artifact_to_data


@dataclass
class TaskResult:
    """
    Result of a single task execution.

    Attributes:
        task_id: Task identifier
        status: Final task status
        outputs: Task outputs
        error: Error message if failed
        duration: Execution duration in seconds
        retries: Number of retries attempted
    """
    task_id: str
    status: str
    outputs: Dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None
    duration: Optional[float] = None
    retries: int = 0


@dataclass
class ExecutionResult:
    """
    Complete pipeline execution result.

    Attributes:
        pipeline_name: Name of executed pipeline
        success: Whether pipeline completed successfully
        start_time: Execution start time
        end_time: Execution end time
        completed_tasks: List of successfully completed tasks
        failed_tasks: List of failed tasks
        skipped_tasks: List of skipped tasks
        task_results: Detailed results for each task
    """
    pipeline_name: str
    success: bool
    start_time: datetime
    end_time: Optional[datetime] = None
    completed_tasks: List[str] = field(default_factory=list)
    failed_tasks: List[str] = field(default_factory=list)
    skipped_tasks: List[str] = field(default_factory=list)
    task_results: Dict[str, TaskResult] = field(default_factory=dict)
    pipeline_outputs: Dict[str, Any] = field(default_factory=dict)

    @property
    def duration(self) -> Optional[float]:
        """Get execution duration in seconds."""
        if self.end_time and self.start_time:
            return (self.end_time - self.start_time).total_seconds()
        return None

    @property
    def total_tasks(self) -> int:
        """Get total number of tasks."""
        return len(self.completed_tasks) + len(self.failed_tasks) + len(self.skipped_tasks)

    def get_task_result(self, task_id: str) -> Optional[TaskResult]:
        """
        Get result for a specific task.

        Args:
            task_id: Task identifier

        Returns:
            TaskResult or None if not found
        """
        return self.task_results.get(task_id)

    def get_final_outputs(self) -> Dict[str, Any]:
        """
        Get outputs from terminal (leaf) tasks.

        Returns:
            Dictionary mapping task_id to outputs
        """
        if self.pipeline_outputs:
            return self.pipeline_outputs
        return {
            task_id: result.outputs
            for task_id, result in self.task_results.items()
            if result.status == 'COMPLETED'
        }

    def generate_report(self) -> str:
        """
        Generate human-readable execution report.

        Returns:
            Formatted report string
        """
        lines = []
        lines.append("=" * 70)
        lines.append(f"Pipeline Execution Report: {self.pipeline_name}")
        lines.append("=" * 70)
        lines.append("")

        # Summary
        lines.append("Summary:")
        lines.append(f"  Status: {'SUCCESS' if self.success else 'FAILED'}")
        lines.append(f"  Duration: {self.duration:.2f} seconds" if self.duration else "  Duration: N/A")
        lines.append(f"  Total Tasks: {self.total_tasks}")
        lines.append(f"    Completed: {len(self.completed_tasks)}")
        lines.append(f"    Failed: {len(self.failed_tasks)}")
        lines.append(f"    Skipped: {len(self.skipped_tasks)}")
        lines.append("")

        # Completed tasks
        if self.completed_tasks:
            lines.append("Completed Tasks:")
            for task_id in self.completed_tasks:
                result = self.task_results.get(task_id)
                if result:
                    duration_str = f"{result.duration:.2f}s" if result.duration else "N/A"
                    retries_str = f" ({result.retries} retries)" if result.retries > 0 else ""
                    lines.append(f"  ✓ {task_id} - {duration_str}{retries_str}")
            lines.append("")

        # Failed tasks
        if self.failed_tasks:
            lines.append("Failed Tasks:")
            for task_id in self.failed_tasks:
                result = self.task_results.get(task_id)
                if result:
                    error_str = result.error[:50] if result.error else "Unknown error"
                    lines.append(f"  ✗ {task_id} - {error_str}")
            lines.append("")

        # Skipped tasks
        if self.skipped_tasks:
            lines.append("Skipped Tasks:")
            for task_id in self.skipped_tasks:
                lines.append(f"  ○ {task_id}")
            lines.append("")

        lines.append("=" * 70)
        return "\n".join(lines)

    def to_dict(self) -> Dict[str, Any]:
        """
        Serialize result to dictionary.

        Returns:
            Dictionary representation
        """
        return {
            'pipeline_name': self.pipeline_name,
            'success': self.success,
            'start_time': self.start_time.isoformat(),
            'end_time': self.end_time.isoformat() if self.end_time else None,
            'duration': self.duration,
            'completed_tasks': self.completed_tasks,
            'failed_tasks': self.failed_tasks,
            'skipped_tasks': self.skipped_tasks,
            'pipeline_outputs': artifact_to_data(self.pipeline_outputs),
            'task_results': {
                task_id: {
                    'task_id': result.task_id,
                    'status': result.status,
                    'outputs': artifact_to_data(result.outputs),
                    'error': result.error,
                    'duration': result.duration,
                    'retries': result.retries
                }
                for task_id, result in self.task_results.items()
            }
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'ExecutionResult':
        """
        Deserialize result from dictionary.

        Args:
            data: Dictionary representation

        Returns:
            ExecutionResult instance
        """
        task_results = {}
        for task_id, result_data in data.get('task_results', {}).items():
            restored = dict(result_data)
            restored['outputs'] = artifact_from_data(restored.get('outputs', {}))
            task_results[task_id] = TaskResult(**restored)

        return cls(
            pipeline_name=data['pipeline_name'],
            success=data['success'],
            start_time=datetime.fromisoformat(data['start_time']),
            end_time=datetime.fromisoformat(data['end_time']) if data.get('end_time') else None,
            completed_tasks=data.get('completed_tasks', []),
            failed_tasks=data.get('failed_tasks', []),
            skipped_tasks=data.get('skipped_tasks', []),
            task_results=task_results,
            pipeline_outputs=artifact_from_data(data.get('pipeline_outputs', {})),
        )

    def save(self, output_path: Path):
        """
        Save result to JSON file.

        Args:
            output_path: Path to output file
        """
        import json
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        with open(output_path, 'w') as f:
            json.dump(self.to_dict(), f, indent=2)

    @classmethod
    def load(cls, input_path: Path) -> 'ExecutionResult':
        """
        Load result from JSON file.

        Args:
            input_path: Path to input file

        Returns:
            ExecutionResult instance
        """
        import json
        with open(input_path, 'r') as f:
            data = json.load(f)
        return cls.from_dict(data)
