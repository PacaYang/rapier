"""
Failure handling and policy enforcement.

Implements different failure policies for task execution.
"""

from dataclasses import dataclass
from typing import Optional
from enum import Enum


class FailureAction(Enum):
    """Actions to take on task failure."""
    RETRY = "retry"                    # Retry the task
    CONTINUE = "continue"              # Continue pipeline, skip downstream
    STOP = "stop"                      # Stop entire pipeline
    SKIP_DOWNSTREAM = "skip_downstream"  # Skip dependent tasks


@dataclass
class FailureDecision:
    """
    Decision on how to handle a task failure.

    Attributes:
        action: Action to take
        should_retry: Whether to retry the task
        should_stop_pipeline: Whether to stop the pipeline
        mark_downstream_skipped: Whether to mark downstream tasks as skipped
        reason: Human-readable reason for the decision
    """
    action: FailureAction
    should_retry: bool = False
    should_stop_pipeline: bool = False
    mark_downstream_skipped: bool = False
    reason: str = ""


class FailureHandler:
    """
    Handles task failures according to configured policies.

    Implements different failure policies:
    - STOP: Halt entire pipeline
    - CONTINUE: Skip failed task and continue
    - RETRY: Retry task up to max attempts
    """

    def handle_failure(self, task: 'Task', error: Exception) -> FailureDecision:
        """
        Determine how to handle a task failure.

        Args:
            task: Failed task
            error: Exception that caused failure

        Returns:
            FailureDecision with action to take
        """
        from rapier.core.enums import FailurePolicy

        policy = task.failure_policy

        # Check if we should retry
        if policy == FailurePolicy.RETRY:
            if self.should_retry(task):
                return FailureDecision(
                    action=FailureAction.RETRY,
                    should_retry=True,
                    reason=f"Retrying (attempt {task.retry_count + 1}/{task.max_retries})"
                )
            else:
                # Max retries exceeded, treat as failed
                return FailureDecision(
                    action=FailureAction.STOP,
                    should_stop_pipeline=True,
                    reason=f"Max retries ({task.max_retries}) exceeded"
                )

        # Continue policy: skip this task and downstream
        elif policy == FailurePolicy.CONTINUE:
            return FailureDecision(
                action=FailureAction.CONTINUE,
                mark_downstream_skipped=True,
                reason="Continue policy: skipping failed task and downstream"
            )

        # Stop policy: halt entire pipeline
        else:  # FailurePolicy.STOP
            return FailureDecision(
                action=FailureAction.STOP,
                should_stop_pipeline=True,
                reason="Stop policy: halting pipeline on task failure"
            )

    def should_retry(self, task: 'Task') -> bool:
        """
        Check if task should be retried.

        Args:
            task: Task to check

        Returns:
            True if task should be retried
        """
        from rapier.core.enums import FailurePolicy

        if task.failure_policy != FailurePolicy.RETRY:
            return False

        return task.retry_count < task.max_retries

    def should_continue(self, task: 'Task') -> bool:
        """
        Check if pipeline should continue after task failure.

        Args:
            task: Failed task

        Returns:
            True if pipeline should continue
        """
        from rapier.core.enums import FailurePolicy

        return task.failure_policy == FailurePolicy.CONTINUE

    def should_stop_pipeline(self, task: 'Task') -> bool:
        """
        Check if pipeline should stop after task failure.

        Args:
            task: Failed task

        Returns:
            True if pipeline should stop
        """
        from rapier.core.enums import FailurePolicy

        # Stop if policy is STOP
        if task.failure_policy == FailurePolicy.STOP:
            return True

        # Stop if retry policy and max retries exceeded
        if task.failure_policy == FailurePolicy.RETRY:
            return task.retry_count >= task.max_retries

        return False

    def mark_downstream_tasks(
        self,
        task: 'Task',
        pipeline: 'Pipeline',
        action: FailureAction
    ) -> list:
        """
        Mark downstream tasks based on failure action.

        Args:
            task: Failed task
            pipeline: Pipeline containing task
            action: Failure action taken

        Returns:
            List of task IDs to skip
        """
        if action != FailureAction.CONTINUE:
            return []

        # Get all downstream tasks (recursively)
        downstream_ids = self._get_all_downstream(task.task_id, pipeline)

        return downstream_ids

    def _get_all_downstream(self, task_id: str, pipeline: 'Pipeline') -> list:
        """
        Get all downstream tasks recursively.

        Args:
            task_id: Starting task ID
            pipeline: Pipeline

        Returns:
            List of downstream task IDs
        """
        visited = set()
        to_visit = [task_id]
        downstream = []

        while to_visit:
            current = to_visit.pop()
            if current in visited:
                continue

            visited.add(current)

            # Get direct downstream
            direct_downstream = pipeline.get_downstream_tasks(current)
            for ds_id in direct_downstream:
                if ds_id not in visited:
                    downstream.append(ds_id)
                    to_visit.append(ds_id)

        return downstream

    def record_failure(self, task: 'Task', error: Exception):
        """
        Record failure information in task.

        Args:
            task: Failed task
            error: Exception that caused failure
        """
        task.error_message = str(error)

        # Store error details in metadata
        if 'failures' not in task.metadata:
            task.metadata['failures'] = []

        task.metadata['failures'].append({
            'attempt': task.retry_count + 1,
            'error': str(error),
            'error_type': type(error).__name__
        })
