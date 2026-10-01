#!/usr/bin/env python3
"""
Visual demonstration of the CLI monitor showing a live pipeline execution.
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from rapier.logging import CLIMonitor, initialize_logging
from rapier.logging.alerts import ExecutionState
from rapier.core import Pipeline, Task
from rapier.core.enums import TaskStatus


def demo_cli_monitor():
    """Demonstrate CLI monitor with simulated pipeline progress."""

    # Initialize
    initialize_logging(log_directory='/tmp/rapier_demo', config={'level': 'WARNING'})

    # Create pipeline
    pipeline = Pipeline(name='virtual_screening_demo')

    tasks = [
        Task(task_id='prep_receptor', method=None, inputs={}),
        Task(task_id='dock_batch_1', method=None, inputs={}),
        Task(task_id='dock_batch_2', method=None, inputs={}),
        Task(task_id='dock_batch_3', method=None, inputs={}),
        Task(task_id='analyze_results', method=None, inputs={}),
    ]

    for task in tasks:
        task.status = TaskStatus.PENDING
        pipeline.add_task(task)

    # Create monitor
    monitor = CLIMonitor(use_colors=True, use_rich=True)
    state = ExecutionState(pipeline_id='virtual_screening_demo')

    print("\n" + "="*60)
    print("RAPIER PIPELINE EXECUTION - LIVE DEMO")
    print("="*60)

    # Initial state
    monitor.display_progress(pipeline, state)
    time.sleep(1.5)

    # Task 1: Prep receptor
    monitor.print_status_line("Starting receptor preparation...", "info")
    tasks[0].status = TaskStatus.RUNNING
    state.tasks_running = 1
    monitor.display_progress(pipeline, state)
    time.sleep(2)

    monitor.print_status_line("Receptor preparation completed", "success")
    tasks[0].status = TaskStatus.COMPLETED
    state.tasks_completed = 1
    state.tasks_running = 0
    monitor.display_progress(pipeline, state)
    time.sleep(1)

    # Tasks 2-4: Parallel docking
    monitor.print_status_line("Starting parallel docking batches...", "info")
    for i in range(1, 4):
        tasks[i].status = TaskStatus.RUNNING
        state.tasks_running = i
        monitor.display_progress(pipeline, state)
        time.sleep(0.5)

    time.sleep(2)

    # Complete docking tasks
    for i in range(1, 4):
        monitor.print_status_line(f"Docking batch {i} completed", "success")
        tasks[i].status = TaskStatus.COMPLETED
        state.tasks_completed += 1
        state.tasks_running -= 1
        monitor.display_progress(pipeline, state)
        time.sleep(1)

    # Task 5: Analysis
    monitor.print_status_line("Starting results analysis...", "info")
    tasks[4].status = TaskStatus.RUNNING
    state.tasks_running = 1
    monitor.display_progress(pipeline, state)
    time.sleep(2)

    monitor.print_status_line("Analysis completed", "success")
    tasks[4].status = TaskStatus.COMPLETED
    state.tasks_completed += 1
    state.tasks_running = 0
    monitor.display_progress(pipeline, state)

    # Show task list
    print("\n")
    monitor.display_task_list(tasks)

    print("\n" + "="*60)
    monitor.print_status_line("Pipeline completed successfully!", "success")
    print("="*60 + "\n")


if __name__ == '__main__':
    demo_cli_monitor()
