"""
Task execution runner.

Executes individual tasks, handling preparation, submission, monitoring, and collection.
"""

from typing import Optional, Dict, Any
from pathlib import Path
from rapier.core.task import Task
from rapier.core.enums import TaskStatus
from rapier.slurm import SlurmClient, SlurmJob, ScriptGenerator
from rapier.artifacts import InputResolver


class TaskRunner:
    """
    Executes individual tasks.

    Handles the complete task lifecycle:
    1. Validation and staging of inputs (runtime dependencies are checked in execute)
    2. Preparation (working directory, input files)
    3. Submission (SLURM job generation and submission)
    4. Monitoring (job status tracking)
    5. Collection (gathering outputs and results)
    """

    def __init__(
        self,
        slurm_client: Optional[SlurmClient] = None,
        script_generator: Optional[ScriptGenerator] = None,
        dry_run: bool = False
    ):
        """
        Initialize task runner.

        Args:
            slurm_client: SLURM client for job submission
            script_generator: Script generator for job scripts
            dry_run: If True, don't actually submit jobs
        """
        self.slurm_client = slurm_client or SlurmClient(dry_run=dry_run)
        self.script_generator = script_generator or ScriptGenerator()
        self.dry_run = dry_run
        self.input_resolver = InputResolver()

    def run_task(self, task: Task) -> TaskStatus:
        """
        Execute a task completely.

        Args:
            task: Task to execute

        Returns:
            Final task status
        """
        try:
            # Prepare task
            self.prepare_task(task)

            # Submit task
            job_id = self.submit_task(task)

            if job_id:
                # Monitor task (blocking)
                self.monitor_task(task)

                # Collect results
                self.collect_task_results(task)

            return task.status

        except Exception as e:
            task.status = TaskStatus.FAILED
            task.error_message = str(e)
            return TaskStatus.FAILED

    def prepare_task(self, task: Task) -> bool:
        """
        Prepare task for execution.

        Validates and stages inputs, creates working directory.

        Args:
            task: Task to prepare

        Returns:
            True if preparation successful

        Raises:
            ValueError: If validation fails
        """
        # Create working directory if not exists
        if not task.work_dir:
            task.work_dir = Path.cwd() / 'runs' / task.task_id

        task.work_dir = Path(task.work_dir)
        task.work_dir.mkdir(parents=True, exist_ok=True)

        # Create subdirectories
        if not task.input_dir:
            task.input_dir = task.work_dir / 'input'
        task.input_dir.mkdir(parents=True, exist_ok=True)

        if not task.output_dir:
            task.output_dir = task.work_dir / 'output'
        task.output_dir.mkdir(parents=True, exist_ok=True)

        if not task.log_dir:
            task.log_dir = task.work_dir / 'logs'
        task.log_dir.mkdir(parents=True, exist_ok=True)

        # Resolve bindings, then let the method type, convert and stage its inputs.
        raw_inputs = self.input_resolver.resolve_task(task)

        if task.method:
            task.inputs = task.method.format_inputs(raw_inputs, task.input_dir / 'normalized')

            is_valid, errors = task.method.validate_inputs(task.inputs)
            if not is_valid:
                raise ValueError(f"Input validation failed: {', '.join(errors)}")

        else:
            task.inputs = raw_inputs

        return True

    def submit_task(self, task: Task) -> Optional[str]:
        """
        Submit task to SLURM.

        Args:
            task: Task to submit

        Returns:
            Job ID if submitted, None otherwise
        """
        # Generate command for task
        if task.method:
            command = task.method.get_command(task)
        else:
            command = 'echo "No method specified"'

        # Create SLURM job
        resources = task.metadata.get('resources', {})
        job = SlurmJob(
            job_name=task.task_id,
            resources=resources,
            work_dir=task.work_dir,
            stdout_path=task.log_dir / f'{task.task_id}.out',
            stderr_path=task.log_dir / f'{task.task_id}.err'
        )

        # Generate script
        script = self.script_generator.generate_script(
            job,
            command=command,
            work_dir=task.work_dir,
            modules=task.metadata.get('modules', []),
            conda_env=task.metadata.get('conda_env'),
            env_vars=task.metadata.get('env_vars', {})
        )

        # Write script
        script_path = task.work_dir / f'{task.task_id}.sh'
        self.script_generator.write_script(script, script_path)

        # Store script path in task
        task.slurm_script_path = script_path
        job.script_path = script_path

        # Submit to SLURM
        job_id = self.slurm_client.submit_job(script_path)

        # Update task
        task.slurm_job_id = job_id
        job.job_id = job_id
        task.status = TaskStatus.RUNNING

        return job_id

    def monitor_task(self, task: Task):
        """
        Monitor task execution until completion.

        Blocks until task finishes.

        Args:
            task: Task to monitor
        """
        if not task.slurm_job_id:
            return

        # Create SLURM job for monitoring
        job = SlurmJob(
            job_name=task.task_id,
            job_id=task.slurm_job_id
        )

        # Update job status from SLURM
        self.slurm_client.update_job_from_slurm(job)

        # Update task from job
        task.update_from_slurm_job(job)

    def collect_task_results(self, task: Task):
        """
        Collect results from completed task.

        Args:
            task: Completed task
        """
        # Read outputs from method (a dry run never executed anything to collect)
        if task.method and not self.dry_run:
            try:
                # Methods return outputs already arranged as typed artifacts.
                task.outputs = dict(task.method.collect_outputs(task))
            except Exception as e:
                task.outputs = {}
                task.status = TaskStatus.FAILED
                task.error_message = f"Failed to collect outputs: {e}"

        # Read log files
        if task.log_dir:
            stdout_file = task.log_dir / f'{task.task_id}.out'
            stderr_file = task.log_dir / f'{task.task_id}.err'

            if stdout_file.exists():
                with open(stdout_file, 'r') as f:
                    task.metadata['stdout'] = f.read()

            if stderr_file.exists():
                with open(stderr_file, 'r') as f:
                    task.metadata['stderr'] = f.read()

    def get_task_status(self, task: Task) -> TaskStatus:
        """
        Get current status of a task.

        Args:
            task: Task to check

        Returns:
            Current task status
        """
        if not task.slurm_job_id:
            return task.status

        # Query SLURM
        status_str = self.slurm_client.get_job_status(task.slurm_job_id)

        if status_str:
            # Map SLURM status to TaskStatus
            status_map = {
                'PENDING': TaskStatus.PENDING,
                'RUNNING': TaskStatus.RUNNING,
                'COMPLETED': TaskStatus.COMPLETED,
                'FAILED': TaskStatus.FAILED,
                'CANCELLED': TaskStatus.FAILED,
                'TIMEOUT': TaskStatus.FAILED,
            }
            task.status = status_map.get(status_str, TaskStatus.RUNNING)

        return task.status

    def cancel_task(self, task: Task) -> bool:
        """
        Cancel a running task.

        Args:
            task: Task to cancel

        Returns:
            True if cancellation successful
        """
        if not task.slurm_job_id:
            return False

        success = self.slurm_client.cancel_job(task.slurm_job_id)

        if success:
            task.status = TaskStatus.FAILED
            task.error_message = "Task cancelled"

        return success
