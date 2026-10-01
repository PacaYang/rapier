"""
SLURM client for job submission and management.

Provides interface to SLURM commands via subprocess.
"""

import subprocess
import re
from pathlib import Path
from typing import Optional, List, Dict, Any
from datetime import datetime
from .job import SlurmJob, SlurmJobStatus


class SlurmClient:
    """
    Client for interacting with SLURM cluster.

    Uses subprocess to call SLURM commands: sbatch, scancel, squeue, sacct.
    """

    def __init__(self, dry_run: bool = False):
        """
        Initialize SLURM client.

        Args:
            dry_run: If True, don't actually submit jobs (for testing)
        """
        self.dry_run = dry_run
        self._next_fake_id = 1000

    def submit_job(
        self,
        script_path: Path,
        dependencies: Optional[List[str]] = None,
        dependency_type: str = 'afterok'
    ) -> str:
        """
        Submit job to SLURM.

        Args:
            script_path: Path to job script
            dependencies: List of job IDs this job depends on
            dependency_type: Type of dependency ('afterok', 'afterany', etc.)

        Returns:
            Job ID assigned by SLURM

        Raises:
            RuntimeError: If submission fails
        """
        if self.dry_run:
            job_id = str(self._next_fake_id)
            self._next_fake_id += 1
            return job_id

        script_path = Path(script_path)
        if not script_path.exists():
            raise FileNotFoundError(f"Script not found: {script_path}")

        # Build sbatch command
        cmd = ['sbatch']

        # Add dependency if specified
        if dependencies:
            dep_str = f"{dependency_type}:{':'.join(dependencies)}"
            cmd.extend(['--dependency', dep_str])

        cmd.append(str(script_path))

        # Submit job
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=30
            )

            if result.returncode != 0:
                raise RuntimeError(f"sbatch failed: {result.stderr}")

            # Parse job ID from output
            # Expected format: "Submitted batch job 12345"
            match = re.search(r'Submitted batch job (\d+)', result.stdout)
            if not match:
                raise RuntimeError(f"Could not parse job ID from: {result.stdout}")

            job_id = match.group(1)
            return job_id

        except subprocess.TimeoutExpired:
            raise RuntimeError("sbatch command timed out")
        except FileNotFoundError:
            raise RuntimeError("sbatch command not found. Is SLURM installed?")

    def cancel_job(self, job_id: str) -> bool:
        """
        Cancel a SLURM job.

        Args:
            job_id: Job ID to cancel

        Returns:
            True if successful, False otherwise
        """
        if self.dry_run:
            return True

        try:
            result = subprocess.run(
                ['scancel', job_id],
                capture_output=True,
                text=True,
                timeout=10
            )
            return result.returncode == 0

        except Exception:
            return False

    def get_job_status(self, job_id: str) -> Optional[str]:
        """
        Get current status of a job.

        Args:
            job_id: Job ID

        Returns:
            Job status string (e.g., "PENDING", "RUNNING", "COMPLETED") or None
        """
        if self.dry_run:
            return "COMPLETED"

        # Try squeue first (for running/pending jobs)
        status = self._get_status_from_squeue(job_id)
        if status:
            return status

        # Try sacct for completed jobs
        return self._get_status_from_sacct(job_id)

    def _get_status_from_squeue(self, job_id: str) -> Optional[str]:
        """Query job status from squeue."""
        try:
            result = subprocess.run(
                ['squeue', '-j', job_id, '-o', '%T', '--noheader'],
                capture_output=True,
                text=True,
                timeout=10
            )

            if result.returncode == 0 and result.stdout.strip():
                return result.stdout.strip()

        except Exception:
            pass

        return None

    def _get_status_from_sacct(self, job_id: str) -> Optional[str]:
        """Query job status from sacct."""
        try:
            result = subprocess.run(
                ['sacct', '-j', job_id, '-o', 'State', '--noheader', '-P'],
                capture_output=True,
                text=True,
                timeout=10
            )

            if result.returncode == 0 and result.stdout.strip():
                # Return first line (main job, not job steps)
                return result.stdout.strip().split('\n')[0]

        except Exception:
            pass

        return None

    def get_job_info(self, job_id: str) -> Optional[Dict[str, Any]]:
        """
        Get detailed information about a job.

        Args:
            job_id: Job ID

        Returns:
            Dictionary with job information or None
        """
        if self.dry_run:
            return {
                'job_id': job_id,
                'state': 'COMPLETED',
                'exit_code': 0,
            }

        try:
            # Query sacct for detailed info
            result = subprocess.run(
                [
                    'sacct', '-j', job_id,
                    '-o', 'JobID,JobName,State,ExitCode,Start,End,Elapsed,NodeList',
                    '--noheader', '-P'
                ],
                capture_output=True,
                text=True,
                timeout=10
            )

            if result.returncode != 0 or not result.stdout.strip():
                return None

            # Parse output (first line is main job)
            line = result.stdout.strip().split('\n')[0]
            fields = line.split('|')

            if len(fields) < 8:
                return None

            info = {
                'job_id': fields[0],
                'job_name': fields[1],
                'state': fields[2],
                'exit_code': self._parse_exit_code(fields[3]),
                'start_time': self._parse_time(fields[4]),
                'end_time': self._parse_time(fields[5]),
                'elapsed': fields[6],
                'node_list': fields[7],
            }

            return info

        except Exception:
            return None

    def _parse_exit_code(self, exit_str: str) -> Optional[int]:
        """Parse exit code from sacct format (e.g., '0:0')."""
        if not exit_str or exit_str == 'Unknown':
            return None

        try:
            # Format is typically "ExitCode:Signal"
            parts = exit_str.split(':')
            return int(parts[0])
        except Exception:
            return None

    def _parse_time(self, time_str: str) -> Optional[datetime]:
        """Parse timestamp from sacct."""
        if not time_str or time_str == 'Unknown':
            return None

        try:
            # Format: YYYY-MM-DDTHH:MM:SS
            return datetime.fromisoformat(time_str)
        except Exception:
            return None

    def update_job_from_slurm(self, job: SlurmJob) -> bool:
        """
        Update job object with current SLURM information.

        Args:
            job: SlurmJob instance to update

        Returns:
            True if update successful, False otherwise
        """
        if not job.job_id:
            return False

        info = self.get_job_info(job.job_id)
        if not info:
            # Try just status
            status = self.get_job_status(job.job_id)
            if status:
                job.update_status(status)
                return True
            return False

        # Update job with detailed info
        job.update_status(info['state'], info.get('exit_code'))
        job.start_time = info.get('start_time')
        job.end_time = info.get('end_time')

        return True

    def wait_for_jobs(
        self,
        job_ids: List[str],
        poll_interval: int = 30,
        timeout: Optional[int] = None
    ) -> Dict[str, str]:
        """
        Wait for jobs to complete.

        Args:
            job_ids: List of job IDs to wait for
            poll_interval: Seconds between status checks
            timeout: Maximum seconds to wait (None = no timeout)

        Returns:
            Dictionary mapping job_id to final status

        Note:
            This is a blocking call. For non-blocking monitoring,
            use JobMonitor class instead.
        """
        import time

        if self.dry_run:
            return {job_id: 'COMPLETED' for job_id in job_ids}

        start_time = time.time()
        statuses = {}

        while len(statuses) < len(job_ids):
            # Check timeout
            if timeout and (time.time() - start_time) > timeout:
                break

            # Check each job
            for job_id in job_ids:
                if job_id in statuses:
                    continue

                status = self.get_job_status(job_id)
                if status and status not in ('PENDING', 'RUNNING'):
                    statuses[job_id] = status

            # Sleep if not all done
            if len(statuses) < len(job_ids):
                time.sleep(poll_interval)

        return statuses

    def get_queue_info(self) -> List[Dict[str, Any]]:
        """
        Get information about all jobs in the queue.

        Returns:
            List of job information dictionaries
        """
        if self.dry_run:
            return []

        try:
            result = subprocess.run(
                ['squeue', '-o', '%i|%j|%t|%M|%D|%R', '--noheader'],
                capture_output=True,
                text=True,
                timeout=10
            )

            if result.returncode != 0:
                return []

            jobs = []
            for line in result.stdout.strip().split('\n'):
                if not line:
                    continue

                fields = line.split('|')
                if len(fields) < 6:
                    continue

                jobs.append({
                    'job_id': fields[0],
                    'name': fields[1],
                    'state': fields[2],
                    'time': fields[3],
                    'nodes': fields[4],
                    'reason': fields[5],
                })

            return jobs

        except Exception:
            return []
