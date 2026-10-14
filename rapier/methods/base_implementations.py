"""Optional capabilities that methods can mix in alongside :class:`~rapier.core.method.Method`."""

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..core.task import Task


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
        """Split inputs into chunks for parallel execution (default: no split)."""
        return [inputs for _ in range(num_chunks)]

    def merge_outputs(self, chunk_outputs: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Merge outputs from parallel chunks into a single result (default: first chunk)."""
        return chunk_outputs[0] if chunk_outputs else {}


class CheckpointableMethod:
    """Mixin for methods that support checkpointing for fault tolerance.

    Checkpointable methods can save their state periodically and resume from
    the last checkpoint after failures or time limits. Critical for long-running
    methods (hours to days).
    """

    checkpoint_interval = 600  # seconds

    def save_checkpoint(self, task: Task, checkpoint_data: Dict[str, Any]) -> Path:
        """Save a checkpoint for this task and return its path."""
        checkpoint_dir = Path(task.work_dir) / 'checkpoints'
        checkpoint_dir.mkdir(parents=True, exist_ok=True)

        checkpoint_file = checkpoint_dir / f'{task.task_id}_checkpoint.json'
        with open(checkpoint_file, 'w') as f:
            json.dump(checkpoint_data, f, indent=2)

        return checkpoint_file

    def load_checkpoint(self, task: Task) -> Optional[Dict[str, Any]]:
        """Load the most recent checkpoint for this task, if any."""
        checkpoint_file = Path(task.work_dir) / 'checkpoints' / f'{task.task_id}_checkpoint.json'

        if checkpoint_file.exists():
            with open(checkpoint_file) as f:
                return json.load(f)
        return None

    def should_checkpoint(self, elapsed_time: float) -> bool:
        """Determine if a checkpoint should be saved based on elapsed time."""
        return elapsed_time >= self.checkpoint_interval
