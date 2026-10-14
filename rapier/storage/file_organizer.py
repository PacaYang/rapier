"""File organization for staging inputs and collecting outputs."""

import os
import shutil
from pathlib import Path
from typing import List, Optional, Dict, Any

from .path_manager import PathManager
from .file_tracker import FileTracker
from .metadata import FileMetadata
from rapier.core.specs import InputSpec, OutputSpec
from rapier.artifacts import Artifact


# File size threshold for symlink vs copy (100 MB)
LARGE_FILE_THRESHOLD = 100 * 1024 * 1024


class FileOrganizer:
    """Manages file placement and movement."""

    def __init__(
        self,
        path_manager: PathManager,
        file_tracker: Optional[FileTracker] = None
    ):
        """Initialize file organizer.

        Args:
            path_manager: Path manager for resolving paths
            file_tracker: Optional file tracker for registration
        """
        self.path_manager = path_manager
        self.file_tracker = file_tracker

    def stage_inputs(
        self,
        task: Any,  # Task object
        input_spec: InputSpec,
        use_symlinks: bool = True
    ) -> Dict[str, Path]:
        """Prepare task inputs by copying or symlinking.

        Args:
            task: Task instance
            input_spec: Input specification
            use_symlinks: Whether to use symlinks for large files

        Returns:
            Dictionary mapping input names to staged paths
        """
        staged_files = {}

        # Ensure task input directory exists
        task_input_dir = self.path_manager.get_task_input_dir(task.task_id)
        task_input_dir.mkdir(parents=True, exist_ok=True)

        # Stage each input
        for input_name, input_value in task.inputs.items():
            if isinstance(input_value, Artifact):
                if not input_value.is_file:
                    continue
                input_value = input_value.path
            # Skip if input is not a file path
            if not isinstance(input_value, (str, Path)):
                continue

            source_path = Path(input_value)

            # Resolve relative paths
            if not source_path.is_absolute():
                source_path = self.path_manager.resolve_input_path(str(source_path))

            if not source_path.exists():
                raise FileNotFoundError(f"Input file not found: {source_path}")

            # Determine target path
            target_path = task_input_dir / source_path.name

            # Decide whether to symlink or copy
            if use_symlinks and self._should_symlink(source_path):
                self._create_symlink(source_path, target_path)
            else:
                self._copy_file(source_path, target_path)

            staged_files[input_name] = target_path

            # Track the file
            if self.file_tracker:
                self.file_tracker.register_file(
                    target_path,
                    category='input',
                    task_id=task.task_id,
                    description=f'Staged input: {input_name}'
                )

        return staged_files

    def collect_outputs(
        self,
        task: Any,  # Task object
        output_spec: OutputSpec
    ) -> Dict[str, Path]:
        """Gather and validate task outputs.

        Args:
            task: Task instance
            output_spec: Output specification

        Returns:
            Dictionary mapping output names to paths
        """
        collected = {}
        output_dir = self.path_manager.get_task_output_dir(task.task_id)

        if not output_dir.exists():
            return collected

        # Normalized outputs carry their exact paths and semantic port names.
        for output_name, output_value in task.outputs.items():
            values = output_value if isinstance(output_value, list) else [output_value]
            for index, value in enumerate(values):
                if not isinstance(value, Artifact) or not value.is_file or not value.path.exists():
                    continue
                key = output_name if len(values) == 1 else f"{output_name}[{index}]"
                collected[key] = value.path
                if self.file_tracker:
                    self.file_tracker.register_file(
                        value.path,
                        category='output',
                        task_id=task.task_id,
                        description=f'Normalized task output: {output_name}'
                    )
                    self.file_tracker.mark_important(value.path)

        # Collect all files in output directory
        for file_path in output_dir.rglob('*'):
            if file_path.is_file():
                rel_path = file_path.relative_to(output_dir)
                if str(rel_path) not in collected:
                    collected[str(rel_path)] = file_path

                    # Track unexpected output
                    if self.file_tracker:
                        self.file_tracker.register_file(
                            file_path,
                            category='output',
                            task_id=task.task_id,
                            description='Unexpected output file'
                        )

        return collected

    def promote_to_results(self, files: List[Path], preserve_structure: bool = True) -> List[Path]:
        """Move important files to results directory.

        Args:
            files: List of file paths to promote
            preserve_structure: Whether to preserve directory structure

        Returns:
            List of new file paths in results directory
        """
        results_dir = self.path_manager.get_results_dir_for_run()
        results_dir.mkdir(parents=True, exist_ok=True)

        promoted = []

        for file_path in files:
            file_path = Path(file_path)

            if not file_path.exists():
                continue

            if preserve_structure:
                # Try to preserve structure relative to run directory
                rel_path = self.path_manager.get_relative_path(file_path)
                if rel_path:
                    target_path = results_dir / rel_path
                else:
                    target_path = results_dir / file_path.name
            else:
                target_path = results_dir / file_path.name

            # Ensure target directory exists
            target_path.parent.mkdir(parents=True, exist_ok=True)

            # Copy file to results
            shutil.copy2(file_path, target_path)
            promoted.append(target_path)

            # Track the promoted file
            if self.file_tracker:
                self.file_tracker.register_file(
                    target_path,
                    category='output',
                    task_id=None,
                    description='Promoted to results'
                )
                self.file_tracker.mark_important(target_path)

        return promoted

    def create_symlinks(self, source: Path, target: Path) -> None:
        """Create symlink from source to target.

        Args:
            source: Source file path
            target: Target link path
        """
        source = Path(source).resolve()
        target = Path(target)

        # Ensure target directory exists
        target.parent.mkdir(parents=True, exist_ok=True)

        # Remove existing target if it exists
        if target.exists() or target.is_symlink():
            target.unlink()

        # Create symlink
        target.symlink_to(source)

    def copy_files(
        self,
        source: Path,
        target: Path,
        pattern: str = '*'
    ) -> List[Path]:
        """Copy files matching pattern from source to target.

        Args:
            source: Source directory
            target: Target directory
            pattern: Glob pattern for files to copy

        Returns:
            List of copied file paths
        """
        source = Path(source)
        target = Path(target)

        target.mkdir(parents=True, exist_ok=True)

        copied = []

        if source.is_file():
            # Source is a single file
            target_file = target / source.name
            shutil.copy2(source, target_file)
            copied.append(target_file)
        else:
            # Source is a directory
            for source_file in source.glob(pattern):
                if source_file.is_file():
                    target_file = target / source_file.name
                    shutil.copy2(source_file, target_file)
                    copied.append(target_file)

        return copied

    def _should_symlink(self, path: Path) -> bool:
        """Determine if file should be symlinked vs copied.

        Args:
            path: File path

        Returns:
            True if file should be symlinked
        """
        # Always symlink large files
        if path.stat().st_size > LARGE_FILE_THRESHOLD:
            return True

        # Check if file is under project input directory (immutable)
        if self.path_manager.is_under_project(path):
            rel_path = self.path_manager.get_relative_path(path)
            if rel_path and str(rel_path).startswith('inputs/'):
                return True

        return False

    def _create_symlink(self, source: Path, target: Path) -> None:
        """Create a symlink.

        Args:
            source: Source file
            target: Target symlink path
        """
        source = source.resolve()

        # Remove existing target
        if target.exists() or target.is_symlink():
            target.unlink()

        target.symlink_to(source)

    def _copy_file(self, source: Path, target: Path) -> None:
        """Copy a file.

        Args:
            source: Source file
            target: Target file path
        """
        # Remove existing target
        if target.exists():
            target.unlink()

        shutil.copy2(source, target)

    def validate_file_exists(self, path: Path) -> bool:
        """Check if a file exists.

        Args:
            path: File path

        Returns:
            True if file exists
        """
        return Path(path).exists()

    def get_file_size(self, path: Path) -> int:
        """Get file size in bytes.

        Args:
            path: File path

        Returns:
            File size in bytes
        """
        return Path(path).stat().st_size
