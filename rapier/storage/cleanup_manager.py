"""Cleanup manager for managing intermediate file cleanup."""

import os
import shutil
import tarfile
from pathlib import Path
from typing import Dict, List, Optional, Set

from .file_tracker import FileTracker
from .path_manager import PathManager


class CleanupPolicy:
    """Policy for cleaning up files."""

    def __init__(
        self,
        name: str,
        keep_inputs: bool = True,
        keep_outputs: bool = True,
        keep_logs: bool = True,
        keep_intermediates: bool = False,
        keep_scripts: bool = False
    ):
        """Initialize cleanup policy.

        Args:
            name: Policy name
            keep_inputs: Whether to keep input files
            keep_outputs: Whether to keep output files
            keep_logs: Whether to keep log files
            keep_intermediates: Whether to keep intermediate files
            keep_scripts: Whether to keep SLURM scripts
        """
        self.name = name
        self.keep_inputs = keep_inputs
        self.keep_outputs = keep_outputs
        self.keep_logs = keep_logs
        self.keep_intermediates = keep_intermediates
        self.keep_scripts = keep_scripts

    def should_keep(self, category: str) -> bool:
        """Check if files in category should be kept.

        Args:
            category: File category

        Returns:
            True if files should be kept
        """
        category_map = {
            'input': self.keep_inputs,
            'output': self.keep_outputs,
            'log': self.keep_logs,
            'intermediate': self.keep_intermediates,
            'script': self.keep_scripts,
        }
        return category_map.get(category, False)


# Predefined policies
POLICIES = {
    'minimal': CleanupPolicy(
        'minimal',
        keep_inputs=True,
        keep_outputs=True,
        keep_logs=True,
        keep_intermediates=True,
        keep_scripts=True
    ),
    'standard': CleanupPolicy(
        'standard',
        keep_inputs=True,
        keep_outputs=True,
        keep_logs=True,
        keep_intermediates=False,
        keep_scripts=False
    ),
    'aggressive': CleanupPolicy(
        'aggressive',
        keep_inputs=True,
        keep_outputs=True,
        keep_logs=False,
        keep_intermediates=False,
        keep_scripts=False
    ),
}


