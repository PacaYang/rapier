"""File monitoring for output validation and stall detection."""

import os
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable, Dict, Optional

from .log_manager import get_log_manager


class FileMonitor:
    """Validates output files during execution and detects stalled jobs."""

    def __init__(self, log_dir: Optional[str] = None):
        """Initialize file monitor.

        Args:
            log_dir: Directory for log files
        """
        self.log_manager = get_log_manager()
        self.logger = self.log_manager.get_logger('monitor.file')

        # Track file sizes over time
        self._file_sizes: Dict[str, int] = {}
        self._last_check: Dict[str, datetime] = {}

    def watch_file(
        self,
        path: str,
        expected_size: Optional[int] = None,
        callback: Optional[Callable[[str, bool], None]] = None
    ) -> bool:
        """Watch a file for creation and growth.

        Args:
            path: File path to watch
            expected_size: Expected minimum file size in bytes
            callback: Optional callback function(path, exists)

        Returns:
            True if file exists and meets size requirement
        """
        file_path = Path(path)
        exists = file_path.exists()

        if callback:
            callback(path, exists)

        if not exists:
            self.logger.warning(f"Watched file does not exist: {path}")
            return False

        # Check file size
        size = file_path.stat().st_size
        self._file_sizes[path] = size
        self._last_check[path] = datetime.now()

        if expected_size is not None and size < expected_size:
            self.logger.warning(
                f"File size below expected: {path} ({size} < {expected_size} bytes)"
            )
            return False

        self.logger.debug(f"File validated: {path} ({size} bytes)")
        return True

    def check_file_growth(
        self,
        path: str,
        min_interval_seconds: int = 60,
        stall_threshold_seconds: int = 300
    ) -> bool:
        """Check if a file is growing (not stalled).

        Args:
            path: File path to check
            min_interval_seconds: Minimum seconds between checks
            stall_threshold_seconds: Seconds without growth to consider stalled

        Returns:
            True if file is growing, False if stalled or doesn't exist
        """
        file_path = Path(path)

        if not file_path.exists():
            self.logger.warning(f"Cannot check growth, file does not exist: {path}")
            return False

        current_size = file_path.stat().st_size
        current_time = datetime.now()

        # First check for this file
        if path not in self._file_sizes:
            self._file_sizes[path] = current_size
            self._last_check[path] = current_time
            self.logger.debug(f"Started monitoring file growth: {path} ({current_size} bytes)")
            return True

        # Check if enough time has passed
        time_since_last = (current_time - self._last_check[path]).total_seconds()
        if time_since_last < min_interval_seconds:
            return True  # Too soon to check

        # Check for growth
        previous_size = self._file_sizes[path]
        has_grown = current_size > previous_size

        if has_grown:
            growth = current_size - previous_size
            self.logger.debug(
                f"File growing: {path} (+{growth} bytes in {time_since_last:.0f}s)"
            )
            self._file_sizes[path] = current_size
            self._last_check[path] = current_time
            return True

        # No growth - check if stalled
        if time_since_last > stall_threshold_seconds:
            self.logger.error(
                f"File appears stalled: {path} "
                f"(no growth for {time_since_last:.0f}s, size={current_size} bytes)"
            )
            return False

        # No growth yet, but not past threshold
        self.logger.debug(
            f"File not growing: {path} ({time_since_last:.0f}s since last growth)"
        )
        return True

    def validate_file_format(self, path: str, format: str) -> bool:
        """Validate file format by checking content.

        Args:
            path: File path
            format: Expected format ('sdf', 'pdb', 'txt', 'csv', etc.)

        Returns:
            True if format appears valid
        """
        file_path = Path(path)

        if not file_path.exists():
            self.logger.error(f"Cannot validate format, file does not exist: {path}")
            return False

        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read(1024)  # Read first 1KB

            valid = self._check_format(content, format)

            if valid:
                self.logger.debug(f"File format valid: {path} ({format})")
            else:
                self.logger.error(f"File format invalid: {path} (expected {format})")

            return valid

        except Exception as e:
            self.logger.error(f"Error validating file format {path}: {e}")
            return False

    def _check_format(self, content: str, format: str) -> bool:
        """Check if content matches expected format.

        Args:
            content: File content sample
            format: Expected format

        Returns:
            True if format matches
        """
        format = format.lower()

        if format == 'sdf':
            # SDF files have molecule blocks ending with $$$$
            return '$$$$' in content or 'M  END' in content

        elif format == 'pdb':
            # PDB files have ATOM or HETATM records
            return 'ATOM  ' in content or 'HETATM' in content

        elif format == 'csv':
            # CSV files have comma-separated values
            lines = content.split('\n')
            return len(lines) > 0 and ',' in lines[0]

        elif format == 'json':
            # JSON files start with { or [
            return content.strip().startswith(('{', '['))

        elif format == 'txt':
            # Any text file is valid
            return True

        else:
            self.logger.warning(f"Unknown format type: {format}, skipping validation")
            return True

    def get_file_info(self, path: str) -> Optional[Dict[str, any]]:
        """Get information about a file.

        Args:
            path: File path

        Returns:
            Dictionary with file info or None if file doesn't exist
        """
        file_path = Path(path)

        if not file_path.exists():
            return None

        stat = file_path.stat()
        return {
            'path': str(file_path.absolute()),
            'size': stat.st_size,
            'modified': datetime.fromtimestamp(stat.st_mtime).isoformat(),
            'created': datetime.fromtimestamp(stat.st_ctime).isoformat(),
        }

    def reset_monitoring(self, path: str) -> None:
        """Reset monitoring state for a file.

        Args:
            path: File path
        """
        if path in self._file_sizes:
            del self._file_sizes[path]
        if path in self._last_check:
            del self._last_check[path]

        self.logger.debug(f"Reset monitoring for: {path}")

    def clear_all(self) -> None:
        """Clear all monitoring state."""
        self._file_sizes.clear()
        self._last_check.clear()
        self.logger.debug("Cleared all file monitoring state")
