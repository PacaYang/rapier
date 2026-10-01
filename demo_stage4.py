#!/usr/bin/env python3
"""
Demonstration of Stage 4: Dependency & Execution Engine

This script demonstrates all major features of the execution engine:
- Task scheduling and dependency resolution
- Parallel execution
- Failure handling with retry/continue policies
- Result pooling
- State persistence
- Complete pipeline execution
"""

import sys
import tempfile
from pathlib import Path
from datetime import datetime

from rapier.core import Pipeline, Task
from rapier.core.base_method import BaseMethod
from rapier.core.enums import TaskStatus, FailurePolicy, MethodType
from rapier.core.specs import InputSpec, OutputSpec, EnvironmentSpec
from rapier.engine import (
    ExecutionEngine,
    Scheduler,
    ResultPooler,
    FailureHandler,
    ExecutionState,
    ParallelExecutor,
    TaskRunner
)


def print_section(title):
    """Print a formatted section header."""
    print(f"\n{'=' * 70}")
    print(f"  {title}")
    print(f"{'=' * 70}\n")


class MockMethod(BaseMethod):
    """Mock method for testing."""

    def __init__(self, name='mock', version='1.0', **kwargs):
        super().__init__(name, version, method_type=MethodType.SINGLE_STEP)
        self.should_fail = kwargs.get('should_fail', False)
        self.delay = kwargs.get('delay', 0)

    def _create_input_spec(self) -> InputSpec:
        return InputSpec()

    def _create_output_spec(self) -> OutputSpec:
        return OutputSpec()

    def _create_env_spec(self) -> EnvironmentSpec:
        return EnvironmentSpec()

    def validate_inputs(self, inputs):
        return True, []

    def validate_environment(self):
        return True, []

    def execute(self, task):
        import time
        if self.delay:
            time.sleep(self.delay)

        if self.should_fail and task.retry_count == 0:
            task.error_message = "Simulated failure"
            return TaskStatus.FAILED

        task.outputs = {'result': f'success_{task.task_id}', 'score': 0.8}
        return TaskStatus.COMPLETED

    def collect_results(self, task):
        return task.outputs


def demo_scheduler():
    """Demonstrate task scheduling and dependency resolution."""
    print_section("1. Task Scheduling & Dependency Resolution")

    # Create pipeline: A -> B -> C, A -> D
    pipeline = Pipeline(name='scheduler_demo')

    task_a = Task('A', MockMethod(), {})
    task_b = Task('B', MockMethod(), {})
    task_c = Task('C', MockMethod(), {})
    task_d = Task('D', MockMethod(), {})

    pipeline.add_task(task_a)
    pipeline.add_task(task_b)
    pipeline.add_task(task_c)
    pipeline.add_task(task_d)

    pipeline.add_connection('A', 'B')
    pipeline.add_connection('B', 'C')
    pipeline.add_connection('A', 'D')

    # Create scheduler
    graph = pipeline.to_execution_graph()
    scheduler = Scheduler(graph)

    print("Pipeline structure:")
    print("  A → B → C")
    print("  A → D")
    print()

    # Get ready tasks
    ready = scheduler.get_ready_tasks()
    print(f"Initially ready: {[t.task_id for t in ready]}")

    # Mark A complete
    scheduler.mark_completed(task_a, TaskStatus.COMPLETED)
    ready = scheduler.get_ready_tasks()
    print(f"After A completes: {[t.task_id for t in ready]}")

    # Mark B complete
    scheduler.mark_completed(task_b, TaskStatus.COMPLETED)
    ready = scheduler.get_ready_tasks()
    print(f"After B completes: {[t.task_id for t in ready]}")

    # Get progress
    progress = scheduler.get_progress()
    print(f"\nProgress: {progress['completed']}/{progress['total']} completed " +
          f"({progress['progress_pct']:.0f}%)")


