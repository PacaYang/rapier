# Stage 3: SLURM Integration Layer

## Goal
Implement the interface between the platform and SLURM. This includes reading SLURM specifications, generating job scripts, submitting jobs, monitoring queue status, and retrieving results. Handle SLURM-specific resource allocation and job dependencies.

## Files to Create

### `rapier/slurm/__init__.py`
- SLURM module initialization
- Export main classes: `SlurmClient`, `SlurmJob`, `SlurmConfig`

### `rapier/slurm/config.py`
- `SlurmConfig` class
- Read SLURM cluster configuration
- Properties: available partitions, node specs, default resources
- Method: `read_cluster_info()` - queries `sinfo`, `scontrol show config`
- Validate partition names and resource limits

### `rapier/slurm/job.py`
- `SlurmJob` class representing a SLURM job
- Properties: `job_id`, `job_name`, `script_path`, `status`, `submit_time`, `start_time`, `end_time`
- Resource specs: `cpus`, `memory`, `gpus`, `time_limit`, `partition`
- Dependency specification: `after_ok`, `after_any`, `after_not_ok`
- Methods: `to_script()`, `parse_status()`, `get_output()`, `get_errors()`

### `rapier/slurm/script_generator.py`
- `ScriptGenerator` class
- Method: `generate_script(task: Task, method: BaseMethod) -> str`
- Create SLURM batch script with proper directives
- Handle module loading, environment activation, command execution
- Support for array jobs (multiple molecules in parallel)
- Template rendering with Jinja2

### `rapier/slurm/client.py`
- `SlurmClient` class - main interface to SLURM
- Methods:
  - `submit_job(script_path: str, dependencies: List[str]) -> str` - returns job_id
  - `cancel_job(job_id: str)`
  - `get_job_status(job_id: str) -> str` - queries `squeue`, `sacct`
  - `get_job_info(job_id: str) -> Dict` - detailed job information
  - `wait_for_jobs(job_ids: List[str], poll_interval: int)`
- Use subprocess to call SLURM commands: `sbatch`, `scancel`, `squeue`, `sacct`

### `rapier/slurm/monitor.py`
- `JobMonitor` class for tracking job status
- Background polling of job queue
- Methods:
  - `start_monitoring(job_ids: List[str], callback: Callable)`
  - `stop_monitoring()`
  - `get_queue_snapshot() -> Dict` - current state of all tracked jobs
- Event-based notifications: on_start, on_complete, on_fail

### `rapier/slurm/resources.py`
- `ResourceParser` class
- Parse resource specifications from strings
- Methods:
  - `parse_memory(spec: str) -> int` - "4GB" -> 4096 (MB)
  - `parse_time(spec: str) -> int` - "2:30:00" -> 9000 (seconds)
  - `parse_cpus(spec: str) -> int`
  - `parse_gpus(spec: str) -> Dict` - "2x V100" -> {'count': 2, 'type': 'V100'}
- Validation against cluster limits

### `rapier/slurm/templates/job_template.sh.j2`
- Jinja2 template for SLURM batch scripts
- Includes standard directives: `#SBATCH --job-name`, `--cpus-per-task`, etc.
- Module loading section
- Environment activation section
- Command execution section
- Error handling and logging

### `rapier/slurm/dependencies.py`
- `DependencyBuilder` class
- Convert task dependencies to SLURM dependency strings
- Methods:
  - `build_dependency_string(upstream_jobs: List[SlurmJob], policy: str) -> str`
  - Policies: `afterok`, `afterany`, `afternotok`, `aftercorr`
- Handle complex dependency patterns (all succeed, any complete, etc.)

## Files to Modify

### `rapier/core/base_method.py`
- Add method: `get_slurm_script(task: Task) -> str`
- Integration point for SLURM script generation

### `rapier/core/task.py`
- Add properties: `slurm_job_id`, `slurm_script_path`
- Add method: `update_from_slurm_job(job: SlurmJob)`

