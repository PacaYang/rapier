"""Path management for consistent file operations."""

import os
from pathlib import Path
from typing import Optional, Union


class PathManager:
    """Resolves and manages paths for all file operations."""

    def __init__(self, project_root: str, run_id: str):
        """Initialize path manager.

        Args:
            project_root: Root directory of the project
            run_id: Run identifier (e.g., 'run_20260624_120000' or '20260624_120000')
        """
        self.project_root = Path(project_root).resolve()

        # Normalize run_id
        if not run_id.startswith('run_'):
            run_id = f'run_{run_id}'
        self.run_id = run_id

        # Standard directories
        self.config_dir = self.project_root / 'config'
        self.input_dir = self.project_root / 'inputs'
        self.work_dir = self.project_root / 'work'
        self.results_dir = self.project_root / 'results'
        self.archive_dir = self.project_root / 'archive'

        # Run-specific directories
        self.run_dir = self.work_dir / run_id
        self.run_tasks_dir = self.run_dir / 'tasks'
        self.run_intermediate_dir = self.run_dir / 'intermediate'
        self.run_logs_dir = self.run_dir / 'logs'

    def get_task_dir(self, task_id: str) -> Path:
        """Get the main directory for a task.

        Args:
            task_id: Task identifier

        Returns:
            Path to task directory
        """
        return self.run_tasks_dir / task_id

    def get_task_input_dir(self, task_id: str) -> Path:
        """Get the input directory for a task.

        Args:
            task_id: Task identifier

        Returns:
            Path to task input directory
        """
        return self.get_task_dir(task_id) / 'input'

    def get_task_output_dir(self, task_id: str) -> Path:
        """Get the output directory for a task.

        Args:
            task_id: Task identifier

        Returns:
            Path to task output directory
        """
        return self.get_task_dir(task_id) / 'output'

    def get_task_log_dir(self, task_id: str) -> Path:
        """Get the log directory for a task.

        Args:
            task_id: Task identifier

        Returns:
            Path to task log directory
        """
        return self.get_task_dir(task_id) / 'logs'

    def get_task_log_file(self, task_id: str, log_type: str = 'task') -> Path:
        """Get a log file path for a task.

        Args:
            task_id: Task identifier
            log_type: Type of log (task, stdout, stderr)

        Returns:
            Path to log file
        """
        log_dir = self.get_task_log_dir(task_id)
        return log_dir / f'{log_type}.log'

    def get_task_scripts_dir(self, task_id: str) -> Path:
        """Get the scripts directory for a task.

        Args:
            task_id: Task identifier

        Returns:
            Path to task scripts directory
        """
        return self.get_task_dir(task_id) / 'scripts'

    def get_slurm_script_path(self, task_id: str) -> Path:
        """Get the SLURM script path for a task.

        Args:
            task_id: Task identifier

        Returns:
            Path to SLURM script
        """
        scripts_dir = self.get_task_scripts_dir(task_id)
        return scripts_dir / 'job.sh'

    def get_intermediate_dir(self) -> Path:
        """Get the intermediate files directory for this run.

        Returns:
            Path to intermediate directory
        """
        return self.run_intermediate_dir

    def get_state_file(self) -> Path:
        """Get the execution state file path.

        Returns:
            Path to state.json file
        """
        return self.run_dir / 'state.json'

    def resolve_input_path(self, relative_path: str) -> Path:
        """Resolve an input path from configuration.

        Supports:
        - Relative paths (resolved from input_dir)
        - Absolute paths (used as-is)
        - Environment variable substitution (${VAR})

        Args:
            relative_path: Path from configuration

        Returns:
            Resolved absolute path
        """
        # Expand environment variables
        expanded = os.path.expandvars(relative_path)

        path = Path(expanded)

        # If absolute, use as-is
        if path.is_absolute():
            return path.resolve()

        # If relative, resolve from input_dir
        return (self.input_dir / path).resolve()

    def resolve_output_path(self, relative_path: str, task_id: str) -> Path:
        """Resolve an output path for a task.

        Args:
            relative_path: Relative output path
            task_id: Task identifier

        Returns:
            Resolved path in task output directory
        """
        output_dir = self.get_task_output_dir(task_id)
        return output_dir / relative_path

    def ensure_task_structure(self, task_id: str) -> None:
        """Ensure all task directories exist.

        Args:
            task_id: Task identifier
        """
        directories = [
            self.get_task_dir(task_id),
            self.get_task_input_dir(task_id),
            self.get_task_output_dir(task_id),
            self.get_task_log_dir(task_id),
            self.get_task_scripts_dir(task_id),
        ]

        for directory in directories:
            directory.mkdir(parents=True, exist_ok=True)

    def ensure_run_structure(self) -> None:
        """Ensure all run directories exist."""
        directories = [
            self.run_dir,
            self.run_tasks_dir,
            self.run_intermediate_dir,
            self.run_logs_dir,
        ]

        for directory in directories:
            directory.mkdir(parents=True, exist_ok=True)

    def get_relative_path(self, absolute_path: Union[str, Path]) -> Optional[Path]:
        """Get path relative to project root.

        Args:
            absolute_path: Absolute path

        Returns:
            Path relative to project root, or None if not under project root
        """
        abs_path = Path(absolute_path).resolve()

        try:
            return abs_path.relative_to(self.project_root)
        except ValueError:
            # Path is not under project root
            return None

    def is_under_project(self, path: Union[str, Path]) -> bool:
        """Check if a path is under the project root.

        Args:
            path: Path to check

        Returns:
            True if path is under project root
        """
        return self.get_relative_path(path) is not None

    def list_task_dirs(self) -> list:
        """List all task directories in this run.

        Returns:
            List of task directory paths
        """
        if not self.run_tasks_dir.exists():
            return []

        return sorted([
            d for d in self.run_tasks_dir.iterdir()
            if d.is_dir()
        ])

    def get_results_dir_for_run(self) -> Path:
        """Get the results directory for this specific run.

        Returns:
            Path to results/run_<id> directory
        """
        return self.results_dir / self.run_id

    def __repr__(self) -> str:
        return f"PathManager(project={self.project_root}, run={self.run_id})"
