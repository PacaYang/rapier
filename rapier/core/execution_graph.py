"""
Execution graph for DAG representation and dependency management.
"""

from typing import Dict, List, Set, Optional
import networkx as nx

from rapier.core.task import Task
from rapier.core.enums import TaskStatus
from rapier.core.exceptions import DependencyError


class ExecutionGraph:
    """
    Directed Acyclic Graph (DAG) representation of pipeline execution.

    Uses NetworkX for graph operations like topological sorting and cycle detection.
    """

    def __init__(self):
        """Initialize execution graph."""
        self.graph = nx.DiGraph()
        self.tasks: Dict[str, Task] = {}

    def add_node(self, task: Task) -> None:
        """
        Add a task node to the graph.

        Args:
            task: Task to add

        Raises:
            DependencyError: If task already exists
        """
        if task.task_id in self.graph:
            raise DependencyError(f"Task '{task.task_id}' already exists in graph")

        self.graph.add_node(task.task_id)
        self.tasks[task.task_id] = task

    def add_edge(self, from_task_id: str, to_task_id: str) -> None:
        """
        Add a dependency edge between tasks.

        Args:
            from_task_id: Source task ID
            to_task_id: Destination task ID (depends on source)

        Raises:
            DependencyError: If tasks don't exist or cycle would be created
        """
        if from_task_id not in self.graph:
            raise DependencyError(f"Task '{from_task_id}' not in graph")
        if to_task_id not in self.graph:
            raise DependencyError(f"Task '{to_task_id}' not in graph")

        # Add edge
        self.graph.add_edge(from_task_id, to_task_id)

        # Check for cycles
        if not nx.is_directed_acyclic_graph(self.graph):
            # Remove the edge that created the cycle
            self.graph.remove_edge(from_task_id, to_task_id)
            raise DependencyError(
                f"Adding edge {from_task_id} -> {to_task_id} would create a cycle"
            )

    def get_ready_tasks(self) -> List[Task]:
        """
        Get tasks that are ready to execute (all dependencies satisfied).

        Returns:
            List of tasks with status PENDING and all upstream tasks completed
        """
        ready = []

        for task_id in self.graph.nodes():
            task = self.tasks[task_id]

            # Skip if not pending
            if task.status != TaskStatus.PENDING:
                continue

            # Check if all upstream tasks are completed
            upstream_ids = self.get_upstream_tasks(task_id)
            if not upstream_ids:
                # No dependencies, ready to run
                ready.append(task)
            else:
                # Check all upstream tasks are completed
                all_completed = all(
                    self.tasks[up_id].status == TaskStatus.COMPLETED
                    for up_id in upstream_ids
                )
                if all_completed:
                    ready.append(task)

        return ready

    def mark_complete(self, task_id: str, status: TaskStatus) -> None:
        """
        Mark a task as complete with the given status.

        Args:
            task_id: Task ID
            status: Final status (COMPLETED, FAILED, SKIPPED, etc.)
        """
        if task_id not in self.tasks:
            raise DependencyError(f"Task '{task_id}' not in graph")

        self.tasks[task_id].status = status

    def get_upstream_tasks(self, task_id: str) -> List[str]:
        """
        Get all direct upstream tasks (predecessors).

        Args:
            task_id: Task ID

        Returns:
            List of upstream task IDs
        """
        return list(self.graph.predecessors(task_id))

    def get_downstream_tasks(self, task_id: str) -> List[str]:
        """
        Get all direct downstream tasks (successors).

        Args:
            task_id: Task ID

        Returns:
            List of downstream task IDs
        """
        return list(self.graph.successors(task_id))

    def get_all_downstream_tasks(self, task_id: str) -> Set[str]:
        """
        Get all downstream tasks recursively.

        Args:
            task_id: Task ID

        Returns:
            Set of all downstream task IDs
        """
        return nx.descendants(self.graph, task_id)

    def get_topological_order(self) -> List[str]:
        """
        Get tasks in topological order.

        Returns:
            List of task IDs in dependency order

        Raises:
            DependencyError: If graph contains cycles
        """
        try:
            return list(nx.topological_sort(self.graph))
        except nx.NetworkXError as e:
            raise DependencyError(f"Cannot sort graph: {e}")

    def mark_task_status(self, task_id: str, status: TaskStatus) -> None:
        """
        Update task status in the graph.

        Args:
            task_id: Task ID or Task instance
            status: New status
        """
        # Support both task_id (string) and Task object
        if isinstance(task_id, str):
            if task_id in self.tasks:
                self.tasks[task_id].status = status
        else:
            # Assume it's a Task object
            task_id.status = status

    # Alias methods for compatibility with scheduler
    def get_upstream(self, task_id: str) -> List[str]:
        """Alias for get_upstream_tasks."""
        return self.get_upstream_tasks(task_id)

    def get_downstream(self, task_id: str) -> List[str]:
        """Alias for get_downstream_tasks."""
        return self.get_downstream_tasks(task_id)

    def is_complete(self) -> bool:
        """
        Check if all tasks in the graph have completed (succeeded or failed).

        Returns:
            True if all tasks are in a terminal state
        """
        terminal_states = {
            TaskStatus.COMPLETED,
            TaskStatus.FAILED,
            TaskStatus.CANCELLED,
            TaskStatus.SKIPPED,
        }
        return all(task.status in terminal_states for task in self.tasks.values())

    @classmethod
    def from_pipeline(cls, pipeline: 'Pipeline') -> 'ExecutionGraph':
        """
        Create execution graph from pipeline.

        Args:
            pipeline: Pipeline instance

        Returns:
            ExecutionGraph instance
        """
        graph = cls()

        # Add all tasks as nodes
        for task in pipeline.tasks.values():
            graph.add_node(task)

        # Add all connections as edges
        for from_id, to_id in pipeline.connections:
            graph.add_edge(from_id, to_id)

        return graph

    def __repr__(self) -> str:
        return f"ExecutionGraph(nodes={len(self.graph.nodes())}, edges={len(self.graph.edges())})"
