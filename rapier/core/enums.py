"""
Enumerations for status values and configuration options.
"""

from enum import Enum, auto


class TaskStatus(Enum):
    """Status of a task execution."""
    PENDING = auto()
    RUNNING = auto()
    COMPLETED = auto()
    FAILED = auto()
    CANCELLED = auto()
    SKIPPED = auto()


class MethodType(Enum):
    """Type of method implementation."""
    SINGLE_STEP = auto()
    MULTI_STEP = auto()


class FailurePolicy(Enum):
    """Policy for handling task failures."""
    STOP = auto()          # Halt entire pipeline
    CONTINUE = auto()      # Skip downstream tasks
    RETRY = auto()         # Retry the failed task