## Implementation Order

1. Implement resource parsing (`resources.py`) - used by other components
2. Create job data model (`job.py`) - represents SLURM jobs
3. Implement SLURM configuration reader (`config.py`)
4. Create job script template (`job_template.sh.j2`)
5. Implement script generator (`script_generator.py`) - uses template
6. Implement dependency builder (`dependencies.py`)
7. Implement SLURM client (`client.py`) - submits and queries jobs
8. Implement job monitor (`monitor.py`) - background tracking
9. Update core classes with SLURM integration points

## Verification Steps

```bash
# Test SLURM configuration reading
python3 -c "
from rapier.slurm import SlurmConfig
config = SlurmConfig()
partitions = config.get_partitions()
print(f'Available partitions: {partitions}')
"

# Test resource parsing
python3 -c "
from rapier.slurm.resources import ResourceParser
parser = ResourceParser()
mem_mb = parser.parse_memory('4GB')
assert mem_mb == 4096, f'Expected 4096, got {mem_mb}'
time_sec = parser.parse_time('2:30:00')
assert time_sec == 9000, f'Expected 9000, got {time_sec}'
print('✓ Resource parsing works')
"

# Test script generation
python3 -c "
from rapier.slurm import ScriptGenerator, SlurmJob
from rapier.core import Task
from rapier.core.specs import ResourceSpec

# Create mock task
task = Task(task_id='test_001', method=None, inputs={})
resource_spec = ResourceSpec(cpus=4, memory='8GB', time='1:00:00', partition='compute')

# Generate script
generator = ScriptGenerator()
script = generator.generate_script(task, resource_spec, command='echo Hello SLURM')
print('Generated script:')
print(script[:200])
assert '#SBATCH --job-name' in script
assert '#SBATCH --cpus-per-task=4' in script
print('✓ Script generation works')
"

# Test job submission (dry-run if no SLURM available)
python3 -c "
from rapier.slurm import SlurmClient
client = SlurmClient()
# Check if SLURM is available
import shutil
if shutil.which('sbatch'):
    print('✓ SLURM commands available')
    # Could do actual submission test here
else:
    print('⚠ SLURM not available (expected in development environment)')
"

# Test dependency string building
python3 -c "
from rapier.slurm.dependencies import DependencyBuilder
from rapier.slurm import SlurmJob

job1 = SlurmJob(job_id='12345', job_name='task_a')
job2 = SlurmJob(job_id='12346', job_name='task_b')

builder = DependencyBuilder()
dep_str = builder.build_dependency_string([job1, job2], policy='afterok')
assert dep_str == 'afterok:12345:12346', f'Got: {dep_str}'
print('✓ Dependency string building works')
"
```

## Key Decisions

**1. Subprocess vs Python SLURM Libraries**
- Use subprocess for calling `sbatch`, `squeue`, `sacct`
- More portable than pyslurm (which requires specific SLURM version)
- Easier to debug (can see exact commands)
- Fallback to SLURM REST API if needed in future

**2. Job Script Template System**
- Use Jinja2 for script generation
- Separates script structure from generation logic
- Easy for users to customize templates
- Supports includes for common sections

**3. Asynchronous Job Monitoring**
- JobMonitor runs in background thread
- Polls `squeue` at configurable interval (default: 30 seconds)
- Callbacks notify execution engine of status changes
- Avoids blocking main execution thread

**4. Dependency Policy Mapping**
- Map platform failure policies to SLURM dependency types:
  - `continue_on_fail` → `afterany` (run regardless of upstream status)
  - `require_success` → `afterok` (run only if upstream succeeded)
  - `run_on_fail` → `afternotok` (run only if upstream failed)

**5. Array Job Support**
- For embarrassingly parallel tasks (e.g., docking 10000 molecules)
- Use SLURM job arrays: `#SBATCH --array=1-10000`
- Reduces queue pressure vs 10000 individual jobs
- Implemented in ScriptGenerator with array task ID substitution
