#!/usr/bin/env python3
"""
Demonstration of Stage 3 SLURM Integration Layer

This script demonstrates all major features of the SLURM integration:
- Resource parsing and formatting
- Job script generation
- Dependency building
- Job submission and monitoring
- SLURM configuration
"""

import sys
import tempfile
from pathlib import Path

from rapier.slurm import (
    SlurmJob,
    SlurmJobStatus,
    SlurmClient,
    SlurmConfig,
    ScriptGenerator,
    DependencyBuilder,
    JobMonitor,
    ResourceParser
)


def print_section(title):
    """Print a formatted section header."""
    print(f"\n{'=' * 70}")
    print(f"  {title}")
    print(f"{'=' * 70}\n")


def demo_resource_parsing():
    """Demonstrate resource specification parsing."""
    print_section("1. Resource Parsing")

    parser = ResourceParser()

    # Memory parsing
    print("Memory Parsing:")
    for spec in ['512MB', '4GB', '2TB']:
        mb = parser.parse_memory(spec)
        print(f"  {spec:8s} → {mb:8d} MB")

    # Time parsing
    print("\nTime Parsing:")
    for spec in ['30m', '1h', '2:30:00', '1-06:00:00']:
        seconds = parser.parse_time(spec)
        formatted = parser.format_time_for_slurm(seconds)
        print(f"  {spec:12s} → {seconds:7d} sec → {formatted}")

    # GPU parsing
    print("\nGPU Parsing:")
    for spec in [2, '2xV100', 'A100:4']:
        result = parser.parse_gpus(spec)
        print(f"  {str(spec):10s} → count={result['count']}, type={result.get('type')}")

    # Resource validation
    print("\nResource Validation:")
    resources = {
        'cpus': 8,
        'memory': '16GB',
        'time_limit': '2h',
        'gpus': 2
    }
    is_valid, errors = parser.validate_resources(resources)
    print(f"  Valid: {is_valid}")
    if errors:
        for error in errors:
            print(f"    - {error}")


def demo_script_generation():
    """Demonstrate SLURM script generation."""
    print_section("2. Script Generation")

    # Create job with resources
    job = SlurmJob(
        job_name='docking_task_001',
        resources={
            'cpus': 8,
            'memory': '16GB',
            'time_limit': '2h',
            'partition': 'compute',
            'gpus': 0
        }
    )

    job.stdout_path = Path('/tmp/docking_001.out')
    job.stderr_path = Path('/tmp/docking_001.err')
    job.work_dir = Path('/project/screening/run_001')

    # Generate script
    generator = ScriptGenerator()
    script = generator.generate_script(
        job,
        command='vina --config config.txt --out results.pdbqt',
        modules=['anaconda3', 'vina/1.2.0'],
        conda_env='screening',
        env_vars={'OMP_NUM_THREADS': '8'},
        pre_commands=['mkdir -p results', 'cp input/*.pdbqt .'],
        post_commands=['tar -czf results.tar.gz results/']
    )

    print("Generated Script (excerpt):")
    lines = script.split('\n')
    for i, line in enumerate(lines[:25]):
        print(f"  {i+1:3d} | {line}")
    print("  ...")

    # Show key directives
    print("\nKey SLURM Directives:")
    for line in lines:
        if line.startswith('#SBATCH'):
            print(f"  {line}")


def demo_array_job():
    """Demonstrate array job script generation."""
    print_section("3. Array Job Generation")

    job = SlurmJob(
        job_name='parallel_docking',
        resources={
            'cpus': 4,
            'memory': '8GB',
            'time_limit': '1h',
            'partition': 'compute'
        }
    )

    job.stdout_path = Path('/tmp/docking_%a.out')
    job.stderr_path = Path('/tmp/docking_%a.err')

    generator = ScriptGenerator()
    script = generator.generate_array_job_script(
        job,
        command_template='python dock.py --input ligand_${SLURM_ARRAY_TASK_ID}.sdf',
        array_size=100,
        work_dir=Path('/project/screening')
    )

    print("Array Job Script (key sections):")
    for line in script.split('\n')[:20]:
        if line.startswith('#SBATCH') or 'ARRAY' in line:
            print(f"  {line}")

    print(f"\n  This will submit 100 parallel jobs (task IDs 1-100)")


