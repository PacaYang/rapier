"""
SLURM job script generator.

Generates SLURM batch scripts from templates and task specifications.
"""

from pathlib import Path
from typing import Dict, Any, List, Optional
from jinja2 import Environment, FileSystemLoader, Template
from .resources import ResourceParser
from .job import SlurmJob


class ScriptGenerator:
    """
    Generator for SLURM batch scripts.

    Uses Jinja2 templates to generate job scripts with proper
    SLURM directives and command execution.
    """

    def __init__(self, template_dir: Optional[Path] = None):
        """
        Initialize script generator.

        Args:
            template_dir: Directory containing Jinja2 templates
        """
        if template_dir is None:
            # Use default template directory
            template_dir = Path(__file__).parent / 'templates'

        self.template_dir = Path(template_dir)
        self.env = Environment(
            loader=FileSystemLoader(str(self.template_dir)),
            trim_blocks=True,
            lstrip_blocks=True
        )
        self.resource_parser = ResourceParser()

    def generate_script(
        self,
        job: SlurmJob,
        command: str,
        work_dir: Optional[Path] = None,
        modules: Optional[List[str]] = None,
        conda_env: Optional[str] = None,
        virtualenv: Optional[Path] = None,
        env_vars: Optional[Dict[str, str]] = None,
        pre_commands: Optional[List[str]] = None,
        post_commands: Optional[List[str]] = None,
        array_spec: Optional[str] = None,
        account: Optional[str] = None,
        qos: Optional[str] = None,
        template_name: str = 'job_template.sh.j2'
    ) -> str:
        """
        Generate SLURM batch script.

        Args:
            job: SlurmJob instance with resource specifications
            command: Main command to execute
            work_dir: Working directory for job execution
            modules: List of environment modules to load
            conda_env: Conda environment name to activate
            virtualenv: Path to virtual environment to activate
            env_vars: Environment variables to set
            pre_commands: Commands to run before main command
            post_commands: Commands to run after main command
            array_spec: Array job specification (e.g., "1-100")
            account: SLURM account to charge
            qos: Quality of service
            template_name: Name of Jinja2 template to use

        Returns:
            Generated script content
        """
        # Parse resources
        resources = job.resources
        cpus = None
        memory_mb = None
        time_limit = None
        gpus = 0
        gpu_type = None
        partition = None

        if 'cpus' in resources:
            cpus = self.resource_parser.parse_cpus(resources['cpus'])

        if 'memory' in resources:
            memory_mb = self.resource_parser.parse_memory(resources['memory'])

        if 'time_limit' in resources:
            time_sec = self.resource_parser.parse_time(resources['time_limit'])
            time_limit = self.resource_parser.format_time_for_slurm(time_sec)

        if 'gpus' in resources:
            gpu_spec = self.resource_parser.parse_gpus(resources['gpus'])
            gpus = gpu_spec['count']
            gpu_type = gpu_spec.get('type')

        if 'partition' in resources:
            partition = resources['partition']

        # Get dependency string
        dependency_string = job.get_dependency_string()

        # Prepare template context
        context = {
            'job_name': job.job_name,
            'stdout_path': str(job.stdout_path) if job.stdout_path else f'{job.job_name}.out',
            'stderr_path': str(job.stderr_path) if job.stderr_path else f'{job.job_name}.err',
            'partition': partition,
            'cpus': cpus,
            'memory_mb': memory_mb,
            'time_limit': time_limit,
            'gpus': gpus,
            'gpu_type': gpu_type,
            'dependency_string': dependency_string,
            'array_spec': array_spec,
            'account': account,
            'qos': qos,
            'work_dir': str(work_dir) if work_dir else str(job.work_dir) if job.work_dir else None,
            'modules': modules or [],
            'conda_env': conda_env,
            'virtualenv': str(virtualenv) if virtualenv else None,
            'env_vars': env_vars or {},
            'pre_commands': pre_commands or [],
            'command': command,
            'post_commands': post_commands or [],
        }

        # Load and render template
        template = self.env.get_template(template_name)
        script = template.render(**context)

        return script

    def generate_from_task(
        self,
        task: 'Task',
        command: str,
        output_dir: Optional[Path] = None,
        **kwargs
    ) -> str:
        """
        Generate script from a Task object.

        Args:
            task: Task instance
            command: Command to execute
            output_dir: Directory for output files
            **kwargs: Additional arguments for generate_script

        Returns:
            Generated script content
        """
        # Create SlurmJob from task
        if output_dir:
            output_dir = Path(output_dir)
        else:
            output_dir = task.work_dir if task.work_dir else Path.cwd()

        job = SlurmJob(
            job_name=task.task_id,
            work_dir=output_dir,
            stdout_path=output_dir / f'{task.task_id}.out',
            stderr_path=output_dir / f'{task.task_id}.err',
            resources=task.metadata.get('resources', {}),
        )

        return self.generate_script(job, command, **kwargs)

    def write_script(
        self,
        script_content: str,
        output_path: Path
    ) -> Path:
        """
        Write script to file and make it executable.

        Args:
            script_content: Script content
            output_path: Path to write script

        Returns:
            Path to written script
        """
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        with open(output_path, 'w') as f:
            f.write(script_content)

        # Make script executable
        output_path.chmod(0o755)

        return output_path

    def generate_and_write(
        self,
        job: SlurmJob,
        command: str,
        output_path: Path,
        **kwargs
    ) -> Path:
        """
        Generate script and write to file.

        Args:
            job: SlurmJob instance
            command: Command to execute
            output_path: Path to write script
            **kwargs: Additional arguments for generate_script

        Returns:
            Path to written script
        """
        script = self.generate_script(job, command, **kwargs)
        return self.write_script(script, output_path)

    def generate_array_job_script(
        self,
        job: SlurmJob,
        command_template: str,
        array_size: int,
        work_dir: Optional[Path] = None,
        **kwargs
    ) -> str:
        """
        Generate script for SLURM array job.

        The command_template can use $SLURM_ARRAY_TASK_ID to reference
        the array task ID.

        Args:
            job: SlurmJob instance
            command_template: Command template with $SLURM_ARRAY_TASK_ID
            array_size: Number of array tasks
            work_dir: Working directory
            **kwargs: Additional arguments for generate_script

        Returns:
            Generated script content

        Example:
            >>> command = "python process.py --input data_$SLURM_ARRAY_TASK_ID.sdf"
            >>> script = generator.generate_array_job_script(
            ...     job, command, array_size=100
            ... )
        """
        array_spec = f"1-{array_size}"

        return self.generate_script(
            job=job,
            command=command_template,
            work_dir=work_dir,
            array_spec=array_spec,
            **kwargs
        )