class CleanupManager:
    """Manages intermediate file cleanup."""

    def __init__(
        self,
        file_tracker: FileTracker,
        path_manager: Optional[PathManager] = None
    ):
        """Initialize cleanup manager.

        Args:
            file_tracker: File tracker instance
            path_manager: Optional path manager
        """
        self.file_tracker = file_tracker
        self.path_manager = path_manager
        self.marked_for_cleanup: Dict[str, str] = {}  # path -> priority
        self.protected_files: Set[str] = set()

    def mark_for_cleanup(self, path: Path, priority: str = 'normal') -> None:
        """Mark a file for cleanup.

        Args:
            path: File path
            priority: Cleanup priority (high, normal, low)
        """
        self.marked_for_cleanup[str(path)] = priority

    def mark_preserve(self, path: Path) -> None:
        """Mark a file to preserve during cleanup.

        Args:
            path: File path
        """
        self.protected_files.add(str(path))
        # Remove from cleanup list if present
        self.marked_for_cleanup.pop(str(path), None)

    def cleanup_task(
        self,
        task_id: str,
        keep_outputs: bool = True,
        dry_run: bool = False
    ) -> Dict[str, any]:
        """Clean up files for a single task.

        Args:
            task_id: Task identifier
            keep_outputs: Whether to keep output files
            dry_run: If True, don't actually delete files

        Returns:
            Dictionary with cleanup statistics
        """
        if not self.path_manager:
            raise ValueError("PathManager required for cleanup_task")

        task_dir = self.path_manager.get_task_dir(task_id)
        if not task_dir.exists():
            return {'deleted_files': 0, 'freed_bytes': 0}

        deleted_files = []
        freed_bytes = 0

        # Get files for this task
        task_files = self.file_tracker.get_files_by_task(task_id)

        for file_path in task_files:
            # Skip protected files
            if str(file_path) in self.protected_files:
                continue

            # Skip important files
            if self.file_tracker.is_important(file_path):
                continue

            # Check category
            metadata = self.file_tracker.get_metadata(file_path)
            if metadata:
                if metadata.category == 'output' and keep_outputs:
                    continue
                if metadata.category == 'input':
                    continue  # Never delete inputs

            # Delete file
            if file_path.exists():
                size = file_path.stat().st_size
                if not dry_run:
                    file_path.unlink()
                deleted_files.append(str(file_path))
                freed_bytes += size

        # Clean up empty directories
        if not dry_run:
            self._remove_empty_dirs(task_dir)

        return {
            'deleted_files': len(deleted_files),
            'freed_bytes': freed_bytes,
            'freed_mb': freed_bytes / (1024 * 1024),
            'files': deleted_files
        }

    def cleanup_run(
        self,
        run_id: str,
        strategy: str = 'standard',
        dry_run: bool = False
    ) -> Dict[str, any]:
        """Clean up an entire run based on strategy.

        Args:
            run_id: Run identifier
            strategy: Cleanup strategy (minimal, standard, aggressive)
            dry_run: If True, don't actually delete files

        Returns:
            Dictionary with cleanup statistics
        """
        if not self.path_manager:
            raise ValueError("PathManager required for cleanup_run")

        policy = POLICIES.get(strategy)
        if not policy:
            raise ValueError(f"Unknown cleanup strategy: {strategy}")

        deleted_files = []
        freed_bytes = 0

        # Get all tracked files
        all_files = self.file_tracker.get_all_tracked_files()

        for path_str, metadata in all_files.items():
            path = Path(path_str)

            # Skip if not in this run
            if not self.path_manager.is_under_project(path):
                continue

            rel_path = self.path_manager.get_relative_path(path)
            if rel_path and not str(rel_path).startswith(f'work/{run_id}'):
                continue

            # Skip protected files
            if path_str in self.protected_files:
                continue

            # Skip important files
            if self.file_tracker.is_important(path):
                continue

            # Check policy
            if not policy.should_keep(metadata.category):
                if path.exists():
                    size = path.stat().st_size
                    if not dry_run:
                        path.unlink()
                    deleted_files.append(path_str)
                    freed_bytes += size

        # Clean up empty directories
        if not dry_run and self.path_manager:
            run_dir = self.path_manager.run_dir
            if run_dir.exists():
                self._remove_empty_dirs(run_dir)

        return {
            'deleted_files': len(deleted_files),
            'freed_bytes': freed_bytes,
            'freed_mb': freed_bytes / (1024 * 1024),
            'freed_gb': freed_bytes / (1024 * 1024 * 1024),
            'files': deleted_files
        }

    def estimate_disk_usage(self, run_id: Optional[str] = None) -> Dict[str, any]:
        """Estimate disk usage by category.

        Args:
            run_id: Optional run identifier to filter by

        Returns:
            Dictionary with disk usage statistics
        """
        usage_by_category = {}
        total_size = 0

        all_files = self.file_tracker.get_all_tracked_files()

        for path_str, metadata in all_files.items():
            path = Path(path_str)

            # Filter by run if specified
            if run_id and self.path_manager:
                if not self.path_manager.is_under_project(path):
                    continue
                rel_path = self.path_manager.get_relative_path(path)
                if rel_path and not str(rel_path).startswith(f'work/{run_id}'):
                    continue

            # Get file size
            if path.exists():
                size = path.stat().st_size
                category = metadata.category

                if category not in usage_by_category:
                    usage_by_category[category] = {'count': 0, 'size_bytes': 0}

                usage_by_category[category]['count'] += 1
                usage_by_category[category]['size_bytes'] += size
                total_size += size

        # Convert to MB/GB
        for category in usage_by_category:
            size_bytes = usage_by_category[category]['size_bytes']
            usage_by_category[category]['size_mb'] = size_bytes / (1024 * 1024)
            usage_by_category[category]['size_gb'] = size_bytes / (1024 * 1024 * 1024)

        return {
            'total_bytes': total_size,
            'total_mb': total_size / (1024 * 1024),
            'total_gb': total_size / (1024 * 1024 * 1024),
            'by_category': usage_by_category
        }

    def archive_run(
        self,
        run_id: str,
        compression: str = 'gz',
        cleanup_after: bool = True
    ) -> Path:
        """Archive a run to compressed tarball.

        Args:
            run_id: Run identifier
            compression: Compression type (gz, bz2, xz)
            cleanup_after: Whether to delete run directory after archiving

        Returns:
            Path to created archive file
        """
        if not self.path_manager:
            raise ValueError("PathManager required for archive_run")

        run_dir = self.path_manager.run_dir
        if not run_dir.exists():
            raise FileNotFoundError(f"Run directory not found: {run_dir}")

        # Determine archive path
        archive_dir = self.path_manager.archive_dir
        archive_dir.mkdir(parents=True, exist_ok=True)

        archive_name = f'{run_id}_cleaned.tar.{compression}'
        archive_path = archive_dir / archive_name

        # Create tarball
        compression_mode = {
            'gz': 'gz',
            'bz2': 'bz2',
            'xz': 'xz'
        }.get(compression, 'gz')

        with tarfile.open(archive_path, f'w:{compression_mode}') as tar:
            tar.add(run_dir, arcname=run_dir.name)

        # Cleanup if requested
        if cleanup_after:
            shutil.rmtree(run_dir)

        return archive_path

    def _remove_empty_dirs(self, root: Path) -> None:
        """Recursively remove empty directories.

        Args:
            root: Root directory to start from
        """
        for dirpath, dirnames, filenames in os.walk(root, topdown=False):
            dir_path = Path(dirpath)
            if not any(dir_path.iterdir()):
                try:
                    dir_path.rmdir()
                except OSError:
                    pass  # Directory not empty or other error

    def get_cleanup_candidates(self, strategy: str = 'standard') -> List[Path]:
        """Get list of files that would be deleted under a strategy.

        Args:
            strategy: Cleanup strategy

        Returns:
            List of file paths that would be deleted
        """
        policy = POLICIES.get(strategy)
        if not policy:
            raise ValueError(f"Unknown cleanup strategy: {strategy}")

        candidates = []
        all_files = self.file_tracker.get_all_tracked_files()

        for path_str, metadata in all_files.items():
            path = Path(path_str)

            # Skip protected and important files
            if path_str in self.protected_files:
                continue
            if self.file_tracker.is_important(path):
                continue

            # Check policy
            if not policy.should_keep(metadata.category):
                if path.exists():
                    candidates.append(path)

        return candidates
