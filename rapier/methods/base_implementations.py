"""Base implementations for common method patterns.

This module provides abstract base classes and mixins for different types of
methods, making it easier to implement concrete screening algorithms.
"""

from abc import abstractmethod
from typing import Dict, Any, List, Optional
from pathlib import Path
import json

from ..core.base_method import BaseMethod
from ..core.task import Task
from ..core.enums import TaskStatus


class SingleStepMethod(BaseMethod):
    """Abstract base class for methods that execute in a single step.

    Single-step methods are the simplest type - they take inputs, perform
    one operation, and produce outputs. Examples include filtering molecules
    by properties or computing molecular descriptors.

    Subclasses must implement:
        - execute(task: Task) -> TaskStatus
    """

    def __init__(self, name: str, version: str, parameters: Optional[Dict[str, Any]] = None):
        super().__init__(name=name, version=version, parameters=parameters or {})
        self.steps = []  # Single-step methods have no sub-steps

    @abstractmethod
    def execute(self, task: Task) -> TaskStatus:
        """Execute the method logic.

        Args:
            task: The task to execute

        Returns:
            TaskStatus indicating completion or failure
        """
        pass


class MultiStepMethod(BaseMethod):
    """Abstract base class for methods that execute in multiple sequential steps.

    Multi-step methods break complex operations into stages, where each step
    has its own inputs, outputs, and SLURM job. Each step must complete before
    the next can begin. Examples include docking (receptor prep → docking → scoring).

    Subclasses should:
        - Define self.steps in __init__ with MethodStep instances
        - Implement execute_step(task: Task, step_index: int) -> TaskStatus
    """

    def __init__(self, name: str, version: str, parameters: Optional[Dict[str, Any]] = None):
        super().__init__(name=name, version=version, parameters=parameters or {})
        self.steps: List['MethodStep'] = []

    def execute(self, task: Task) -> TaskStatus:
        """Execute all steps sequentially.

        Args:
            task: The task to execute

        Returns:
            TaskStatus.COMPLETED if all steps succeed, TaskStatus.FAILED otherwise
        """
        for step_idx, step in enumerate(self.steps):
            status = self.execute_step(task, step_idx)
            if status != TaskStatus.COMPLETED:
                return status
        return TaskStatus.COMPLETED

    @abstractmethod
    def execute_step(self, task: Task, step_index: int) -> TaskStatus:
        """Execute a specific step.

        Args:
            task: The task to execute
            step_index: Index of the step to execute

        Returns:
            TaskStatus indicating step completion or failure
        """
        pass


class MethodStep:
    """Represents a single step in a multi-step method.

    Each step has a name, input/output specifications, and resource requirements.
    """

    def __init__(
        self,
        name: str,
        description: str,
        input_spec: Optional[Dict[str, Any]] = None,
        output_spec: Optional[Dict[str, Any]] = None,
        resource_spec: Optional[Dict[str, Any]] = None
    ):
        self.name = name
        self.description = description
        self.input_spec = input_spec or {}
        self.output_spec = output_spec or {}
        self.resource_spec = resource_spec or {}


class ParallelizableMethod:
    """Mixin for methods that can be parallelized across input data.

    Parallelizable methods are embarrassingly parallel - they can process
    subsets of inputs independently. The platform can split the input and
    distribute work across SLURM array jobs.

    Example: Docking 10,000 ligands can be split into 100 jobs of 100 ligands each.
    """

    def can_parallelize(self) -> bool:
        """Check if this method supports parallelization."""
        return True

    def split_inputs(self, inputs: Dict[str, Any], num_chunks: int) -> List[Dict[str, Any]]:
        """Split inputs into chunks for parallel execution.

        Args:
            inputs: The full input specification
            num_chunks: Number of parallel chunks to create

        Returns:
            List of input specifications, one per chunk
        """
        # Default implementation - subclasses should override
        # This is a naive implementation that just duplicates inputs
        return [inputs for _ in range(num_chunks)]

    def merge_outputs(self, chunk_outputs: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Merge outputs from parallel chunks into a single result.

        Args:
            chunk_outputs: List of outputs from each parallel chunk

        Returns:
            Merged output specification
        """
        # Default implementation - subclasses should override
        return chunk_outputs[0] if chunk_outputs else {}


class CheckpointableMethod:
    """Mixin for methods that support checkpointing for fault tolerance.

    Checkpointable methods can save their state periodically and resume from
    the last checkpoint after failures or time limits. Critical for long-running
    methods (hours to days).
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.checkpoint_interval = kwargs.get('checkpoint_interval', 600)  # seconds

    def save_checkpoint(self, task: Task, checkpoint_data: Dict[str, Any]) -> Path:
        """Save a checkpoint for this task.

        Args:
            task: The task being executed
            checkpoint_data: State to save

        Returns:
            Path to the checkpoint file
        """
        checkpoint_dir = Path(task.work_dir) / 'checkpoints'
        checkpoint_dir.mkdir(parents=True, exist_ok=True)

        checkpoint_file = checkpoint_dir / f'{task.task_id}_checkpoint.json'
        with open(checkpoint_file, 'w') as f:
            json.dump(checkpoint_data, f, indent=2)

        return checkpoint_file

    def load_checkpoint(self, task: Task) -> Optional[Dict[str, Any]]:
        """Load the most recent checkpoint for this task.

        Args:
            task: The task being executed

        Returns:
            Checkpoint data if found, None otherwise
        """
        checkpoint_file = Path(task.work_dir) / 'checkpoints' / f'{task.task_id}_checkpoint.json'

        if checkpoint_file.exists():
            with open(checkpoint_file) as f:
                return json.load(f)
        return None

    def should_checkpoint(self, elapsed_time: float) -> bool:
        """Determine if a checkpoint should be saved based on elapsed time.

        Args:
            elapsed_time: Seconds since last checkpoint

        Returns:
            True if a checkpoint should be saved
        """
        return elapsed_time >= self.checkpoint_interval
