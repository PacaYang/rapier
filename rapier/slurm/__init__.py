"""
SLURM integration module.

Provides interface to SLURM cluster for job submission, monitoring,
and resource management.
"""

from .job import SlurmJob, SlurmJobStatus
from .client import SlurmClient
from .config import SlurmConfig, PartitionInfo
from .script_generator import ScriptGenerator
from .dependencies import DependencyBuilder
from .monitor import JobMonitor
from .resources import ResourceParser

__all__ = [
    'SlurmJob',
    'SlurmJobStatus',
    'SlurmClient',
    'SlurmConfig',
    'PartitionInfo',
    'ScriptGenerator',
    'DependencyBuilder',
    'JobMonitor',
    'ResourceParser',
]