def demo_failure_handling():
    """Demonstrate failure handling policies."""
    print_section("2. Failure Handling")

    handler = FailureHandler()

    # Test RETRY policy
    print("Testing RETRY policy:")
    task = Task('retry_task', MockMethod(), {})
    task.failure_policy = FailurePolicy.RETRY
    task.max_retries = 3
    task.retry_count = 0

    decision = handler.handle_failure(task, Exception("Test error"))
    print(f"  Attempt 1: should_retry={decision.should_retry}")
    print(f"  Reason: {decision.reason}")

    task.retry_count = 3
    decision = handler.handle_failure(task, Exception("Test error"))
    print(f"  Attempt 4 (max): should_retry={decision.should_retry}")
    print(f"  Reason: {decision.reason}")

    # Test CONTINUE policy
    print("\nTesting CONTINUE policy:")
    task = Task('continue_task', MockMethod(), {})
    task.failure_policy = FailurePolicy.CONTINUE

    decision = handler.handle_failure(task, Exception("Test error"))
    print(f"  mark_downstream_skipped={decision.mark_downstream_skipped}")
    print(f"  Reason: {decision.reason}")

    # Test STOP policy
    print("\nTesting STOP policy:")
    task = Task('stop_task', MockMethod(), {})
    task.failure_policy = FailurePolicy.STOP

    decision = handler.handle_failure(task, Exception("Test error"))
    print(f"  should_stop_pipeline={decision.should_stop_pipeline}")
    print(f"  Reason: {decision.reason}")


def demo_result_pooling():
    """Demonstrate result pooling strategies."""
    print_section("3. Result Pooling")

    # Create tasks with results
    task1 = Task('docking_1', MockMethod(), {})
    task1.status = TaskStatus.COMPLETED
    task1.outputs = {'score': 0.8, 'molecules': ['mol1', 'mol2']}

    task2 = Task('docking_2', MockMethod(), {})
    task2.status = TaskStatus.COMPLETED
    task2.outputs = {'score': 0.6, 'molecules': ['mol3', 'mol4']}

    task3 = Task('docking_3', MockMethod(), {})
    task3.status = TaskStatus.COMPLETED
    task3.outputs = {'score': 0.9, 'molecules': ['mol5']}

    tasks = [task1, task2, task3]
    pooler = ResultPooler()

    # Test 'all' strategy
    print("Strategy: ALL")
    result = pooler.pool_results(tasks, 'all')
    print(f"  Returns: {len(result)} tasks")
    print(f"  Task IDs: {[t.task_id for t in result]}")

    # Test 'any' strategy
    print("\nStrategy: ANY (first successful)")
    result = pooler.pool_results(tasks, 'any')
    print(f"  Returns: {len(result)} task(s)")
    print(f"  Task ID: {result[0].task_id if result else 'none'}")

    # Test 'best' strategy
    print("\nStrategy: BEST by score")
    result = pooler.pool_results(tasks, 'best[score]')
    print(f"  Best task: {result.task_id}")
    print(f"  Score: {result.outputs['score']}")

    # Test 'majority' strategy
    print("\nStrategy: MAJORITY")
    result = pooler.pool_results(tasks, 'majority')
    print(f"  Returns: {len(result)} tasks (>50% succeeded)")


def demo_parallel_execution():
    """Demonstrate parallel task execution."""
    print_section("4. Parallel Execution")

    import time

    def mock_task_fn(task):
        """Mock task function that takes some time."""
        time.sleep(0.1)
        return f"Result: {task.task_id}"

    # Create tasks
    tasks = [Task(f'parallel_{i}', MockMethod(), {}) for i in range(5)]

    print(f"Executing {len(tasks)} tasks in parallel...")

    executor = ParallelExecutor(max_workers=3)
    start = time.time()

    # Submit all tasks
    futures = executor.submit_parallel(tasks, mock_task_fn)

    # Wait for completion
    results = executor.wait_all(futures, timeout=5)

    duration = time.time() - start

    print(f"  Completed in {duration:.2f} seconds")
    print(f"  Results: {len(results)} tasks")
    print(f"  Parallel speedup vs sequential: {len(tasks) * 0.1 / duration:.1f}x")

    executor.shutdown()


