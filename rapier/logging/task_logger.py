"""Task-level logging for method execution."""

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..core.task import Task
from ..core.enums import TaskStatus
from .log_manager import get_log_manager


class TaskLogger:
    """Method/task-level logging for individual executions."""

    def __init__(self, task: Task, log_dir: Optional[str] = None):
        """Initialize task logger.

        Args:
            task: Task instance
            log_dir: Directory for log files
        """
        self.task = task
        self.task_id = task.task_id
        self.log_dir = Path(log_dir) if log_dir else None

        # Get logger from log manager
        self.log_manager = get_log_manager()
        self.logger = self.log_manager.get_logger(f'task.{self.task_id}')

        # Create dedicated log files
        if self.log_dir:
            self.log_dir.mkdir(parents=True, exist_ok=True)
            self.log_file = self.log_dir / f'task_{self.task_id}.log'
            self.error_file = self.log_dir / f'task_{self.task_id}_error.log'
        else:
            self.log_file = None
            self.error_file = None

        self.start_time: Optional[datetime] = None

    def _write_log(self, event: str, data: Dict[str, Any], level: str = 'INFO') -> None:
        """Write structured log entry.

        Args:
            event: Event type
            data: Event data
            level: Log level
        """
        if not self.log_file:
            return

        log_entry = {
            'timestamp': datetime.now().isoformat(),
            'task_id': self.task_id,
            'level': level,
            'event': event,
            'data': data
        }

        with open(self.log_file, 'a', encoding='utf-8') as f:
            f.write(json.dumps(log_entry) + '\n')

    def _write_error(self, message: str) -> None:
        """Write error log entry.

        Args:
            message: Error message
        """
        if not self.error_file:
            return

        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        with open(self.error_file, 'a', encoding='utf-8') as f:
            f.write(f"[{timestamp}] {message}\n")

    def log_validation(self, phase: str, success: bool, messages: List[str]) -> None:
        """Log validation results.

        Args:
            phase: Validation phase (e.g., 'inputs', 'environment', 'outputs')
            success: Whether validation passed
            messages: Validation messages
        """
        data = {
            'phase': phase,
            'success': success,
            'messages': messages
        }

        level = 'INFO' if success else 'ERROR'
        self._write_log('validation', data, level)

        if not success:
            self._write_error(f"Validation failed for {phase}: {', '.join(messages)}")

        log_method = self.logger.info if success else self.logger.error
        log_method(
            f"Validation {phase}: {'PASSED' if success else 'FAILED'}",
            extra={
                'task_id': self.task_id,
                'event': 'validation',
                'data': data
            }
        )

    def log_execution_start(self, command: str, slurm_job_id: Optional[str] = None) -> None:
        """Log execution start.

        Args:
            command: Command being executed
            slurm_job_id: SLURM job ID if applicable
        """
        self.start_time = datetime.now()

        data = {
            'command': command,
            'slurm_job_id': slurm_job_id,
            'start_time': self.start_time.isoformat()
        }

        self._write_log('execution_start', data)

        extra = {
            'task_id': self.task_id,
            'event': 'execution_start',
            'data': data
        }
        if slurm_job_id:
            extra['slurm_job_id'] = slurm_job_id

        self.logger.info(
            f"Execution started: {command[:100]}...",
            extra=extra
        )

    def log_progress(self, message: str, percentage: Optional[float] = None) -> None:
        """Log execution progress.

        Args:
            message: Progress message
            percentage: Optional progress percentage (0-100)
        """
        data = {
            'message': message,
            'percentage': percentage
        }

        self._write_log('progress', data)

        progress_str = f" ({percentage:.1f}%)" if percentage is not None else ""
        self.logger.info(
            f"Progress{progress_str}: {message}",
            extra={
                'task_id': self.task_id,
                'event': 'progress',
                'data': data
            }
        )

    def log_output_file(self, path: str, exists: bool, size: Optional[int] = None) -> None:
        """Log output file status.

        Args:
            path: File path
            exists: Whether file exists
            size: File size in bytes if exists
        """
        data = {
            'path': path,
            'exists': exists,
            'size': size
        }

        level = 'INFO' if exists else 'WARNING'
        self._write_log('output_file', data, level)

        if not exists:
            self._write_error(f"Expected output file not found: {path}")

        size_str = f" ({size} bytes)" if size is not None else ""
        status = "found" if exists else "MISSING"
        self.logger.log(
            getattr(self.logger, level.lower()).level if hasattr(self.logger, level.lower()) else 20,
            f"Output file {status}: {path}{size_str}",
            extra={
                'task_id': self.task_id,
                'event': 'output_file',
                'data': data
            }
        )

    def log_execution_complete(
        self,
        status: TaskStatus,
        outputs: Optional[Dict[str, Any]] = None,
        error: Optional[str] = None
    ) -> None:
        """Log execution completion.

        Args:
            status: Final task status
            outputs: Output data
            error: Error message if failed
        """
        duration = None
        if self.start_time:
            duration = (datetime.now() - self.start_time).total_seconds()

        data = {
            'status': status.value if hasattr(status, 'value') else str(status),
            'duration': duration,
            'outputs': outputs or {},
            'error': error
        }

        level = 'INFO' if status == TaskStatus.COMPLETED else 'ERROR'
        self._write_log('execution_complete', data, level)

        if error:
            self._write_error(f"Execution failed: {error}")

        duration_str = f" in {duration:.2f}s" if duration else ""
        self.logger.log(
            logging.INFO if status == TaskStatus.COMPLETED else logging.ERROR,
            f"Execution completed: {status.value if hasattr(status, 'value') else status}{duration_str}",
            extra={
                'task_id': self.task_id,
                'event': 'execution_complete',
                'data': data
            }
        )

    def log_stdout(self, output: str) -> None:
        """Log stdout output from task execution.

        Args:
            output: Standard output text
        """
        if not self.log_file:
            return

        data = {
            'stream': 'stdout',
            'content': output
        }

        self._write_log('output', data)

    def log_stderr(self, output: str) -> None:
        """Log stderr output from task execution.

        Args:
            output: Standard error text
        """
        if not self.log_file:
            return

        data = {
            'stream': 'stderr',
            'content': output
        }

        self._write_log('output', data, 'WARNING')
        self._write_error(output)

    def log_resource_usage(self, cpu_percent: float, memory_mb: float, runtime: float) -> None:
        """Log resource usage statistics.

        Args:
            cpu_percent: CPU usage percentage
            memory_mb: Memory usage in MB
            runtime: Runtime in seconds
        """
        data = {
            'cpu_percent': cpu_percent,
            'memory_mb': memory_mb,
            'runtime': runtime
        }

        self._write_log('resource_usage', data)

        self.logger.debug(
            f"Resources: CPU {cpu_percent:.1f}%, Memory {memory_mb:.1f}MB, Runtime {runtime:.1f}s",
            extra={
                'task_id': self.task_id,
                'event': 'resource_usage',
                'data': data
            }
        )

    def get_log_content(self) -> Optional[str]:
        """Get the full log file content.

        Returns:
            Log file content as string, or None if no log file
        """
        if not self.log_file or not self.log_file.exists():
            return None

        with open(self.log_file, 'r', encoding='utf-8') as f:
            return f.read()

    def get_error_content(self) -> Optional[str]:
        """Get the full error log content.

        Returns:
            Error log content as string, or None if no errors
        """
        if not self.error_file or not self.error_file.exists():
            return None

        with open(self.error_file, 'r', encoding='utf-8') as f:
            return f.read()


# Fix missing import
import logging
