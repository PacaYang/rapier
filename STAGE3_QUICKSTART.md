# Stage 3: SLURM Integration - Quick Reference

## Installation

No additional dependencies required beyond Stage 1 & 2. Jinja2 is already available.

## Quick Start

### 1. Generate a SLURM Job Script

```python
from rapier.slurm import ScriptGenerator, SlurmJob
from pathlib import Path

# Create job with resource requirements
job = SlurmJob(
    job_name='my_screening_job',
    resources={
        'cpus': 8,
        'memory': '16GB',
        'time_limit': '2h',
        'partition': 'compute'
    }
)

# Generate script
generator = ScriptGenerator()
script = generator.generate_script(
    job,
    command='python screen.py --input molecules.sdf',
    modules=['anaconda3'],
    conda_env='screening'
)

# Write to file
script_path = generator.write_script(script, Path('job.sh'))
```

### 2. Submit Job to SLURM

```python
from rapier.slurm import SlurmClient

client = SlurmClient()
job_id = client.submit_job(script_path)
print(f"Submitted job: {job_id}")
```

### 3. Monitor Job Status

```python
# Simple status check
status = client.get_job_status(job_id)
print(f"Status: {status}")

# Detailed information
info = client.get_job_info(job_id)
print(f"Job info: {info}")
```

### 4. Background Monitoring with Callbacks

```python
from rapier.slurm import JobMonitor

monitor = JobMonitor(client=client, poll_interval=30)

# Register callbacks
def on_complete(job):
    print(f"Job {job.job_name} completed!")

monitor.on_complete(on_complete)

# Add job and start monitoring
monitor.add_job(job)
monitor.start_monitoring()

# Wait for completion
results = monitor.wait_for_completion()
```

## Common Tasks

### Parse Resource Specifications

```python
from rapier.slurm import ResourceParser

parser = ResourceParser()

# Memory
mem_mb = parser.parse_memory('4GB')      # 4096
mem_mb = parser.parse_memory('512MB')    # 512

# Time
seconds = parser.parse_time('1h')        # 3600
seconds = parser.parse_time('2:30:00')   # 9000

# Format time for SLURM
slurm_time = parser.format_time_for_slurm(9000)  # "02:30:00"

# GPUs
gpu_spec = parser.parse_gpus('2xV100')   # {'count': 2, 'type': 'V100'}
```

### Job Dependencies

```python
from rapier.slurm import DependencyBuilder

builder = DependencyBuilder()

# Simple dependency (job2 runs after job1 succeeds)
dep_str = builder.build_dependency_string([job1], 'afterok')

# Multiple dependencies (job3 runs after job1 AND job2 succeed)
dep_str = builder.build_dependency_string([job1, job2], 'afterok')

# Run regardless of success/failure
dep_str = builder.build_dependency_string([job1], 'afterany')

# Submit with dependency
client.submit_job('job2.sh', dependencies=[job_id_1], dependency_type='afterok')
```

### Array Jobs (Parallel Execution)

```python
# Generate array job for 100 parallel tasks
script = generator.generate_array_job_script(
    job,
    command_template='python process.py --input ligand_${SLURM_ARRAY_TASK_ID}.sdf',
    array_size=100
)

# This creates 100 jobs with task IDs 1-100
```

### Check SLURM Availability

```python
from rapier.slurm import SlurmConfig

config = SlurmConfig()
if config.is_slurm_available():
    config.read_cluster_info()
    partitions = config.get_partitions()
    print(f"Available partitions: {partitions}")
```

### Dry-Run Mode (Testing Without SLURM)

```python
# Create client in dry-run mode
client = SlurmClient(dry_run=True)

# All operations work but don't actually submit to SLURM
job_id = client.submit_job(script_path)  # Returns fake job ID
status = client.get_job_status(job_id)   # Returns "COMPLETED"
```

## Resource Specifications

### Memory Units
- `KB`: Kilobytes
- `MB`: Megabytes (default SLURM unit)
- `GB`: Gigabytes
- `TB`: Terabytes

