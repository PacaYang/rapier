"""Project directory structure definition and management."""

import os
from datetime import datetime
from pathlib import Path
from typing import Optional


class ProjectStructure:
    """Defines and manages the standard Rapier project directory layout."""

    def __init__(self, root: str):
        """Initialize project structure.

        Args:
            root: Project root directory
        """
        self.root = Path(root).resolve()
        self.config_dir = self.root / 'config'
        self.input_dir = self.root / 'inputs'
        self.work_dir = self.root / 'work'
        self.results_dir = self.root / 'results'
        self.archive_dir = self.root / 'archive'

    @classmethod
    def create_project(cls, root: str) -> 'ProjectStructure':
        """Create a new project with standard directory structure.

        Args:
            root: Project root directory

        Returns:
            ProjectStructure instance with directories created
        """
        structure = cls(root)

        # Create main directories
        structure.root.mkdir(parents=True, exist_ok=True)
        structure.config_dir.mkdir(exist_ok=True)
        structure.input_dir.mkdir(exist_ok=True)
        structure.work_dir.mkdir(exist_ok=True)
        structure.results_dir.mkdir(exist_ok=True)
        structure.archive_dir.mkdir(exist_ok=True)

        # Create input subdirectories
        (structure.input_dir / 'receptors').mkdir(exist_ok=True)
        (structure.input_dir / 'ligands').mkdir(exist_ok=True)

        # Create README files
        structure._create_readme_files()

        return structure

    def _create_readme_files(self) -> None:
        """Create README files in key directories."""
        readmes = {
            self.config_dir: "Pipeline configuration files (YAML)",
            self.input_dir: "Original input files (never modified)",
            self.work_dir: "Working directory for pipeline executions",
            self.results_dir: "Final curated outputs",
            self.archive_dir: "Archived/compressed runs"
        }

        for directory, description in readmes.items():
            readme_path = directory / 'README.txt'
            if not readme_path.exists():
                with open(readme_path, 'w') as f:
                    f.write(f"{description}\n")

    def create_run_directory(self, timestamp: Optional[str] = None) -> Path:
        """Create a new run directory with timestamp.

        Args:
            timestamp: Optional timestamp string (defaults to current time)

        Returns:
            Path to created run directory
        """
        if timestamp is None:
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')

        run_dir = self.work_dir / f'run_{timestamp}'
        run_dir.mkdir(parents=True, exist_ok=True)

        # Create run subdirectories
        (run_dir / 'tasks').mkdir(exist_ok=True)
        (run_dir / 'intermediate').mkdir(exist_ok=True)
        (run_dir / 'logs').mkdir(exist_ok=True)

        return run_dir

    def get_directory(self, dir_type: str) -> Path:
        """Get a directory by type.

        Args:
            dir_type: Directory type (config, input, work, results, archive)

        Returns:
            Path to requested directory

        Raises:
            ValueError: If dir_type is invalid
        """
        dir_map = {
            'config': self.config_dir,
            'input': self.input_dir,
            'inputs': self.input_dir,
            'work': self.work_dir,
            'results': self.results_dir,
            'archive': self.archive_dir
        }

        if dir_type not in dir_map:
            raise ValueError(
                f"Invalid directory type: {dir_type}. "
                f"Valid types: {', '.join(dir_map.keys())}"
            )

        return dir_map[dir_type]

    def list_runs(self) -> list:
        """List all run directories.

        Returns:
            List of run directory paths sorted by timestamp (newest first)
        """
        runs = []
        if self.work_dir.exists():
            runs = [d for d in self.work_dir.iterdir() if d.is_dir() and d.name.startswith('run_')]
            runs.sort(reverse=True)
        return runs

    def get_run_info(self, run_id: str) -> dict:
        """Get information about a specific run.

        Args:
            run_id: Run identifier (e.g., 'run_20260624_120000' or '20260624_120000')

        Returns:
            Dictionary with run information
        """
        if not run_id.startswith('run_'):
            run_id = f'run_{run_id}'

        run_dir = self.work_dir / run_id

        if not run_dir.exists():
            return {'exists': False, 'run_id': run_id}

        # Get directory size
        total_size = sum(
            f.stat().st_size
            for f in run_dir.rglob('*')
            if f.is_file()
        )

        # Count tasks
        tasks_dir = run_dir / 'tasks'
        task_count = len(list(tasks_dir.iterdir())) if tasks_dir.exists() else 0

        # Get creation time
        created = datetime.fromtimestamp(run_dir.stat().st_ctime)

        return {
            'exists': True,
            'run_id': run_id,
            'path': str(run_dir),
            'size_bytes': total_size,
            'size_mb': total_size / (1024 * 1024),
            'task_count': task_count,
            'created': created.isoformat(),
        }

    def validate_structure(self) -> tuple:
        """Validate that required directories exist.

        Returns:
            Tuple of (is_valid, missing_directories)
        """
        required = [
            self.config_dir,
            self.input_dir,
            self.work_dir,
            self.results_dir,
            self.archive_dir
        ]

        missing = [str(d) for d in required if not d.exists()]
        is_valid = len(missing) == 0

        return is_valid, missing

    def __repr__(self) -> str:
        return f"ProjectStructure(root={self.root})"