def demo_state_persistence():
    """Demonstrate execution state persistence."""
    print_section("5. State Persistence")

    # Create initial state
    state = ExecutionState.create_initial(
        pipeline_id='persist_demo',
        pipeline_name='Persistence Demo',
        task_ids=['A', 'B', 'C', 'D']
    )

    print("Initial state:")
    print(f"  Total tasks: {state.total_tasks}")
    print(f"  Pending: {len(state.tasks_pending)}")

    # Update some tasks
    state.update_task_status('A', 'RUNNING')
    state.update_task_status('B', 'COMPLETED')
    state.update_task_status('C', 'FAILED')

    print("\nAfter updates:")
    print(f"  Running: {len(state.tasks_running)}")
    print(f"  Completed: {len(state.tasks_completed)}")
    print(f"  Failed: {len(state.tasks_failed)}")
    print(f"  Progress: {state.get_progress():.1f}%")

    # Save and load
    with tempfile.TemporaryDirectory() as tmpdir:
        state_file = Path(tmpdir) / 'state.json'
        state.save(state_file)
        print(f"\n✓ State saved to {state_file}")

        loaded_state = ExecutionState.load(state_file)
        print(f"✓ State loaded successfully")
        print(f"  Loaded progress: {loaded_state.get_progress():.1f}%")


def demo_execution_result():
    """Demonstrate execution result reporting."""
    print_section("6. Execution Result")

    from rapier.engine import ExecutionResult, TaskResult

    # Create result
    result = ExecutionResult(
        pipeline_name='Demo Pipeline',
        success=True,
        start_time=datetime.now()
    )

    # Add task results
    result.task_results['prep'] = TaskResult(
        task_id='prep',
        status='COMPLETED',
        outputs={'molecules': 100},
        duration=5.2
    )

    result.task_results['filter'] = TaskResult(
        task_id='filter',
        status='COMPLETED',
        outputs={'molecules': 80},
        duration=3.1
    )

    result.task_results['dock'] = TaskResult(
        task_id='dock',
        status='COMPLETED',
        outputs={'best_score': 0.92},
        duration=120.5,
        retries=1
    )

    result.completed_tasks = ['prep', 'filter', 'dock']
    result.end_time = datetime.now()

    # Generate report
    print(result.generate_report())


def demo_complete_pipeline():
    """Demonstrate complete pipeline execution (mock)."""
    print_section("7. Complete Pipeline Execution (Mock)")

    # Create pipeline with branching
    pipeline = Pipeline(name='complete_demo')

    # Create tasks
    prep = Task('preparation', MockMethod('prep'), {})
    filter1 = Task('filter_lipinski', MockMethod('filter'), {})
    filter2 = Task('filter_pains', MockMethod('filter'), {})
    dock = Task('docking', MockMethod('dock'), {})

    pipeline.add_task(prep)
    pipeline.add_task(filter1)
    pipeline.add_task(filter2)
    pipeline.add_task(dock)

    # Add connections: prep -> filter1 -> dock
    #                  prep -> filter2 -> dock
    pipeline.add_connection('preparation', 'filter_lipinski')
    pipeline.add_connection('preparation', 'filter_pains')
    pipeline.add_connection('filter_lipinski', 'docking')
    pipeline.add_connection('filter_pains', 'docking')

    # Add pooling rule for dock (needs both filters)
    pipeline.add_pooling_rule('docking', 'all')

    print("Pipeline structure:")
    print("  preparation → filter_lipinski → docking")
    print("              → filter_pains    →")
    print()

    # Create execution graph
    graph = pipeline.to_execution_graph()
    scheduler = Scheduler(graph)

    print("Execution simulation:")

    # Round 1: Only prep is ready
    ready = scheduler.get_ready_tasks()
    print(f"  Round 1: {[t.task_id for t in ready]} ready")
    scheduler.mark_completed(prep, TaskStatus.COMPLETED)

    # Round 2: Both filters ready
    ready = scheduler.get_ready_tasks()
    print(f"  Round 2: {[t.task_id for t in ready]} ready (parallel)")
    scheduler.mark_completed(filter1, TaskStatus.COMPLETED)
    scheduler.mark_completed(filter2, TaskStatus.COMPLETED)

    # Round 3: Docking ready
    ready = scheduler.get_ready_tasks()
    print(f"  Round 3: {[t.task_id for t in ready]} ready")
    scheduler.mark_completed(dock, TaskStatus.COMPLETED)

    print(f"\n✓ Pipeline completed: {scheduler.is_finished()}")


def main():
    """Run all demonstrations."""
    print(f"\n{'*' * 70}")
    print(f"  Stage 4: Dependency & Execution Engine Demo")
    print(f"{'*' * 70}")

    try:
        demo_scheduler()
        demo_failure_handling()
        demo_result_pooling()
        demo_parallel_execution()
        demo_state_persistence()
        demo_execution_result()
        demo_complete_pipeline()

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
