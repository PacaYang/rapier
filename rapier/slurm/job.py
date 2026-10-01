"""
SLURM job representation.

Represents a SLURM job with its metadata, status, and resource specifications.
"""

from dataclasses import dataclass, field
from typing import Optional, Dict, Any, List
from datetime import datetime
from pathlib import Path
from enum import Enum


class SlurmJobStatus(Enum):
    """SLURM job status."""
    PENDING = "PENDING"      # Job is waiting in queue
    RUNNING = "RUNNING"      # Job is executing
    COMPLETED = "COMPLETED"  # Job finished successfully
    FAILED = "FAILED"        # Job failed
    CANCELLED = "CANCELLED"  # Job was cancelled
    TIMEOUT = "TIMEOUT"      # Job exceeded time limit
    OUT_OF_MEMORY = "OUT_OF_MEMORY"  # Job ran out of memory
    NODE_FAIL = "NODE_FAIL"  # Node failure
    UNKNOWN = "UNKNOWN"      # Status unknown


@dataclass
class SlurmJob:
    """
    Represents a SLURM job.

    Attributes:
        job_id: SLURM job ID (assigned by SLURM after submission)
        job_name: Human-readable job name
        script_path: Path to the job script
        status: Current job status
        submit_time: When job was submitted
        start_time: When job started running
        end_time: When job finished
        work_dir: Working directory for job execution
        stdout_path: Path to stdout file
        stderr_path: Path to stderr file
        exit_code: Job exit code
        resources: Resource specifications
        dependencies: Job dependency specifications
        metadata: Additional job metadata
    """

    job_name: str
    script_path: Optional[Path] = None
    job_id: Optional[str] = None
    status: SlurmJobStatus = SlurmJobStatus.PENDING

    # Timing
    submit_time: Optional[datetime] = None
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None

    # Paths
    work_dir: Optional[Path] = None
    stdout_path: Optional[Path] = None
    stderr_path: Optional[Path] = None

    # Execution details
    exit_code: Optional[int] = None

    # Resources
    resources: Dict[str, Any] = field(default_factory=dict)

    # Dependencies
    dependencies: List[str] = field(default_factory=list)
    dependency_type: str = "afterok"  # afterok, afterany, afternotok, etc.

    # Metadata
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def is_finished(self) -> bool:
        """Check if job has finished (completed, failed, or cancelled)."""
        return self.status in (
            SlurmJobStatus.COMPLETED,
            SlurmJobStatus.FAILED,
            SlurmJobStatus.CANCELLED,
            SlurmJobStatus.TIMEOUT,
            SlurmJobStatus.OUT_OF_MEMORY,
            SlurmJobStatus.NODE_FAIL
        )

    @property
    def is_running(self) -> bool:
        """Check if job is currently running."""
        return self.status == SlurmJobStatus.RUNNING

    @property
    def is_pending(self) -> bool:
        """Check if job is pending."""
        return self.status == SlurmJobStatus.PENDING

    @property
    def succeeded(self) -> bool:
        """Check if job completed successfully."""
        return self.status == SlurmJobStatus.COMPLETED and (
            self.exit_code is None or self.exit_code == 0
        )

    @property
    def failed(self) -> bool:
        """Check if job failed."""
        return self.status in (
            SlurmJobStatus.FAILED,
            SlurmJobStatus.TIMEOUT,
            SlurmJobStatus.OUT_OF_MEMORY,
            SlurmJobStatus.NODE_FAIL
        ) or (self.status == SlurmJobStatus.COMPLETED and self.exit_code != 0)

    @property
    def duration(self) -> Optional[float]:
        """Get job duration in seconds."""
        if self.start_time and self.end_time:
            return (self.end_time - self.start_time).total_seconds()
        return None

    def update_status(self, status_str: str, exit_code: Optional[int] = None):
        """
        Update job status from SLURM status string.

        Args:
            status_str: SLURM status string (e.g., "RUNNING", "COMPLETED")
            exit_code: Optional exit code
        """
        status_map = {
            'PENDING': SlurmJobStatus.PENDING,
            'PD': SlurmJobStatus.PENDING,
            'RUNNING': SlurmJobStatus.RUNNING,
            'R': SlurmJobStatus.RUNNING,
            'COMPLETED': SlurmJobStatus.COMPLETED,
            'CD': SlurmJobStatus.COMPLETED,
            'FAILED': SlurmJobStatus.FAILED,
            'F': SlurmJobStatus.FAILED,
            'CANCELLED': SlurmJobStatus.CANCELLED,
            'CA': SlurmJobStatus.CANCELLED,
            'TIMEOUT': SlurmJobStatus.TIMEOUT,
            'TO': SlurmJobStatus.TIMEOUT,
            'OUT_OF_MEMORY': SlurmJobStatus.OUT_OF_MEMORY,
            'OOM': SlurmJobStatus.OUT_OF_MEMORY,
            'NODE_FAIL': SlurmJobStatus.NODE_FAIL,
            'NF': SlurmJobStatus.NODE_FAIL,
        }

        self.status = status_map.get(status_str.upper(), SlurmJobStatus.UNKNOWN)

        if exit_code is not None:
            self.exit_code = exit_code

    def get_dependency_string(self) -> Optional[str]:
        """
        Get SLURM dependency string.

        Returns:
            Dependency string (e.g., "afterok:12345:12346") or None
        """
        if not self.dependencies:
            return None

        return f"{self.dependency_type}:{':'.join(self.dependencies)}"

    def get_output(self) -> Optional[str]:
        """
        Read stdout from job.

        Returns:
            Job stdout content or None if not available
        """
        if not self.stdout_path or not self.stdout_path.exists():
            return None

        try:
            with open(self.stdout_path, 'r') as f:
                return f.read()
        except Exception:
            return None

    def get_errors(self) -> Optional[str]:
        """
        Read stderr from job.

        Returns:
            Job stderr content or None if not available
        """
        if not self.stderr_path or not self.stderr_path.exists():
            return None

        try:
            with open(self.stderr_path, 'r') as f:
                return f.read()
        except Exception:
            return None

    def to_dict(self) -> Dict[str, Any]:
        """
        Serialize job to dictionary.

        Returns:
            Dictionary representation
        """
        return {
            'job_id': self.job_id,
            'job_name': self.job_name,
            'script_path': str(self.script_path) if self.script_path else None,
            'status': self.status.value,
            'submit_time': self.submit_time.isoformat() if self.submit_time else None,
            'start_time': self.start_time.isoformat() if self.start_time else None,
            'end_time': self.end_time.isoformat() if self.end_time else None,
            'work_dir': str(self.work_dir) if self.work_dir else None,
            'stdout_path': str(self.stdout_path) if self.stdout_path else None,
            'stderr_path': str(self.stderr_path) if self.stderr_path else None,
            'exit_code': self.exit_code,
            'resources': self.resources,
            'dependencies': self.dependencies,
            'dependency_type': self.dependency_type,
            'duration': self.duration,
            'metadata': self.metadata,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'SlurmJob':
        """
        Deserialize job from dictionary.

        Args:
            data: Dictionary representation

        Returns:
            SlurmJob instance
        """
        job = cls(
            job_name=data['job_name'],
            job_id=data.get('job_id'),
            script_path=Path(data['script_path']) if data.get('script_path') else None,
        )

        if data.get('status'):
            job.status = SlurmJobStatus(data['status'])

        if data.get('submit_time'):
            job.submit_time = datetime.fromisoformat(data['submit_time'])
        if data.get('start_time'):
            job.start_time = datetime.fromisoformat(data['start_time'])
        if data.get('end_time'):
            job.end_time = datetime.fromisoformat(data['end_time'])

        if data.get('work_dir'):
            job.work_dir = Path(data['work_dir'])
        if data.get('stdout_path'):
            job.stdout_path = Path(data['stdout_path'])
        if data.get('stderr_path'):
            job.stderr_path = Path(data['stderr_path'])

        job.exit_code = data.get('exit_code')
        job.resources = data.get('resources', {})
        job.dependencies = data.get('dependencies', [])
        job.dependency_type = data.get('dependency_type', 'afterok')
        job.metadata = data.get('metadata', {})

        return job

    def __repr__(self) -> str:
        return f"SlurmJob(id={self.job_id}, name={self.job_name}, status={self.status.value})"
