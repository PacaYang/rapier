"""SLURM-specific logging for job monitoring."""

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

from .log_manager import get_log_manager


class SlurmJob:
    """Container for SLURM job information."""

    def __init__(
        self,
        job_id: str,
        task_id: str,
        script_path: str,
        status: str = 'PENDING',
        partition: Optional[str] = None,
        nodes: int = 1,
        cpus: int = 1,
        memory: Optional[str] = None
    ):
        self.job_id = job_id
        self.task_id = task_id
        self.script_path = script_path
        self.status = status
        self.partition = partition
        self.nodes = nodes
        self.cpus = cpus
        self.memory = memory
        self.submit_time = datetime.now()


class SlurmLogger:
    """SLURM-specific logging for job tracking."""

    def __init__(self, log_dir: Optional[str] = None):
        """Initialize SLURM logger.

        Args:
            log_dir: Directory for log files
        """
        self.log_dir = Path(log_dir) if log_dir else None

        # Get logger from log manager
        self.log_manager = get_log_manager()
        self.logger = self.log_manager.get_logger('slurm')

        # Create dedicated log file
        if self.log_dir:
            self.log_dir.mkdir(parents=True, exist_ok=True)
            self.log_file = self.log_dir / 'slurm.log'
        else:
            self.log_file = None

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
            'level': level,
            'event': event,
            'data': data
        }

        with open(self.log_file, 'a', encoding='utf-8') as f:
            f.write(json.dumps(log_entry) + '\n')

    def log_job_submission(self, job: SlurmJob) -> None:
        """Log SLURM job submission.

        Args:
            job: SLURM job information
        """
        data = {
            'job_id': job.job_id,
            'task_id': job.task_id,
            'script_path': job.script_path,
            'partition': job.partition,
            'nodes': job.nodes,
            'cpus': job.cpus,
            'memory': job.memory,
            'submit_time': job.submit_time.isoformat()
        }

        self._write_log('job_submission', data)

        resource_info = f"{job.cpus} CPUs"
        if job.memory:
            resource_info += f", {job.memory} memory"
        if job.partition:
            resource_info += f", partition={job.partition}"

        self.logger.info(
            f"Job submitted: {job.job_id} (task={job.task_id}, {resource_info})",
            extra={
                'slurm_job_id': job.job_id,
                'task_id': job.task_id,
                'event': 'job_submission',
                'data': data
            }
        )

    def log_job_status_change(
        self,
        job_id: str,
        old_status: str,
        new_status: str,
        task_id: Optional[str] = None
    ) -> None:
        """Log SLURM job status change.

        Args:
            job_id: SLURM job ID
            old_status: Previous status
            new_status: New status
            task_id: Associated task ID
        """
        data = {
            'job_id': job_id,
            'old_status': old_status,
            'new_status': new_status,
            'task_id': task_id
        }

        level = 'INFO'
        if new_status in ['FAILED', 'CANCELLED', 'TIMEOUT', 'NODE_FAIL']:
            level = 'ERROR'
        elif new_status in ['PENDING', 'CONFIGURING']:
            level = 'DEBUG'

        self._write_log('job_status_change', data, level)

        extra = {
            'slurm_job_id': job_id,
            'event': 'job_status_change',
            'data': data
        }
        if task_id:
            extra['task_id'] = task_id

        self.logger.log(
            getattr(self.logger, level.lower()).level if hasattr(getattr(self.logger, level.lower(), None), 'level') else 20,
            f"Job {job_id}: {old_status} → {new_status}",
            extra=extra
        )

    def log_queue_snapshot(self, jobs: Dict[str, Any]) -> None:
        """Log snapshot of SLURM queue state.

        Args:
            jobs: Dictionary of job_id -> job_info
        """
        data = {
            'total_jobs': len(jobs),
            'jobs': jobs,
            'snapshot_time': datetime.now().isoformat()
        }

        self._write_log('queue_snapshot', data, 'DEBUG')

        # Count jobs by status
        status_counts = {}
        for job_info in jobs.values():
            status = job_info.get('status', 'UNKNOWN')
            status_counts[status] = status_counts.get(status, 0) + 1

        status_str = ', '.join(f"{status}: {count}" for status, count in status_counts.items())
        self.logger.debug(
            f"Queue snapshot: {len(jobs)} jobs ({status_str})",
            extra={
                'event': 'queue_snapshot',
                'data': data
            }
        )

    def log_resource_usage(
        self,
        job_id: str,
        usage: Dict[str, Any],
        task_id: Optional[str] = None
    ) -> None:
        """Log resource usage from sacct.

        Args:
            job_id: SLURM job ID
            usage: Resource usage dictionary with keys like:
                - elapsed: Elapsed time
                - cpu_time: CPU time used
                - max_rss: Maximum resident set size (memory)
                - avg_cpu: Average CPU usage
            task_id: Associated task ID
        """
        data = {
            'job_id': job_id,
            'task_id': task_id,
            'usage': usage,
            'query_time': datetime.now().isoformat()
        }

        self._write_log('resource_usage', data)

        usage_summary = []
        if 'elapsed' in usage:
            usage_summary.append(f"elapsed={usage['elapsed']}")
        if 'avg_cpu' in usage:
            usage_summary.append(f"CPU={usage['avg_cpu']}")
        if 'max_rss' in usage:
            usage_summary.append(f"mem={usage['max_rss']}")

        extra = {
            'slurm_job_id': job_id,
            'event': 'resource_usage',
            'data': data
        }
        if task_id:
            extra['task_id'] = task_id

        self.logger.info(
            f"Job {job_id} resources: {', '.join(usage_summary)}",
            extra=extra
        )

    def log_job_error(
        self,
        job_id: str,
        error: str,
        task_id: Optional[str] = None
    ) -> None:
        """Log SLURM job error.

        Args:
            job_id: SLURM job ID
            error: Error message
            task_id: Associated task ID
        """
        data = {
            'job_id': job_id,
            'task_id': task_id,
            'error': error
        }

        self._write_log('job_error', data, 'ERROR')

        extra = {
            'slurm_job_id': job_id,
            'event': 'job_error',
            'data': data
        }
        if task_id:
            extra['task_id'] = task_id

        self.logger.error(
            f"Job {job_id} error: {error}",
            extra=extra
        )

    def log_job_cancellation(
        self,
        job_id: str,
        reason: str,
        task_id: Optional[str] = None
    ) -> None:
        """Log SLURM job cancellation.

        Args:
            job_id: SLURM job ID
            reason: Cancellation reason
            task_id: Associated task ID
        """
        data = {
            'job_id': job_id,
            'task_id': task_id,
            'reason': reason
        }

        self._write_log('job_cancellation', data, 'WARNING')

        extra = {
            'slurm_job_id': job_id,
            'event': 'job_cancellation',
            'data': data
        }
        if task_id:
            extra['task_id'] = task_id

        self.logger.warning(
            f"Job {job_id} cancelled: {reason}",
            extra=extra
        )

    def log_submission_failure(
        self,
        task_id: str,
        script_path: str,
        error: str
    ) -> None:
        """Log failed job submission.

        Args:
            task_id: Task ID
            script_path: Path to job script
            error: Error message
        """
        data = {
            'task_id': task_id,
            'script_path': script_path,
            'error': error
        }

        self._write_log('submission_failure', data, 'ERROR')

        self.logger.error(
            f"Failed to submit job for task {task_id}: {error}",
            extra={
                'task_id': task_id,
                'event': 'submission_failure',
                'data': data
            }
        )
