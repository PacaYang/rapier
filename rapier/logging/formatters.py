"""Custom log formatters for structured and human-readable logging."""

import json
import logging
from datetime import datetime
from typing import Any, Dict


class StructuredFormatter(logging.Formatter):
    """JSON formatter for machine-readable structured logs."""

    def __init__(self, include_context: bool = True):
        super().__init__()
        self.include_context = include_context

    def format(self, record: logging.LogRecord) -> str:
        """Format log record as JSON."""
        log_data = {
            'timestamp': datetime.fromtimestamp(record.created).isoformat(),
            'level': record.levelname,
            'component': record.name,
            'message': record.getMessage(),
        }

        # Add contextual information if available
        if self.include_context:
            if hasattr(record, 'task_id'):
                log_data['task_id'] = record.task_id
            if hasattr(record, 'pipeline_id'):
                log_data['pipeline_id'] = record.pipeline_id
            if hasattr(record, 'slurm_job_id'):
                log_data['slurm_job_id'] = record.slurm_job_id
            if hasattr(record, 'event'):
                log_data['event'] = record.event
            if hasattr(record, 'data'):
                log_data['data'] = record.data

        # Add exception info if present
        if record.exc_info:
            log_data['exception'] = self.formatException(record.exc_info)

        return json.dumps(log_data)


class HumanFormatter(logging.Formatter):
    """Colored, human-readable formatter for console output."""

    # ANSI color codes
    COLORS = {
        'DEBUG': '\033[36m',      # Cyan
        'INFO': '\033[32m',       # Green
        'WARNING': '\033[33m',    # Yellow
        'ERROR': '\033[31m',      # Red
        'CRITICAL': '\033[35m',   # Magenta
        'RESET': '\033[0m',       # Reset
    }

    def __init__(self, use_colors: bool = True):
        super().__init__()
        self.use_colors = use_colors

    def format(self, record: logging.LogRecord) -> str:
        """Format log record with colors and readable structure."""
        timestamp = datetime.fromtimestamp(record.created).strftime('%Y-%m-%d %H:%M:%S')
        level = record.levelname
        component = record.name
        message = record.getMessage()

        # Add color if enabled
        if self.use_colors:
            color = self.COLORS.get(level, self.COLORS['RESET'])
            reset = self.COLORS['RESET']
            formatted = f"{timestamp} {color}[{level}]{reset} {component}: {message}"
        else:
            formatted = f"{timestamp} [{level}] {component}: {message}"

        # Add contextual info if available
        context_parts = []
        if hasattr(record, 'task_id'):
            context_parts.append(f"task={record.task_id}")
        if hasattr(record, 'pipeline_id'):
            context_parts.append(f"pipeline={record.pipeline_id}")
        if hasattr(record, 'slurm_job_id'):
            context_parts.append(f"slurm={record.slurm_job_id}")

        if context_parts:
            formatted += f" ({', '.join(context_parts)})"

        # Add exception if present
        if record.exc_info:
            formatted += f"\n{self.formatException(record.exc_info)}"

        return formatted


class TimestampFormatter(logging.Formatter):
    """Formatter with high-precision timestamps."""

    def format(self, record: logging.LogRecord) -> str:
        """Format log record with microsecond precision."""
        timestamp = datetime.fromtimestamp(record.created).strftime('%Y-%m-%d %H:%M:%S.%f')
        level = record.levelname
        component = record.name
        message = record.getMessage()

        formatted = f"{timestamp} [{level}] {component}: {message}"

        if record.exc_info:
            formatted += f"\n{self.formatException(record.exc_info)}"

        return formatted


class ContextAdapter(logging.LoggerAdapter):
    """Logger adapter that adds contextual information to all log records."""

    def __init__(self, logger: logging.Logger, context: Dict[str, Any]):
        super().__init__(logger, context)

    def process(self, msg: str, kwargs: Dict[str, Any]) -> tuple:
        """Add context to log record."""
        # Add context as extra fields
        if 'extra' not in kwargs:
            kwargs['extra'] = {}
        kwargs['extra'].update(self.extra)
        return msg, kwargs
