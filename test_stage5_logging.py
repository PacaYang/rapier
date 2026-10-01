#!/usr/bin/env python3
"""
Comprehensive test of the Stage 5 logging system.
Demonstrates all logging components working together.
"""

import os
import sys
import time
from datetime import datetime
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent))

from rapier.logging import (
    initialize_logging,
    PipelineLogger,
    TaskLogger,
    SlurmLogger,
    FileMonitor,
    LogAggregator,
    AlertManager,
    CLIMonitor,
    Dashboard,
    ExecutionState,
    SlurmJob
)
from rapier.core import Pipeline, Task
from rapier.core.enums import TaskStatus


def test_complete_logging_flow():
    """Test complete logging flow from pipeline start to finish."""

    print("\n" + "="*60)
    print("STAGE 5 LOGGING SYSTEM - COMPREHENSIVE TEST")
    print("="*60 + "\n")

    # Initialize logging
    log_dir = '/tmp/rapier_stage5_test'
    log_manager = initialize_logging(
        log_directory=log_dir,
        config={
            'level': 'INFO',
            'console': True,
            'file': True,
            'structured': True,
            'colored': True
        }
    )

    print(f"✓ Initialized logging system (log_dir: {log_dir})\n")

    # Create a test pipeline
    pipeline = Pipeline(name='test_screening_pipeline')

    # Add some test tasks
    task1 = Task(task_id='prep_001', method=None, inputs={'pdb': 'protein.pdb'})
    task1.status = TaskStatus.PENDING

    task2 = Task(task_id='dock_001', method=None, inputs={'receptor': 'prepared.pdb'})
    task2.status = TaskStatus.PENDING

    task3 = Task(task_id='dock_002', method=None, inputs={'receptor': 'prepared.pdb'})
    task3.status = TaskStatus.PENDING

    pipeline.add_task(task1)
    pipeline.add_task(task2)
    pipeline.add_task(task3)

    print(f"✓ Created test pipeline with {len(pipeline.tasks)} tasks\n")

    # Initialize loggers
    pipeline_logger = PipelineLogger('test_screening_pipeline', log_dir=log_dir)
    slurm_logger = SlurmLogger(log_dir=log_dir)
    file_monitor = FileMonitor(log_dir=log_dir)
    alert_manager = AlertManager(log_dir=log_dir)
    cli_monitor = CLIMonitor(use_colors=True, use_rich=True)

    print("✓ Initialized all logger components\n")

    # Create execution state
    state = ExecutionState(pipeline_id='test_screening_pipeline')

    # Start pipeline
    pipeline_logger.log_start(pipeline)
    print("\n--- PIPELINE STARTED ---\n")

    # Simulate task execution
    print("--- Simulating Task Execution ---\n")

    # Task 1: Preparation
    task1.status = TaskStatus.RUNNING
    state.tasks_running = 1
    pipeline_logger.log_task_submitted(task1)

    task1_logger = TaskLogger(task1, log_dir=log_dir)
    task1_logger.log_validation('inputs', True, ['PDB file exists', 'Format valid'])

    job1 = SlurmJob(
        job_id='12345',
        task_id='prep_001',
        script_path='/tmp/prep.sh',
        status='RUNNING',
        cpus=4,
        memory='8GB'
    )
    slurm_logger.log_job_submission(job1)

    task1_logger.log_execution_start('pdb_prep.py protein.pdb', '12345')
    time.sleep(0.5)

    task1_logger.log_progress('Cleaning structure', 33.0)
    time.sleep(0.5)
    task1_logger.log_progress('Adding hydrogens', 66.0)
    time.sleep(0.5)
    task1_logger.log_progress('Generating coordinates', 100.0)

    # Monitor output file
    test_output = '/tmp/prepared.pdb'
    with open(test_output, 'w') as f:
        f.write('ATOM  1  N   ALA A   1\n')

    file_monitor.watch_file(test_output, expected_size=10)

    task1.status = TaskStatus.COMPLETED
    state.tasks_running = 0
    state.tasks_completed = 1
    task1_logger.log_execution_complete(
        TaskStatus.COMPLETED,
        {'output': test_output},
        None
    )

    from rapier.logging.pipeline_logger import TaskResult
    pipeline_logger.log_task_completed(task1, TaskResult(
        task_id='prep_001',
        status=TaskStatus.COMPLETED,
        outputs={'output': test_output},
        duration=1.5
    ))

    slurm_logger.log_job_status_change('12345', 'RUNNING', 'COMPLETED', 'prep_001')
    slurm_logger.log_resource_usage(
        '12345',
        {'elapsed': '00:01:30', 'avg_cpu': '95%', 'max_rss': '2.1GB'},
        'prep_001'
    )

    print("\n✓ Task prep_001 completed\n")

    # Display progress
    cli_monitor.display_progress(pipeline, state)

    # Task 2: Docking (success)
    task2.status = TaskStatus.RUNNING
    state.tasks_running = 1
    pipeline_logger.log_task_submitted(task2)

    task2_logger = TaskLogger(task2, log_dir=log_dir)
    job2 = SlurmJob(job_id='12346', task_id='dock_001', script_path='/tmp/dock.sh', cpus=8, memory='16GB')
    slurm_logger.log_job_submission(job2)
    task2_logger.log_execution_start('vina --receptor prepared.pdb', '12346')

    time.sleep(0.5)
    task2_logger.log_progress('Docking molecules', 50.0)

    task2.status = TaskStatus.COMPLETED
    state.tasks_running = 0
    state.tasks_completed = 2
    task2_logger.log_execution_complete(TaskStatus.COMPLETED, {'output': 'results.sdf'})
    pipeline_logger.log_task_completed(task2, TaskResult(
        task_id='dock_001',
        status=TaskStatus.COMPLETED,
        outputs={'output': 'results.sdf'},
        duration=0.5
    ))

    print("✓ Task dock_001 completed\n")

    # Task 3: Docking (failed)
    task3.status = TaskStatus.RUNNING
    state.tasks_running = 1
    pipeline_logger.log_task_submitted(task3)

    task3_logger = TaskLogger(task3, log_dir=log_dir)
    job3 = SlurmJob(job_id='12347', task_id='dock_002', script_path='/tmp/dock2.sh', cpus=8, memory='16GB')
    slurm_logger.log_job_submission(job3)
    task3_logger.log_execution_start('vina --receptor prepared.pdb', '12347')

    time.sleep(0.5)

    # Simulate failure
    error = Exception("SLURM job failed: OUT_OF_MEMORY")
    task3.status = TaskStatus.FAILED
    state.tasks_running = 0
    state.tasks_failed = 1

    task3_logger.log_execution_complete(TaskStatus.FAILED, error=str(error))
    pipeline_logger.log_task_failed(task3, error)
    slurm_logger.log_job_error('12347', str(error), 'dock_002')

    print("✗ Task dock_002 failed\n")

    # Check alerts
    alerts = alert_manager.check_alerts(state)
    if alerts:
        print(f"\n⚠️  Alerts triggered: {len(alerts)}")
        for alert in alerts:
            print(f"   - {alert}")

    # Display final progress
    cli_monitor.display_progress(pipeline, state)

    # Complete pipeline
    from rapier.logging.pipeline_logger import ExecutionResult
    result = ExecutionResult(
        pipeline_id='test_screening_pipeline',
        status='COMPLETED_WITH_FAILURES',
        tasks_completed=state.tasks_completed,
        tasks_failed=state.tasks_failed,
        duration=(datetime.now() - state.start_time).total_seconds()
    )
    pipeline_logger.log_pipeline_complete(result)

    print("\n--- PIPELINE COMPLETED ---\n")

    # Test log aggregation
    print("--- Testing Log Aggregation ---\n")
    aggregator = LogAggregator(log_dir)

    # Search for errors
    error_entries = aggregator.search_logs('', {'level': 'ERROR'})
    print(f"✓ Found {len(error_entries)} error entries")

    # Get task logs
    task1_logs = aggregator.collect_task_logs('prep_001')
    print(f"✓ Collected {len(task1_logs)} log entries for prep_001")

    # Get timeline
    timeline = aggregator.get_timeline('test_screening_pipeline')
    print(f"✓ Generated timeline with {len(timeline)} events")

    # Get summary
    summary = aggregator.get_summary()
    print(f"\n✓ Log Summary:")
    print(f"   - Total entries: {summary['total_entries']}")
    print(f"   - Log files: {summary['log_files']}")
    print(f"   - By level: {summary['by_level']}")

    # Show log file locations
    print(f"\n--- Log Files Created ---")
    log_files = list(Path(log_dir).glob('*.log'))
    for log_file in sorted(log_files):
        size = log_file.stat().st_size
        print(f"  - {log_file.name} ({size} bytes)")

    # Cleanup test file
    if os.path.exists(test_output):
        os.unlink(test_output)

    print("\n" + "="*60)
    print("✓ ALL STAGE 5 TESTS PASSED")
    print("="*60 + "\n")

    return True


if __name__ == '__main__':
    try:
        success = test_complete_logging_flow()
        sys.exit(0 if success else 1)
    except Exception as e:
        print(f"\n✗ Test failed with error: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(1)
