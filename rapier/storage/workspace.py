"""High-level workspace management interface."""

from pathlib import Path
from typing import Any, Dict, Optional

from .structure import ProjectStructure
from .path_manager import PathManager
from .file_organizer import FileOrganizer, InputSpec, OutputSpec
from .file_tracker import FileTracker
from .cleanup_manager import CleanupManager


class Workspace:
    """High-level workspace management combining all storage components."""

    def __init__(self):
        """Initialize workspace."""
        self.structure: Optional[ProjectStructure] = None
        self.path_manager: Optional[PathManager] = None
        self.file_tracker: FileTracker = FileTracker()
        self.file_organizer: Optional[FileOrganizer] = None
        self.cleanup_manager: Optional[CleanupManager] = None

        self.initialized = False

    def initialize(self, root: str, run_id: str) -> None:
        """Initialize workspace for a pipeline run.

        Args:
            root: Project root directory
            run_id: Run identifier
        """
        # Create or load project structure
        root_path = Path(root)
        if not root_path.exists() or not (root_path / 'config').exists():
            self.structure = ProjectStructure.create_project(root)
        else:
            self.structure = ProjectStructure(root)

        # Create run directory
        if not run_id.startswith('run_'):
            run_id = f'run_{run_id}'

        run_dir = self.structure.work_dir / run_id
        if not run_dir.exists():
            self.structure.create_run_directory(run_id.replace('run_', ''))

        # Initialize path manager
        self.path_manager = PathManager(project_root=root, run_id=run_id)
        self.path_manager.ensure_run_structure()

        # Initialize file organizer
        self.file_organizer = FileOrganizer(
            path_manager=self.path_manager,
            file_tracker=self.file_tracker
        )

        # Initialize cleanup manager
        self.cleanup_manager = CleanupManager(
            file_tracker=self.file_tracker,
            path_manager=self.path_manager
        )

        self.initialized = True

    def prepare_task_workspace(
        self,
        task: Any,  # Task object
        input_spec: Optional[InputSpec] = None
    ) -> Dict[str, Path]:
        """Prepare workspace for a task.

        Args:
            task: Task instance
            input_spec: Optional input specification

        Returns:
            Dictionary of staged input files
        """
        if not self.initialized:
            raise RuntimeError("Workspace not initialized. Call initialize() first.")

        # Ensure task directory structure
        self.path_manager.ensure_task_structure(task.task_id)

        # Set task directory properties
        task.work_dir = self.path_manager.get_task_dir(task.task_id)
        task.input_dir = self.path_manager.get_task_input_dir(task.task_id)
        task.output_dir = self.path_manager.get_task_output_dir(task.task_id)
        task.log_dir = self.path_manager.get_task_log_dir(task.task_id)

        # Stage inputs if spec provided
        if input_spec:
            staged_files = self.file_organizer.stage_inputs(task, input_spec)
            return staged_files

        return

    def finalize_task_workspace(
        self,
        task: Any,  # Task object
        output_spec: Optional[OutputSpec] = None
    ) -> Dict[str, Path]:
        """Finalize workspace after task completion.

        Args:
            task: Task instance
            output_spec: Optional output specification

        Returns:
            Dictionary of collected output files
        """
        if not self.initialized:
            raise RuntimeError("Workspace not initialized")

        # Collect outputs if spec provided
        if output_spec:
            outputs = self.file_organizer.collect_outputs(task, output_spec)
            return outputs

        return {}

    def get_disk_usage(self) -> Dict[str, any]:
        """Get disk usage statistics for this workspace.

        Returns:
            Dictionary with disk usage statistics
        """
        if not self.initialized:
            raise RuntimeError("Workspace not initialized")

        return self.cleanup_manager.estimate_disk_usage(
            run_id=self.path_manager.run_id
        )

    def cleanup(self, strategy: str = 'standard', dry_run: bool = False) -> Dict[str, any]:
        """Clean up workspace files based on strategy.

        Args:
            strategy: Cleanup strategy (minimal, standard, aggressive)
            dry_run: If True, simulate cleanup without deleting

        Returns:
            Dictionary with cleanup statistics
        """
        if not self.initialized:
            raise RuntimeError("Workspace not initialized")

        return self.cleanup_manager.cleanup_run(
            run_id=self.path_manager.run_id,
            strategy=strategy,
            dry_run=dry_run
        )

    def save_state(self) -> None:
        """Save workspace state to file."""
        if not self.initialized:
            raise RuntimeError("Workspace not initialized")

        # Save file tracker manifest
        manifest_path = self.path_manager.get_state_file().parent / 'files_manifest.json'
        self.file_tracker.save_manifest(str(manifest_path))

    def load_state(self) -> None:
        """Load workspace state from file."""
        if not self.initialized:
            raise RuntimeError("Workspace not initialized")

        # Load file tracker manifest
        manifest_path = self.path_manager.get_state_file().parent / 'files_manifest.json'
        if Path(manifest_path).exists():
            self.file_tracker = FileTracker.load_manifest(str(manifest_path))

            # Reconnect organizer and cleanup manager
            self.file_organizer = FileOrganizer(
                path_manager=self.path_manager,
                file_tracker=self.file_tracker
            )
            self.cleanup_manager = CleanupManager(
                file_tracker=self.file_tracker,
                path_manager=self.path_manager
            )

    def archive_run(self, compression: str = 'gz', cleanup_after: bool = True) -> Path:
        """Archive this run to compressed tarball.

        Args:
            compression: Compression type (gz, bz2, xz)
            cleanup_after: Whether to delete run directory after archiving

        Returns:
            Path to created archive file
        """
        if not self.initialized:
            raise RuntimeError("Workspace not initialized")

        return self.cleanup_manager.archive_run(
            run_id=self.path_manager.run_id,
            compression=compression,
            cleanup_after=cleanup_after
        )

    def promote_to_results(self, files: list) -> list:
        """Promote files to results directory.

        Args:
            files: List of file paths to promote

        Returns:
            List of paths in results directory
        """
        if not self.initialized:
            raise RuntimeError("Workspace not initialized")

        return self.file_organizer.promote_to_results(files)

    def get_path_manager(self) -> PathManager:
        """Get path manager instance.

        Returns:
            PathManager instance
        """
        if not self.initialized:
            raise RuntimeError("Workspace not initialized")

        return self.path_manager

    def get_file_tracker(self) -> FileTracker:
        """Get file tracker instance.

        Returns:
            FileTracker instance
        """
        return self.file_tracker

    def __repr__(self) -> str:
        if self.initialized:
            return f"Workspace(root={self.structure.root}, run={self.path_manager.run_id})"
        return "Workspace(uninitialized)"
