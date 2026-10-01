"""
Task scheduling and dependency resolution.

Manages task execution order based on DAG dependencies.
"""

from typing import List, Set, Dict, Optional
from rapier.core.task import Task
from rapier.core.enums import TaskStatus
from rapier.core.execution_graph import ExecutionGraph


class Scheduler:
    """
    Schedules tasks based on dependency graph.

    Implements topological ordering and dependency resolution.
    """

    def __init__(self, graph: ExecutionGraph):
        """
        Initialize scheduler.

        Args:
            graph: Execution graph with task dependencies
        """
        self.graph = graph
        self.completed_tasks: Set[str] = set()
        self.failed_tasks: Set[str] = set()
        self.skipped_tasks: Set[str] = set()
        self.running_tasks: Set[str] = set()

    def get_ready_tasks(self) -> List[Task]:
        """
        Get tasks ready for execution.

        A task is ready if:
        - It's not completed, failed, skipped, or running
        - All upstream dependencies are completed

        Returns:
            List of ready tasks
        """
        ready = []

        for task in self.graph.tasks.values():
            # Skip if already processed or running
            if (task.task_id in self.completed_tasks or
                task.task_id in self.failed_tasks or
                task.task_id in self.skipped_tasks or
                task.task_id in self.running_tasks):
                continue

            # Check if all dependencies are satisfied
            if self._are_dependencies_satisfied(task):
                ready.append(task)

        return ready

    def _are_dependencies_satisfied(self, task: Task) -> bool:
        """
        Check if all dependencies for a task are satisfied.

        Args:
            task: Task to check

        Returns:
            True if all dependencies are completed
        """
        upstream_ids = self.graph.get_upstream(task.task_id)

        for upstream_id in upstream_ids:
            # Upstream must be completed
            if upstream_id not in self.completed_tasks:
                return False

        return True

    def mark_running(self, task: Task):
        """
        Mark task as running.

        Args:
            task: Task that started running
        """
        self.running_tasks.add(task.task_id)
        task.status = TaskStatus.RUNNING

    def mark_completed(self, task: Task, status: TaskStatus):
        """
        Mark task as completed and update graph.

        Args:
            task: Completed task
            status: Final task status
        """
        task.status = status

        # Remove from running
        self.running_tasks.discard(task.task_id)

        # Add to appropriate set
        if status == TaskStatus.COMPLETED:
            self.completed_tasks.add(task.task_id)
        elif status == TaskStatus.FAILED:
            self.failed_tasks.add(task.task_id)
        elif status == TaskStatus.SKIPPED:
            self.skipped_tasks.add(task.task_id)

        # Update graph
        self.update_graph(task, status)

    def update_graph(self, task: Task, status: TaskStatus):
        """
        Update graph after task completion.

        Args:
            task: Completed task
            status: Task status
        """
        # Mark in graph
        self.graph.mark_task_status(task.task_id, status)

    def mark_downstream_skipped(self, task: Task) -> List[str]:
        """
        Mark all downstream tasks as skipped.

        Args:
            task: Failed task

        Returns:
            List of skipped task IDs
        """
        skipped = []
        to_skip = list(self.graph.get_downstream(task.task_id))
        visited = set()

        while to_skip:
            task_id = to_skip.pop()

            if task_id in visited:
                continue

            visited.add(task_id)

            # Skip this task
            self.skipped_tasks.add(task_id)
            skipped.append(task_id)

            # Get task and mark as skipped
            if task_id in self.graph.tasks:
                downstream_task = self.graph.tasks[task_id]
                downstream_task.status = TaskStatus.SKIPPED

            # Add its downstream tasks
            downstream_ids = self.graph.get_downstream(task_id)
            to_skip.extend(downstream_ids)

        return skipped

    def can_continue(self, task: Task) -> bool:
        """
        Check if pipeline can continue after task failure.

        Args:
            task: Failed task

        Returns:
            True if pipeline can continue
        """
        from rapier.core.enums import FailurePolicy

        return task.failure_policy == FailurePolicy.CONTINUE

    def get_progress(self) -> Dict[str, int]:
        """
        Get execution progress statistics.

        Returns:
            Dictionary with task counts by status
        """
        total = len(self.graph.tasks)
        completed = len(self.completed_tasks)
        failed = len(self.failed_tasks)
        skipped = len(self.skipped_tasks)
        running = len(self.running_tasks)
        pending = total - completed - failed - skipped - running

        return {
            'total': total,
            'completed': completed,
            'failed': failed,
            'skipped': skipped,
            'running': running,
            'pending': pending,
            'progress_pct': (completed / total * 100) if total > 0 else 0
        }

    def is_finished(self) -> bool:
        """
        Check if all tasks are finished.

        Returns:
            True if no more tasks to execute
        """
        stats = self.get_progress()
        return stats['pending'] == 0 and stats['running'] == 0

    def has_failures(self) -> bool:
        """
        Check if any tasks have failed.

        Returns:
            True if any tasks failed
        """
        return len(self.failed_tasks) > 0

    def get_topological_order(self) -> List[str]:
        """
        Get topological ordering of tasks.

        Returns:
            List of task IDs in topological order
        """
        # Kahn's algorithm
        in_degree = {}
        for task_id in self.graph.tasks:
            in_degree[task_id] = len(self.graph.get_upstream(task_id))

        queue = [task_id for task_id, degree in in_degree.items() if degree == 0]
        result = []

        while queue:
            task_id = queue.pop(0)
            result.append(task_id)

            # Reduce in-degree for downstream tasks
            for downstream_id in self.graph.get_downstream(task_id):
                in_degree[downstream_id] -= 1
                if in_degree[downstream_id] == 0:
                    queue.append(downstream_id)

        return result

    def reset(self):
        """Reset scheduler state."""
        self.completed_tasks.clear()
        self.failed_tasks.clear()
        self.skipped_tasks.clear()
        self.running_tasks.clear()
