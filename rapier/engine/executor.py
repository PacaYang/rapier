"""
Main execution engine orchestrating pipeline execution.

Coordinates scheduler, task runner, failure handler, and result pooler.
"""

import time
from typing import Optional
from datetime import datetime
from pathlib import Path

from rapier.core.pipeline import Pipeline
from rapier.core.enums import TaskStatus
from rapier.engine.scheduler import Scheduler
from rapier.engine.task_runner import TaskRunner
from rapier.engine.failure_handler import FailureHandler, FailureAction
from rapier.engine.pooler import ResultPooler
from rapier.engine.parallel_executor import ParallelExecutor
from rapier.engine.state import ExecutionState, ExecutionPhase
from rapier.engine.result import ExecutionResult, TaskResult


class ExecutionEngine:
    """
    Orchestrates pipeline execution.

    Main event loop coordinating:
    - Task scheduling (Scheduler)
    - Task execution (TaskRunner)
    - Failure handling (FailureHandler)
    - Result pooling (ResultPooler)
    - Parallel execution (ParallelExecutor)
    """

    def __init__(
        self,
        max_parallel: int = 4,
        poll_interval: float = 10.0,
        dry_run: bool = False,
        state_dir: Optional[Path] = None
    ):
        """
        Initialize execution engine.

        Args:
            max_parallel: Maximum parallel tasks
            poll_interval: Seconds between status checks
            dry_run: If True, don't actually submit jobs
            state_dir: Directory for state persistence
        """
        self.max_parallel = max_parallel
        self.poll_interval = poll_interval
        self.dry_run = dry_run
        self.state_dir = Path(state_dir) if state_dir else Path.cwd() / '.rapier_state'

        # Components
        self.scheduler: Optional[Scheduler] = None
        self.runner = TaskRunner(dry_run=dry_run)
        self.failure_handler = FailureHandler()
        self.pooler = ResultPooler()
        self.parallel_executor = ParallelExecutor(max_workers=max_parallel)

        # State
        self.pipeline: Optional[Pipeline] = None
        self.state: Optional[ExecutionState] = None
        self.result: Optional[ExecutionResult] = None

        # Control
        self._paused = False
        self._cancelled = False

    def execute(
        self,
        pipeline: Pipeline,
        resume: bool = False,
        save_state: bool = True
    ) -> ExecutionResult:
        """
        Execute a pipeline.

        Args:
            pipeline: Pipeline to execute
            resume: Whether to resume from previous state
            save_state: Whether to save state periodically

        Returns:
            ExecutionResult with pipeline outcome
        """
        self.pipeline = pipeline

        # Initialize or resume state
        if resume:
            self.state = self._load_state(pipeline)
        else:
            self.state = ExecutionState.create_initial(
                pipeline_id=pipeline.name,
                pipeline_name=pipeline.name,
                task_ids=list(pipeline.tasks.keys())
            )

        # Create scheduler
        graph = pipeline.to_execution_graph()
        self.scheduler = Scheduler(graph)

        # Initialize result
        self.result = ExecutionResult(
            pipeline_name=pipeline.name,
            success=False,
            start_time=self.state.start_time
        )

        # Start execution
        self.start()

        # Main execution loop
        while not self.scheduler.is_finished() and not self._cancelled:
            if self._paused:
                time.sleep(1)
                continue

            # Execute one step
            self.step()

            # Save state periodically
            if save_state:
                self._save_state()

            # Wait before next poll
            time.sleep(self.poll_interval)

        # Finalize
        self._finalize()

        return self.result

    def start(self):
        """Initialize execution."""
        if self.state:
            self.state.phase = ExecutionPhase.RUNNING

    def step(self):
        """Execute one round of ready tasks."""
        # Get ready tasks
        ready_tasks = self.scheduler.get_ready_tasks()

        if not ready_tasks:
            return

        # Limit by max parallel
        to_execute = ready_tasks[:self.max_parallel - len(self.scheduler.running_tasks)]

        # Execute tasks
        for task in to_execute:
            self._execute_task(task)

        # Check status of running tasks
        self._check_running_tasks()

    def _execute_task(self, task):
        """
        Execute a single task.

        Args:
            task: Task to execute
        """
        print(f"Executing task: {task.task_id}")

        try:
            # Mark as running
            self.scheduler.mark_running(task)
            self.state.update_task_status(task.task_id, 'RUNNING')

            # Prepare and submit
            self.runner.prepare_task(task)
            job_id = self.runner.submit_task(task)

            print(f"  Submitted job: {job_id}")

        except Exception as e:
            print(f"  Failed to submit: {e}")
            self._handle_task_failure(task, e)

    def _check_running_tasks(self):
        """Check status of running tasks and handle completion."""
        for task_id in list(self.scheduler.running_tasks):
            task = self.pipeline.tasks[task_id]

            # Get current status
            status = self.runner.get_task_status(task)

            # Check if finished
            if status in (TaskStatus.COMPLETED, TaskStatus.FAILED):
                self._handle_task_completion(task, status)

    def _handle_task_completion(self, task, status):
        """
        Handle task completion.

        Args:
            task: Completed task
            status: Final task status
        """
        print(f"Task {task.task_id} finished: {status.name}")

        # Collect results
        if status == TaskStatus.COMPLETED:
            try:
                self.runner.collect_task_results(task)
            except Exception as e:
                print(f"  Failed to collect results: {e}")

        # Mark completed
        self.scheduler.mark_completed(task, status)
        self.state.update_task_status(task.task_id, status.name)

        # Handle failure
        if status == TaskStatus.FAILED:
            self._handle_task_failure(task, Exception(task.error_message or "Unknown error"))
        else:
            # Record success
            self._record_task_result(task, status)

    def _handle_task_failure(self, task, error: Exception):
        """
        Handle task failure.

        Args:
            task: Failed task
            error: Exception that caused failure
        """
        print(f"Handling failure for task {task.task_id}: {error}")

        # Record failure
        self.failure_handler.record_failure(task, error)

        # Get failure decision
        decision = self.failure_handler.handle_failure(task, error)
        print(f"  Decision: {decision.reason}")

        # Execute decision
        if decision.should_retry:
            # Retry task
            task.retry_count += 1
            task.status = TaskStatus.PENDING
            self.scheduler.running_tasks.discard(task.task_id)

        elif decision.should_stop_pipeline:
            # Stop pipeline
            print("  Stopping pipeline")
            self._cancelled = True

        elif decision.mark_downstream_skipped:
            # Skip downstream tasks
            skipped = self.scheduler.mark_downstream_skipped(task)
            print(f"  Skipped {len(skipped)} downstream tasks")

            for task_id in skipped:
                self.state.update_task_status(task_id, 'SKIPPED')

        # Record failure result
        self._record_task_result(task, TaskStatus.FAILED)

    def _record_task_result(self, task, status):
        """
        Record task result.

        Args:
            task: Task to record
            status: Task status
        """
        result = TaskResult(
            task_id=task.task_id,
            status=status.name,
            outputs=task.outputs,
            error=task.error_message,
            duration=task.duration,
            retries=task.retry_count
        )

        self.result.task_results[task.task_id] = result

        if status == TaskStatus.COMPLETED:
            self.result.completed_tasks.append(task.task_id)
        elif status == TaskStatus.FAILED:
            self.result.failed_tasks.append(task.task_id)
        elif status == TaskStatus.SKIPPED:
            self.result.skipped_tasks.append(task.task_id)

    def _finalize(self):
        """Finalize execution and prepare result."""
        print("\nFinalizing execution...")

        # Update result
        self.result.end_time = datetime.now()
        self.result.success = (
            len(self.result.failed_tasks) == 0 and
            not self._cancelled
        )
        self.result.pipeline_outputs = self._resolve_pipeline_outputs()

        # Update state
        if self.state:
            if self.result.success:
                self.state.phase = ExecutionPhase.COMPLETED
            elif self._cancelled:
                self.state.phase = ExecutionPhase.CANCELLED
            else:
                self.state.phase = ExecutionPhase.FAILED

        # Save final state
        self._save_state()

        # Print report
        print(self.result.generate_report())

    def _resolve_pipeline_outputs(self):
        """Resolve configured exported output names from completed tasks."""
        exported = {}
        if not self.pipeline:
            return exported
        for name, reference in self.pipeline.output_bindings.items():
            task_id, separator, port_name = reference.partition('.')
            if not separator or task_id not in self.pipeline.tasks:
                continue
            task = self.pipeline.tasks[task_id]
            if port_name in task.outputs:
                exported[name] = task.outputs[port_name]
        return exported

    def _save_state(self):
        """Save current execution state."""
        if not self.state:
            return

        self.state_dir.mkdir(parents=True, exist_ok=True)
        state_file = self.state_dir / f'{self.pipeline.name}_state.json'
        self.state.save(state_file)

    def _load_state(self, pipeline: Pipeline) -> ExecutionState:
        """
        Load execution state.

        Args:
            pipeline: Pipeline to load state for

        Returns:
            ExecutionState
        """
        state_file = self.state_dir / f'{pipeline.name}_state.json'

        if state_file.exists():
            return ExecutionState.load(state_file)
        else:
            # Create new state
            return ExecutionState.create_initial(
                pipeline_id=pipeline.name,
                pipeline_name=pipeline.name,
                task_ids=list(pipeline.tasks.keys())
            )

    def pause(self):
        """Pause execution."""
        self._paused = True
        if self.state:
            self.state.phase = ExecutionPhase.PAUSED

    def resume(self):
        """Resume execution."""
        self._paused = False
        if self.state:
            self.state.phase = ExecutionPhase.RUNNING

    def cancel(self):
        """Cancel execution."""
        self._cancelled = True

        # Cancel running tasks
        for task_id in list(self.scheduler.running_tasks):
            task = self.pipeline.tasks[task_id]
            self.runner.cancel_task(task)

    def wait_for_completion(self, timeout: Optional[float] = None) -> bool:
        """
        Wait for pipeline to complete.

        Args:
            timeout: Maximum time to wait in seconds

        Returns:
            True if completed, False if timeout
        """
        start_time = time.time()

        while not self.scheduler.is_finished():
            if timeout and (time.time() - start_time) > timeout:
                return False

            time.sleep(1)

        return True
