"""
SLURM job monitor for background tracking.

Monitors job status changes and provides callbacks for events.
"""

import threading
import time
from typing import List, Dict, Any, Callable, Optional
from .client import SlurmClient
from .job import SlurmJob, SlurmJobStatus


class JobMonitor:
    """
    Background monitor for SLURM jobs.

    Polls job status at regular intervals and triggers callbacks
    on status changes.
    """

    def __init__(
        self,
        client: Optional[SlurmClient] = None,
        poll_interval: int = 30
    ):
        """
        Initialize job monitor.

        Args:
            client: SlurmClient instance (creates new one if None)
            poll_interval: Seconds between status checks
        """
        self.client = client or SlurmClient()
        self.poll_interval = poll_interval

        # Tracked jobs
        self.jobs: Dict[str, SlurmJob] = {}

        # Callbacks
        self.on_start_callbacks: List[Callable[[SlurmJob], None]] = []
        self.on_complete_callbacks: List[Callable[[SlurmJob], None]] = []
        self.on_fail_callbacks: List[Callable[[SlurmJob], None]] = []
        self.on_status_change_callbacks: List[Callable[[SlurmJob, SlurmJobStatus, SlurmJobStatus], None]] = []

        # Monitoring control
        self._monitoring = False
        self._monitor_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()

    def add_job(self, job: SlurmJob):
        """
        Add job to monitoring.

        Args:
            job: SlurmJob instance to monitor
        """
        if job.job_id:
            self.jobs[job.job_id] = job

    def remove_job(self, job_id: str):
        """
        Remove job from monitoring.

        Args:
            job_id: Job ID to remove
        """
        self.jobs.pop(job_id, None)

    def on_start(self, callback: Callable[[SlurmJob], None]):
        """
        Register callback for job start event.

        Args:
            callback: Function to call when job starts
        """
        self.on_start_callbacks.append(callback)

    def on_complete(self, callback: Callable[[SlurmJob], None]):
        """
        Register callback for job completion event.

        Args:
            callback: Function to call when job completes
        """
        self.on_complete_callbacks.append(callback)

    def on_fail(self, callback: Callable[[SlurmJob], None]):
        """
        Register callback for job failure event.

        Args:
            callback: Function to call when job fails
        """
        self.on_fail_callbacks.append(callback)

    def on_status_change(
        self,
        callback: Callable[[SlurmJob, SlurmJobStatus, SlurmJobStatus], None]
    ):
        """
        Register callback for any status change.

        Args:
            callback: Function(job, old_status, new_status) to call on status change
        """
        self.on_status_change_callbacks.append(callback)

    def start_monitoring(self, job_ids: Optional[List[str]] = None):
        """
        Start background monitoring.

        Args:
            job_ids: Optional list of job IDs to monitor (monitors all tracked jobs if None)
        """
        if self._monitoring:
            return

        # Filter jobs if specific IDs provided
        if job_ids:
            self.jobs = {
                job_id: job for job_id, job in self.jobs.items()
                if job_id in job_ids
            }

        self._monitoring = True
        self._stop_event.clear()
        self._monitor_thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self._monitor_thread.start()

    def stop_monitoring(self):
        """Stop background monitoring."""
        if not self._monitoring:
            return

        self._monitoring = False
        self._stop_event.set()

        if self._monitor_thread:
            self._monitor_thread.join(timeout=5)
            self._monitor_thread = None

    def _monitor_loop(self):
        """Main monitoring loop (runs in background thread)."""
        while self._monitoring and not self._stop_event.is_set():
            try:
                self._check_all_jobs()
            except Exception as e:
                # Log error but continue monitoring
                print(f"Error in monitoring loop: {e}")

            # Wait for next poll or stop event
            self._stop_event.wait(timeout=self.poll_interval)

    def _check_all_jobs(self):
        """Check status of all tracked jobs."""
        # Copy job list to avoid modification during iteration
        jobs_to_check = list(self.jobs.values())

        for job in jobs_to_check:
            if not job.job_id:
                continue

            # Skip finished jobs
            if job.is_finished:
                continue

            # Get current status
            old_status = job.status
            self.client.update_job_from_slurm(job)
            new_status = job.status

            # Check for status change
            if old_status != new_status:
                self._handle_status_change(job, old_status, new_status)

    def _handle_status_change(
        self,
        job: SlurmJob,
        old_status: SlurmJobStatus,
        new_status: SlurmJobStatus
    ):
        """
        Handle job status change.

        Args:
            job: Job that changed
            old_status: Previous status
            new_status: New status
        """
        # Trigger status change callbacks
        for callback in self.on_status_change_callbacks:
            try:
                callback(job, old_status, new_status)
            except Exception as e:
                print(f"Error in status change callback: {e}")

        # Trigger specific event callbacks
        if old_status == SlurmJobStatus.PENDING and new_status == SlurmJobStatus.RUNNING:
            # Job started
            for callback in self.on_start_callbacks:
                try:
                    callback(job)
                except Exception as e:
                    print(f"Error in start callback: {e}")

        elif new_status in (SlurmJobStatus.COMPLETED, SlurmJobStatus.CANCELLED):
            if job.succeeded:
                # Job completed successfully
                for callback in self.on_complete_callbacks:
                    try:
                        callback(job)
                    except Exception as e:
                        print(f"Error in complete callback: {e}")
            else:
                # Job failed
                for callback in self.on_fail_callbacks:
                    try:
                        callback(job)
                    except Exception as e:
                        print(f"Error in fail callback: {e}")

        elif new_status in (
            SlurmJobStatus.FAILED,
            SlurmJobStatus.TIMEOUT,
            SlurmJobStatus.OUT_OF_MEMORY,
            SlurmJobStatus.NODE_FAIL
        ):
            # Job failed
            for callback in self.on_fail_callbacks:
                try:
                    callback(job)
                except Exception as e:
                    print(f"Error in fail callback: {e}")

    def get_queue_snapshot(self) -> Dict[str, Dict[str, Any]]:
        """
        Get current state of all tracked jobs.

        Returns:
            Dictionary mapping job_id to job state dictionary
        """
        snapshot = {}

        for job_id, job in self.jobs.items():
            snapshot[job_id] = {
                'job_name': job.job_name,
                'status': job.status.value,
                'is_finished': job.is_finished,
                'is_running': job.is_running,
                'succeeded': job.succeeded if job.is_finished else None,
                'submit_time': job.submit_time.isoformat() if job.submit_time else None,
                'start_time': job.start_time.isoformat() if job.start_time else None,
                'end_time': job.end_time.isoformat() if job.end_time else None,
                'duration': job.duration,
            }

        return snapshot

    def wait_for_completion(self, timeout: Optional[int] = None) -> Dict[str, bool]:
        """
        Wait for all tracked jobs to complete.

        Args:
            timeout: Maximum seconds to wait (None = no timeout)

        Returns:
            Dictionary mapping job_id to success status
        """
        start_time = time.time()

        while True:
            # Check if all jobs finished
            all_finished = all(job.is_finished for job in self.jobs.values())
            if all_finished:
                break

            # Check timeout
            if timeout and (time.time() - start_time) > timeout:
                break

            # Wait a bit
            time.sleep(1)

        # Return success status for each job
        return {
            job_id: job.succeeded
            for job_id, job in self.jobs.items()
        }

    def get_running_count(self) -> int:
        """Get number of currently running jobs."""
        return sum(1 for job in self.jobs.values() if job.is_running)

    def get_pending_count(self) -> int:
        """Get number of pending jobs."""
        return sum(1 for job in self.jobs.values() if job.is_pending)

    def get_finished_count(self) -> int:
        """Get number of finished jobs."""
        return sum(1 for job in self.jobs.values() if job.is_finished)

    def get_success_count(self) -> int:
        """Get number of successfully completed jobs."""
        return sum(1 for job in self.jobs.values() if job.succeeded)

    def get_failed_count(self) -> int:
        """Get number of failed jobs."""
        return sum(1 for job in self.jobs.values() if job.failed)

    def __enter__(self):
        """Context manager entry."""
        self.start_monitoring()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit."""
        self.stop_monitoring()
        return False
