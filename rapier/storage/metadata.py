"""File metadata for provenance tracking."""

import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional


class FileMetadata:
    """Stores metadata about files for provenance tracking."""

    def __init__(
        self,
        path: str,
        source_task: Optional[str] = None,
        category: str = 'unknown',
        description: Optional[str] = None
    ):
        """Initialize file metadata.

        Args:
            path: File path
            source_task: Task that created this file
            category: File category (input, intermediate, output, log, script, checkpoint)
            description: Optional description
        """
        self.path = str(path)
        self.source_task = source_task
        self.category = category
        self.description = description

        # Computed properties (lazy)
        self._size: Optional[int] = None
        self._created_time: Optional[str] = None
        self._modified_time: Optional[str] = None
        self._checksum: Optional[str] = None

    @property
    def size(self) -> Optional[int]:
        """Get file size in bytes."""
        if self._size is None:
            path = Path(self.path)
            if path.exists():
                self._size = path.stat().st_size
        return self._size

    @property
    def created_time(self) -> Optional[str]:
        """Get file creation time as ISO format string."""
        if self._created_time is None:
            path = Path(self.path)
            if path.exists():
                timestamp = path.stat().st_ctime
                self._created_time = datetime.fromtimestamp(timestamp).isoformat()
        return self._created_time

    @property
    def modified_time(self) -> Optional[str]:
        """Get file modification time as ISO format string."""
        if self._modified_time is None:
            path = Path(self.path)
            if path.exists():
                timestamp = path.stat().st_mtime
                self._modified_time = datetime.fromtimestamp(timestamp).isoformat()
        return self._modified_time

    @property
    def checksum(self) -> Optional[str]:
        """Get cached checksum (if computed)."""
        return self._checksum

    def compute_checksum(self, algorithm: str = 'md5') -> str:
        """Compute file checksum.

        Args:
            algorithm: Hash algorithm ('md5' or 'sha256')

        Returns:
            Hex digest of file checksum

        Raises:
            ValueError: If algorithm is not supported
            FileNotFoundError: If file doesn't exist
        """
        path = Path(self.path)
        if not path.exists():
            raise FileNotFoundError(f"Cannot compute checksum, file not found: {path}")

        if algorithm == 'md5':
            hasher = hashlib.md5()
        elif algorithm == 'sha256':
            hasher = hashlib.sha256()
        else:
            raise ValueError(f"Unsupported algorithm: {algorithm}. Use 'md5' or 'sha256'")

        # Read file in chunks for memory efficiency
        with open(path, 'rb') as f:
            for chunk in iter(lambda: f.read(8192), b''):
                hasher.update(chunk)

        self._checksum = hasher.hexdigest()
        return self._checksum

    def is_valid(self) -> bool:
        """Check if file still exists and matches stored checksum.

        Returns:
            True if file exists and checksum matches (if checksum was computed)
        """
        path = Path(self.path)
        if not path.exists():
            return False

        # If we have a stored checksum, verify it
        if self._checksum is not None:
            # Determine algorithm from checksum length
            algorithm = 'md5' if len(self._checksum) == 32 else 'sha256'
            current_checksum = self.compute_checksum(algorithm)
            return current_checksum == self._checksum

        return True

    def to_dict(self) -> Dict[str, Any]:
        """Convert metadata to dictionary.

        Returns:
            Dictionary representation
        """
        return {
            'path': self.path,
            'source_task': self.source_task,
            'category': self.category,
            'description': self.description,
            'size': self.size,
            'created_time': self.created_time,
            'modified_time': self.modified_time,
            'checksum': self.checksum,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'FileMetadata':
        """Create FileMetadata from dictionary.

        Args:
            data: Dictionary representation

        Returns:
            FileMetadata instance
        """
        metadata = cls(
            path=data['path'],
            source_task=data.get('source_task'),
            category=data.get('category', 'unknown'),
            description=data.get('description')
        )

        # Restore computed properties if present
        metadata._size = data.get('size')
        metadata._created_time = data.get('created_time')
        metadata._modified_time = data.get('modified_time')
        metadata._checksum = data.get('checksum')

        return metadata

    def save(self, metadata_path: Optional[str] = None) -> None:
        """Save metadata to JSON file.

        Args:
            metadata_path: Path to metadata file (defaults to <file>.metadata.json)
        """
        if metadata_path is None:
            metadata_path = f"{self.path}.metadata.json"

        with open(metadata_path, 'w') as f:
            json.dump(self.to_dict(), f, indent=2)

    @classmethod
    def load(cls, metadata_path: str) -> 'FileMetadata':
        """Load metadata from JSON file.

        Args:
            metadata_path: Path to metadata file

        Returns:
            FileMetadata instance
        """
        with open(metadata_path, 'r') as f:
            data = json.load(f)

        return cls.from_dict(data)

    @classmethod
    def load_for_file(cls, file_path: str) -> Optional['FileMetadata']:
        """Load metadata for a file (looks for <file>.metadata.json).

        Args:
            file_path: Path to file

        Returns:
            FileMetadata instance, or None if metadata file doesn't exist
        """
        metadata_path = f"{file_path}.metadata.json"
        if not Path(metadata_path).exists():
            return None

        return cls.load(metadata_path)

    def update_stats(self) -> None:
        """Update file statistics (size, timestamps)."""
        # Reset cached values to force recomputation
        self._size = None
        self._created_time = None
        self._modified_time = None

        # Access properties to compute them
        _ = self.size
        _ = self.created_time
        _ = self.modified_time

    def __repr__(self) -> str:
        return f"FileMetadata(path={self.path}, category={self.category}, task={self.source_task})"