### Time Units
- `s`: Seconds
- `m`: Minutes
- `h`: Hours
- `d`: Days
- SLURM format: `HH:MM:SS` or `DD-HH:MM:SS`

### GPU Specifications
- Integer: `2` (any GPU type)
- Type + count: `"2xV100"`, `"A100:4"`

## Job Status Values

- `PENDING`: Waiting in queue
- `RUNNING`: Currently executing
- `COMPLETED`: Finished successfully
- `FAILED`: Failed
- `CANCELLED`: Cancelled by user
- `TIMEOUT`: Exceeded time limit
- `OUT_OF_MEMORY`: Ran out of memory

## Dependency Types

- `afterok`: Start after jobs complete successfully
- `afterany`: Start after jobs complete (any exit code)
- `afternotok`: Start after jobs fail
- `singleton`: Only one job with this name runs at a time

## Complete Example

```python
from rapier.slurm import (
    SlurmJob,
    SlurmClient,
    ScriptGenerator,
    JobMonitor
)
from pathlib import Path

# 1. Create job
job = SlurmJob(
    job_name='docking_pipeline',
    resources={
        'cpus': 16,
        'memory': '32GB',
        'time_limit': '4h',
        'partition': 'compute',
        'gpus': 1
    }
)

# 2. Generate script
generator = ScriptGenerator()
script_path = generator.generate_and_write(
    job,
    command='python run_docking.py --config pipeline.yaml',
    output_path=Path('docking.sh'),
    modules=['cuda/11.8', 'anaconda3'],
    conda_env='drug_discovery'
)

# 3. Submit job
client = SlurmClient()
job_id = client.submit_job(script_path)
job.job_id = job_id

print(f"Submitted job {job_id}")

# 4. Monitor with callbacks
monitor = JobMonitor(client=client)

def on_complete(job):
    print(f"✓ Job completed: {job.job_name}")
    print(f"  Duration: {job.duration} seconds")

def on_fail(job):
    print(f"✗ Job failed: {job.job_name}")
    errors = job.get_errors()
    if errors:
        print(f"  Errors: {errors[:200]}")

monitor.on_complete(on_complete)
monitor.on_fail(on_fail)
monitor.add_job(job)

# Start monitoring
monitor.start_monitoring()

# Wait for completion
result = monitor.wait_for_completion(timeout=14400)  # 4 hours
print(f"Job succeeded: {result[job_id]}")

# Stop monitoring
monitor.stop_monitoring()
```

## Integration with Pipeline Configuration

```python
from rapier.config import ConfigParser
from rapier.core import Pipeline
from rapier.slurm import ScriptGenerator, SlurmJob

# Parse pipeline configuration
config = ConfigParser().parse_file('pipeline.yaml')
pipeline = Pipeline.from_config(config)

# Generate SLURM scripts for each task
generator = ScriptGenerator()

for task_id, task in pipeline.tasks.items():
    # Get resources from task metadata
    resources = task.metadata.get('resources', {})
    
    # Create SLURM job
    job = SlurmJob(job_name=task_id, resources=resources)
    
    # Generate script
    script = generator.generate_from_task(
        task,
        command=f'python run_method.py --task {task_id}'
    )
    
    # Write script
    script_path = Path(f'jobs/{task_id}.sh')
    generator.write_script(script, script_path)
```

## Testing

Run the demo to see all features:

```bash
python3 demo_stage3.py
```

## Troubleshooting

### SLURM Not Available
If SLURM commands are not found:
- Use `dry_run=True` for development/testing
- The system will work normally but not submit real jobs

### Script Generation Issues
- Check template directory: `rapier/slurm/templates/`
- Verify Jinja2 is installed: `python3 -c "import jinja2"`

### Job Monitoring Not Working
- Check poll interval (default: 30 seconds)
- Verify job IDs are valid
- Use callbacks to track events

## Next Steps

- See `STAGE3_SUMMARY.md` for complete documentation
- Run `demo_stage3.py` for interactive examples
- Proceed to Stage 4 for execution engine integration
