"""File tracking system for managing created files."""

from pathlib import Path
from typing import Dict, List, Optional, Set

from .metadata import FileMetadata


class FileTracker:
    """Tracks all files created during pipeline execution."""

    def __init__(self):
        """Initialize file tracker."""
        self.files: Dict[str, FileMetadata] = {}
        self.important_files: Set[str] = set()
        self.by_category: Dict[str, List[str]] = {}
        self.by_task: Dict[str, List[str]] = {}

    def register_file(
        self,
        path: Path,
        category: str,
        task_id: Optional[str] = None,
        description: Optional[str] = None
    ) -> FileMetadata:
        """Register a file for tracking.

        Args:
            path: File path
            category: File category (input, intermediate, output, log, script, checkpoint)
            task_id: Task that created this file
            description: Optional description

        Returns:
            FileMetadata instance
        """
        path_str = str(path)

        # Create or update metadata
        if path_str in self.files:
            metadata = self.files[path_str]
            metadata.category = category
            metadata.source_task = task_id
            if description:
                metadata.description = description
        else:
            metadata = FileMetadata(
                path=path_str,
                source_task=task_id,
                category=category,
                description=description
            )
            self.files[path_str] = metadata

        # Update indices
        if category not in self.by_category:
            self.by_category[category] = []
        if path_str not in self.by_category[category]:
            self.by_category[category].append(path_str)

        if task_id:
            if task_id not in self.by_task:
                self.by_task[task_id] = []
            if path_str not in self.by_task[task_id]:
                self.by_task[task_id].append(path_str)

        return metadata

    def get_files_by_category(self, category: str) -> List[Path]:
        """Get all files in a category.

        Args:
            category: File category

        Returns:
            List of file paths
        """
        paths = self.by_category.get(category, [])
        return [Path(p) for p in paths]

    def get_files_by_task(self, task_id: str) -> List[Path]:
        """Get all files created by a task.

        Args:
            task_id: Task identifier

        Returns:
            List of file paths
        """
        paths = self.by_task.get(task_id, [])
        return [Path(p) for p in paths]

    def mark_important(self, path: Path) -> None:
        """Mark a file as important (preserve during cleanup).

        Args:
            path: File path
        """
        self.important_files.add(str(path))

    def is_important(self, path: Path) -> bool:
        """Check if a file is marked as important.

        Args:
            path: File path

        Returns:
            True if file is important
        """
        return str(path) in self.important_files

    def get_metadata(self, path: Path) -> Optional[FileMetadata]:
        """Get metadata for a file.

        Args:
            path: File path

        Returns:
            FileMetadata if tracked, None otherwise
        """
        return self.files.get(str(path))

    def get_all_tracked_files(self) -> Dict[str, FileMetadata]:
        """Get all tracked files.

        Returns:
            Dictionary of path -> FileMetadata
        """
        return self.files.copy()

    def get_stats(self) -> Dict[str, any]:
        """Get tracking statistics.

        Returns:
            Dictionary with statistics
        """
        total_size = sum(
            m.size for m in self.files.values()
            if m.size is not None
        )

        return {
            'total_files': len(self.files),
            'by_category': {
                cat: len(files) for cat, files in self.by_category.items()
            },
            'by_task': {
                task: len(files) for task, files in self.by_task.items()
            },
            'important_files': len(self.important_files),
            'total_size_bytes': total_size,
            'total_size_mb': total_size / (1024 * 1024),
        }

    def remove_file(self, path: Path) -> bool:
        """Remove a file from tracking.

        Args:
            path: File path

        Returns:
            True if file was tracked
        """
        path_str = str(path)

        if path_str not in self.files:
            return False

        metadata = self.files[path_str]

        # Remove from indices
        if metadata.category in self.by_category:
            self.by_category[metadata.category].remove(path_str)

        if metadata.source_task and metadata.source_task in self.by_task:
            self.by_task[metadata.source_task].remove(path_str)

        # Remove from important files
        self.important_files.discard(path_str)

        # Remove from main dict
        del self.files[path_str]

        return True

    def save_manifest(self, manifest_path: str) -> None:
        """Save file tracking manifest to JSON.

        Args:
            manifest_path: Path to save manifest
        """
        import json

        data = {
            'files': {
                path: metadata.to_dict()
                for path, metadata in self.files.items()
            },
            'important_files': list(self.important_files),
            'stats': self.get_stats()
        }

        with open(manifest_path, 'w') as f:
            json.dump(data, f, indent=2)

    @classmethod
    def load_manifest(cls, manifest_path: str) -> 'FileTracker':
        """Load file tracking manifest from JSON.

        Args:
            manifest_path: Path to manifest file

        Returns:
            FileTracker instance
        """
        import json

        with open(manifest_path, 'r') as f:
            data = json.load(f)

        tracker = cls()

        # Restore files
        for path, metadata_dict in data['files'].items():
            metadata = FileMetadata.from_dict(metadata_dict)
            tracker.files[path] = metadata

            # Rebuild indices
            if metadata.category not in tracker.by_category:
                tracker.by_category[metadata.category] = []
            tracker.by_category[metadata.category].append(path)

            if metadata.source_task:
                if metadata.source_task not in tracker.by_task:
                    tracker.by_task[metadata.source_task] = []
                tracker.by_task[metadata.source_task].append(path)

        # Restore important files
        tracker.important_files = set(data.get('important_files', []))

        return tracker

    def __repr__(self) -> str:
        return f"FileTracker(files={len(self.files)}, tasks={len(self.by_task)})"