def demo_dependencies():
    """Demonstrate dependency building."""
    print_section("4. Dependency Management")

    # Create upstream jobs
    prep_job = SlurmJob(job_name='preparation', job_id='10001')
    filter_job = SlurmJob(job_name='filtering', job_id='10002')
    dock_a = SlurmJob(job_name='docking_a', job_id='10003')
    dock_b = SlurmJob(job_name='docking_b', job_id='10004')

    builder = DependencyBuilder()

    # Simple dependency
    dep1 = builder.build_dependency_string([prep_job], 'afterok')
    print(f"Simple dependency:")
    print(f"  {dep1}")

    # Multiple dependencies
    dep2 = builder.build_dependency_string([dock_a, dock_b], 'afterok')
    print(f"\nMultiple dependencies (both must succeed):")
    print(f"  {dep2}")

    # Any complete
    dep3 = builder.build_dependency_string([dock_a, dock_b], 'afterany')
    print(f"\nAny complete (run regardless of success/failure):")
    print(f"  {dep3}")

    # Complex dependency
    dep4 = builder.build_complex_dependency(
        afterok=[prep_job, filter_job],
        afterany=[dock_a]
    )
    print(f"\nComplex dependency:")
    print(f"  {dep4}")
    print(f"  (prep AND filter must succeed, dock_a must complete)")


def demo_job_submission():
    """Demonstrate job submission (dry-run mode)."""
    print_section("5. Job Submission")

    # Create client in dry-run mode
    client = SlurmClient(dry_run=True)

    # Create a test script
    with tempfile.NamedTemporaryFile(mode='w', suffix='.sh', delete=False) as f:
        f.write('#!/bin/bash\n')
        f.write('#SBATCH --job-name=test\n')
        f.write('#SBATCH --time=00:10:00\n')
        f.write('echo "Running test job"\n')
        script_path = Path(f.name)

    print("Submitting job (dry-run mode)...")

    # Submit first job
    job_id_1 = client.submit_job(script_path)
    print(f"  Job 1 submitted: ID = {job_id_1}")

    # Submit second job with dependency
    job_id_2 = client.submit_job(
        script_path,
        dependencies=[job_id_1],
        dependency_type='afterok'
    )
    print(f"  Job 2 submitted: ID = {job_id_2} (depends on {job_id_1})")

    # Query status
    status = client.get_job_status(job_id_1)
    print(f"\n  Job {job_id_1} status: {status}")

    # Get detailed info
    info = client.get_job_info(job_id_1)
    print(f"  Job {job_id_1} info: {info}")

    # Clean up
    script_path.unlink()


def demo_monitoring():
    """Demonstrate job monitoring."""
    print_section("6. Job Monitoring")

    # Create monitor with dry-run client
    client = SlurmClient(dry_run=True)
    monitor = JobMonitor(client=client, poll_interval=1)

    # Track events
    events = []

    def on_start(job):
        events.append(f"Started: {job.job_name}")

    def on_complete(job):
        events.append(f"Completed: {job.job_name}")

    def on_fail(job):
        events.append(f"Failed: {job.job_name}")

    monitor.on_start(on_start)
    monitor.on_complete(on_complete)
    monitor.on_fail(on_fail)

    # Add jobs to monitor
    job1 = SlurmJob(job_name='task_a', job_id='20001')
    job2 = SlurmJob(job_name='task_b', job_id='20002')
    job3 = SlurmJob(job_name='task_c', job_id='20003')

    monitor.add_job(job1)
    monitor.add_job(job2)
    monitor.add_job(job3)

    print("Monitoring 3 jobs:")
    snapshot = monitor.get_queue_snapshot()
    for job_id, status in snapshot.items():
        print(f"  {status['job_name']:10s} - {status['status']}")

    print(f"\nJob counts:")
    print(f"  Pending: {monitor.get_pending_count()}")
    print(f"  Running: {monitor.get_running_count()}")
    print(f"  Finished: {monitor.get_finished_count()}")


