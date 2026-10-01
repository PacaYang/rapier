"""
Execution engine module.

Provides pipeline execution orchestration with dependency resolution,
failure handling, and parallel execution.
"""

from .result import ExecutionResult, TaskResult
from .state import ExecutionState, ExecutionPhase
from .failure_handler import FailureHandler, FailureAction, FailureDecision
from .pooler import ResultPooler
from .parallel_executor import ParallelExecutor, TaskIsolation
from .task_runner import TaskRunner
from .scheduler import Scheduler
from .executor import ExecutionEngine

__all__ = [
    'ExecutionResult',
    'TaskResult',
    'ExecutionState',
    'ExecutionPhase',
    'FailureHandler',
    'FailureAction',
    'FailureDecision',
    'ResultPooler',
    'ParallelExecutor',
    'TaskIsolation',
    'TaskRunner',
    'Scheduler',
    'ExecutionEngine',
]
