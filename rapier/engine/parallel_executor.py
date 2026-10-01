"""
Parallel task execution management.

Manages concurrent execution of independent tasks.
"""

from concurrent.futures import ThreadPoolExecutor, Future, as_completed
from typing import List, Callable, Any, Optional
from rapier.core.task import Task


class ParallelExecutor:
    """
    Manages parallel execution of tasks.

    Uses ThreadPoolExecutor for I/O-bound coordination tasks.
    For CPU-bound work, actual parallelism happens in SLURM jobs.
    """

    def __init__(self, max_workers: Optional[int] = None):
        """
        Initialize parallel executor.

        Args:
            max_workers: Maximum number of concurrent workers (default: CPU count)
        """
        self.max_workers = max_workers
        self.executor = ThreadPoolExecutor(max_workers=max_workers)

    def submit_parallel(
        self,
        tasks: List[Task],
        task_fn: Callable[[Task], Any]
    ) -> List[Future]:
        """
        Submit tasks for parallel execution.

        Args:
            tasks: List of tasks to execute
            task_fn: Function to execute for each task

        Returns:
            List of Future objects
        """
        futures = []
        for task in tasks:
            future = self.executor.submit(task_fn, task)
            futures.append(future)

        return futures

    def wait_any(self, futures: List[Future], timeout: Optional[float] = None) -> Optional[Future]:
        """
        Wait for any future to complete.

        Args:
            futures: List of futures
            timeout: Maximum time to wait in seconds

        Returns:
            First completed future or None if timeout
        """
        try:
            completed = next(as_completed(futures, timeout=timeout))
            return completed
        except StopIteration:
            return None

    def wait_all(
        self,
        futures: List[Future],
        timeout: Optional[float] = None
    ) -> List[Any]:
        """
        Wait for all futures to complete.

        Args:
            futures: List of futures
            timeout: Maximum time to wait in seconds

        Returns:
            List of results in order of completion

        Raises:
            TimeoutError: If timeout exceeded
        """
        results = []
        for future in as_completed(futures, timeout=timeout):
            try:
                result = future.result()
                results.append(result)
            except Exception as e:
                # Store exception as result
                results.append(e)

        return results

    def get_results(self, futures: List[Future]) -> List[Any]:
        """
        Get results from completed futures.

        Args:
            futures: List of futures

        Returns:
            List of results (or exceptions)
        """
        results = []
        for future in futures:
            try:
                result = future.result(timeout=0)
                results.append(result)
            except Exception as e:
                results.append(e)

        return results

    def cancel_all(self, futures: List[Future]):
        """
        Cancel all pending futures.

        Args:
            futures: List of futures to cancel
        """
        for future in futures:
            future.cancel()

    def shutdown(self, wait: bool = True):
        """
        Shutdown the executor.

        Args:
            wait: Whether to wait for running tasks to complete
        """
        self.executor.shutdown(wait=wait)

    def __enter__(self):
        """Context manager entry."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit."""
        self.shutdown()
        return False


class TaskIsolation:
    """
    Provides task isolation for parallel execution.

    Ensures tasks don't interfere with each other by using
    separate working directories.
    """

    @staticmethod
    def create_task_workspace(task: Task, base_dir: str) -> str:
        """
        Create isolated workspace for task.

        Args:
            task: Task to create workspace for
            base_dir: Base directory for workspaces

        Returns:
            Path to task workspace
        """
        from pathlib import Path

        workspace = Path(base_dir) / task.task_id
        workspace.mkdir(parents=True, exist_ok=True)

        # Set task working directory
        task.work_dir = workspace

        # Create subdirectories
        (workspace / 'input').mkdir(exist_ok=True)
        (workspace / 'output').mkdir(exist_ok=True)
        (workspace / 'logs').mkdir(exist_ok=True)

        task.input_dir = workspace / 'input'
        task.output_dir = workspace / 'output'
        task.log_dir = workspace / 'logs'

        return str(workspace)

    @staticmethod
    def cleanup_workspace(task: Task):
        """
        Clean up task workspace.

        Args:
            task: Task whose workspace to clean
        """
        import shutil

        if task.work_dir and task.work_dir.exists():
            try:
                shutil.rmtree(task.work_dir)
            except Exception:
                pass  # Best effort cleanup
