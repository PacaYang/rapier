"""Central logging coordinator for the Rapier platform."""

import logging
import os
from pathlib import Path
from typing import Dict, Optional

from .formatters import HumanFormatter, StructuredFormatter


class LogManager:
    """Central logging coordinator that manages multiple loggers."""

    def __init__(self, log_directory: Optional[str] = None):
        """Initialize the log manager.

        Args:
            log_directory: Base directory for log files. If None, logs to console only.
        """
        self.log_directory = Path(log_directory) if log_directory else None
        self.loggers: Dict[str, logging.Logger] = {}
        self._configured = False

    def set_log_directory(self, path: str) -> None:
        """Set the log directory and create it if needed.

        Args:
            path: Path to log directory
        """
        self.log_directory = Path(path)
        self.log_directory.mkdir(parents=True, exist_ok=True)

    def configure_logging(self, config: Optional[Dict] = None) -> None:
        """Configure logging system with given configuration.

        Args:
            config: Configuration dictionary with keys:
                - level: Default log level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
                - console: Whether to log to console (default: True)
                - file: Whether to log to file (default: True if log_directory set)
                - structured: Whether to use structured (JSON) format (default: True)
                - colored: Whether to use colored console output (default: True)
        """
        if config is None:
            config = {}

        # Set defaults
        default_level = config.get('level', 'INFO')
        console_enabled = config.get('console', True)
        file_enabled = config.get('file', self.log_directory is not None)
        structured = config.get('structured', True)
        colored = config.get('colored', True)

        # Configure root logger
        root_logger = logging.getLogger('rapier')
        root_logger.setLevel(getattr(logging, default_level))
        root_logger.handlers.clear()

        # Add console handler
        if console_enabled:
            console_handler = logging.StreamHandler()
            console_handler.setLevel(getattr(logging, default_level))
            console_formatter = HumanFormatter(use_colors=colored)
            console_handler.setFormatter(console_formatter)
            root_logger.addHandler(console_handler)

        # Add file handler if directory is set
        if file_enabled and self.log_directory:
            self.log_directory.mkdir(parents=True, exist_ok=True)
            file_handler = logging.FileHandler(
                self.log_directory / 'rapier.log',
                mode='a',
                encoding='utf-8'
            )
            file_handler.setLevel(getattr(logging, default_level))

            if structured:
                file_formatter = StructuredFormatter(include_context=True)
            else:
                file_formatter = HumanFormatter(use_colors=False)

            file_handler.setFormatter(file_formatter)
            root_logger.addHandler(file_handler)

        self._configured = True

    def get_logger(self, name: str, level: Optional[str] = None) -> logging.Logger:
        """Get or create a logger with the given name.

        Args:
            name: Logger name (hierarchical, e.g., 'rapier.engine.executor')
            level: Log level override for this logger

        Returns:
            Logger instance
        """
        if not self._configured:
            self.configure_logging()

        # Ensure name starts with 'rapier'
        if not name.startswith('rapier'):
            name = f'rapier.{name}'

        if name in self.loggers:
            logger = self.loggers[name]
        else:
            logger = logging.getLogger(name)
            self.loggers[name] = logger

        if level:
            logger.setLevel(getattr(logging, level))

        return logger

    def get_log_file(self, component: str) -> Optional[str]:
        """Get the log file path for a specific component.

        Args:
            component: Component name (e.g., 'pipeline', 'task', 'slurm')

        Returns:
            Path to log file, or None if logging to console only
        """
        if not self.log_directory:
            return None

        log_file = self.log_directory / f'{component}.log'
        return str(log_file)

    def create_component_logger(
        self,
        component: str,
        log_file: Optional[str] = None,
        structured: bool = True
    ) -> logging.Logger:
        """Create a dedicated logger for a component with its own log file.

        Args:
            component: Component name
            log_file: Optional custom log file path
            structured: Whether to use structured (JSON) format

        Returns:
            Logger instance with file handler
        """
        logger_name = f'rapier.{component}'
        logger = self.get_logger(logger_name)

        # Add dedicated file handler if log directory is set
        if self.log_directory:
            if log_file is None:
                log_file = self.log_directory / f'{component}.log'
            else:
                log_file = Path(log_file)

            # Check if this handler already exists
            handler_exists = any(
                isinstance(h, logging.FileHandler) and h.baseFilename == str(log_file.absolute())
                for h in logger.handlers
            )

            if not handler_exists:
                file_handler = logging.FileHandler(
                    log_file,
                    mode='a',
                    encoding='utf-8'
                )

                if structured:
                    formatter = StructuredFormatter(include_context=True)
                else:
                    formatter = HumanFormatter(use_colors=False)

                file_handler.setFormatter(formatter)
                logger.addHandler(file_handler)

        return logger

    def close_all(self) -> None:
        """Close all logging handlers and flush buffers."""
        for logger in self.loggers.values():
            for handler in logger.handlers[:]:
                handler.close()
                logger.removeHandler(handler)

        # Close root logger handlers
        root_logger = logging.getLogger('rapier')
        for handler in root_logger.handlers[:]:
            handler.close()
            root_logger.removeHandler(handler)


# Global log manager instance
_global_log_manager: Optional[LogManager] = None


def get_log_manager() -> LogManager:
    """Get the global log manager instance.

    Returns:
        Global LogManager instance
    """
    global _global_log_manager
    if _global_log_manager is None:
        _global_log_manager = LogManager()
    return _global_log_manager


def initialize_logging(log_directory: Optional[str] = None, config: Optional[Dict] = None) -> LogManager:
    """Initialize the global logging system.

    Args:
        log_directory: Base directory for log files
        config: Logging configuration dictionary

    Returns:
        Configured LogManager instance
    """
    global _global_log_manager
    _global_log_manager = LogManager(log_directory)
    _global_log_manager.configure_logging(config)
    return _global_log_manager