def demo_slurm_config():
    """Demonstrate SLURM configuration."""
    print_section("7. SLURM Configuration")

    config = SlurmConfig()

    # Check availability
    is_available = config.is_slurm_available()
    print(f"SLURM available: {is_available}")

    if is_available:
        print("\nReading cluster configuration...")
        success = config.read_cluster_info()

        if success:
            partitions = config.get_partitions()
            print(f"Available partitions: {partitions}")

            default = config.get_default_partition()
            if default:
                print(f"Default partition: {default}")

                limits = config.get_partition_limits(default)
                if limits:
                    print(f"Partition limits: {limits}")
        else:
            print("Could not read cluster configuration")
    else:
        print("\nSLURM not available on this system")
        print("(This is expected in development environments)")


def demo_integration():
    """Demonstrate complete integration workflow."""
    print_section("8. Complete Integration Workflow")

    print("Workflow: Generate script → Submit job → Monitor → Retrieve results\n")

    # 1. Create job with resources
    print("Step 1: Create job with resource specifications")
    job = SlurmJob(
        job_name='screening_pipeline',
        resources={
            'cpus': 16,
            'memory': '32GB',
            'time_limit': '4h',
            'partition': 'compute',
            'gpus': 1
        }
    )
    job.stdout_path = Path('/tmp/screening.out')
    job.stderr_path = Path('/tmp/screening.err')
    print(f"  ✓ Job created: {job.job_name}")

    # 2. Generate script
    print("\nStep 2: Generate SLURM batch script")
    generator = ScriptGenerator()

    with tempfile.NamedTemporaryFile(mode='w', suffix='.sh', delete=False) as f:
        script_path = Path(f.name)

    script = generator.generate_script(
        job,
        command='python screen.py --config pipeline.yaml',
        modules=['anaconda3', 'cuda/11.8'],
        conda_env='screening'
    )

    with open(script_path, 'w') as f:
        f.write(script)
    script_path.chmod(0o755)

    job.script_path = script_path
    print(f"  ✓ Script generated: {script_path}")

    # 3. Submit job
    print("\nStep 3: Submit job to SLURM")
    client = SlurmClient(dry_run=True)
    job_id = client.submit_job(script_path)
    job.job_id = job_id
    print(f"  ✓ Job submitted: ID = {job_id}")

    # 4. Monitor job
    print("\nStep 4: Monitor job status")
    monitor = JobMonitor(client=client)
    monitor.add_job(job)

    snapshot = monitor.get_queue_snapshot()
    status_info = snapshot[job_id]
    print(f"  ✓ Job status: {status_info['status']}")

    # 5. Retrieve results (simulated)
    print("\nStep 5: Retrieve results")
    print(f"  ✓ Stdout: {job.stdout_path}")
    print(f"  ✓ Stderr: {job.stderr_path}")

    # Clean up
    script_path.unlink()

    print("\n✓ Complete workflow demonstrated!")


def main():
    """Run all demonstrations."""
    print(f"\n{'*' * 70}")
    print(f"  Stage 3: SLURM Integration Layer Demo")
    print(f"{'*' * 70}")

    try:
        demo_resource_parsing()
        demo_script_generation()
        demo_array_job()
        demo_dependencies()
        demo_job_submission()
        demo_monitoring()
        demo_slurm_config()
        demo_integration()

        print(f"\n{'*' * 70}")
        print(f"  All demonstrations completed successfully!")
        print(f"{'*' * 70}\n")

        return 0

    except Exception as e:
        print(f"\n✗ Error: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
