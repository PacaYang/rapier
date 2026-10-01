"""Logging and monitoring system for the Rapier platform."""

from .log_manager import LogManager, get_log_manager, initialize_logging
from .formatters import StructuredFormatter, HumanFormatter, TimestampFormatter, ContextAdapter
from .pipeline_logger import PipelineLogger, ExecutionResult, TaskResult
from .task_logger import TaskLogger
from .slurm_logger import SlurmLogger, SlurmJob
from .monitor import FileMonitor
from .aggregator import LogAggregator, LogEntry, Event
from .alerts import AlertManager, Alert, AlertLevel, AlertCondition, ExecutionState
from .cli_monitor import CLIMonitor
from .dashboard import Dashboard, DashboardWithWebSocket

__all__ = [
    # Core logging
    'LogManager',
    'get_log_manager',
    'initialize_logging',

    # Formatters
    'StructuredFormatter',
    'HumanFormatter',
    'TimestampFormatter',
    'ContextAdapter',

    # Loggers
    'PipelineLogger',
    'TaskLogger',
    'SlurmLogger',

    # Results
    'ExecutionResult',
    'TaskResult',
    'SlurmJob',

    # Monitoring
    'FileMonitor',
    'LogAggregator',
    'LogEntry',
    'Event',

    # Alerts
    'AlertManager',
    'Alert',
    'AlertLevel',
    'AlertCondition',
    'ExecutionState',

    # Display
    'CLIMonitor',
    'Dashboard',
    'DashboardWithWebSocket',
]
