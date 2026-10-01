# Stage 4: Dependency & Execution Engine

## Goal
Build the core execution engine that manages the pipeline DAG, handles task dependencies, schedules tasks based on completion status, and implements failure handling policies. Support optional continuation when tasks fail, parallel execution of independent branches, and result pooling from multiple upstream tasks.

## Files to Create

### `rapier/engine/__init__.py`
- Engine module initialization
- Export main classes: `ExecutionEngine`, `Scheduler`, `TaskRunner`, `ResultPooler`

### `rapier/engine/scheduler.py`
- `Scheduler` class - manages task execution order
- Methods:
  - `get_ready_tasks() -> List[Task]` - tasks with satisfied dependencies
  - `mark_completed(task: Task, status: TaskStatus)`
  - `update_graph(task: Task, status: TaskStatus)` - update dependencies
  - `can_continue(task: Task) -> bool` - check failure policies
- Uses ExecutionGraph for dependency tracking
- Implements topological ordering

### `rapier/engine/task_runner.py`
- `TaskRunner` class - executes individual tasks
- Methods:
  - `run_task(task: Task) -> TaskStatus`
  - `prepare_task(task: Task)` - validate inputs/environment
  - `submit_task(task: Task) -> str` - submit to SLURM
  - `monitor_task(task: Task)` - track execution
  - `collect_task_results(task: Task)` - gather outputs
- Integrates with SLURM client
- Implements pre-run validation and post-run collection

### `rapier/engine/pooler.py`
- `ResultPooler` class - combines results from multiple upstream tasks
- Methods:
  - `pool_results(tasks: List[Task], rule: str) -> Any`
  - `apply_rule_any(results: List) -> List` - any succeeded
  - `apply_rule_all(results: List) -> List` - all succeeded
  - `apply_rule_majority(results: List) -> List` - majority vote
  - `apply_rule_best(results: List, metric: str) -> Any` - best by metric
- Support for custom pooling functions
- Handles different result types (files, scores, molecule lists)

### `rapier/engine/executor.py`
- `ExecutionEngine` class - orchestrates entire pipeline execution
- Properties: `pipeline`, `scheduler`, `runner`, `pooler`, `status`
- Methods:
  - `execute(pipeline: Pipeline) -> ExecutionResult`
  - `start()` - initialize execution
  - `step()` - execute one round of ready tasks
  - `wait_for_completion()` - block until pipeline finishes
  - `pause()`, `resume()`, `cancel()` - execution control
- Main event loop coordinating all components

### `rapier/engine/failure_handler.py`
- `FailureHandler` class - implements failure policies
- Methods:
  - `handle_failure(task: Task, error: Exception) -> FailureAction`
  - `should_retry(task: Task) -> bool`
  - `should_continue(task: Task) -> bool`
  - `should_stop_pipeline(task: Task) -> bool`
  - `mark_downstream_tasks(task: Task, action: FailureAction)`
- Policies: STOP, CONTINUE, RETRY, SKIP_DOWNSTREAM
- Tracks retry counts and failure reasons

### `rapier/engine/state.py`
- `ExecutionState` class - tracks pipeline execution state
- Properties: `pipeline_id`, `start_time`, `tasks_completed`, `tasks_failed`, `current_phase`
- Methods:
  - `save()` - persist state to disk
  - `load(state_file: str) -> ExecutionState`
  - `to_dict()`, `from_dict()`
- Enables pipeline resumption after interruption
- Stores task history and status

### `rapier/engine/parallel_executor.py`
- `ParallelExecutor` class - manages concurrent task execution
- Uses ThreadPoolExecutor or ProcessPoolExecutor
- Methods:
  - `submit_parallel(tasks: List[Task]) -> List[Future]`
  - `wait_any(futures: List[Future]) -> Future`
  - `wait_all(futures: List[Future]) -> List[Result]`
- Respects concurrency limits from configuration
- Handles task isolation (separate working directories)

### `rapier/engine/result.py`
- `ExecutionResult` class - pipeline execution outcome
- Properties: `success`, `completed_tasks`, `failed_tasks`, `skipped_tasks`, `duration`, `outputs`
- Methods:
  - `get_task_result(task_id: str) -> TaskResult`
  - `get_final_outputs() -> Dict` - outputs from terminal tasks
  - `generate_report() -> str` - human-readable summary
- Aggregates results from all tasks

## Files to Modify

### `rapier/core/execution_graph.py`
- Add method: `get_upstream_tasks(task: Task) -> List[Task]`
- Add method: `get_downstream_tasks(task: Task) -> List[Task]`
- Add method: `mark_task_status(task: Task, status: TaskStatus)`

### `rapier/core/pipeline.py`
- Add method: `to_execution_graph() -> ExecutionGraph`
- Add property: `pooling_nodes` - tasks that pool results

## Implementation Order

1. Implement execution state (`state.py`) - used for persistence
2. Implement execution result (`result.py`) - outcome representation
3. Implement failure handler (`failure_handler.py`) - policy enforcement
4. Implement result pooler (`pooler.py`) - combining results
5. Implement task runner (`task_runner.py`) - individual task execution
6. Implement parallel executor (`parallel_executor.py`) - concurrent execution
7. Implement scheduler (`scheduler.py`) - dependency resolution
8. Implement execution engine (`executor.py`) - main orchestrator
9. Update ExecutionGraph with new methods

## Verification Steps

```bash
# Test scheduler dependency resolution
python3 -c "
from rapier.engine import Scheduler
from rapier.core import Pipeline, Task, ExecutionGraph

# Create simple pipeline: A -> B -> C
pipeline = Pipeline(name='test')
task_a = Task(task_id='A', method=None, inputs={})
task_b = Task(task_id='B', method=None, inputs={})
task_c = Task(task_id='C', method=None, inputs={})
pipeline.add_task(task_a)
pipeline.add_task(task_b)
pipeline.add_task(task_c)
pipeline.add_connection('A', 'B')
pipeline.add_connection('B', 'C')

graph = pipeline.to_execution_graph()
scheduler = Scheduler(graph)

# Initially only A should be ready
ready = scheduler.get_ready_tasks()
assert len(ready) == 1 and ready[0].task_id == 'A', f'Expected [A], got {[t.task_id for t in ready]}'
print('✓ Initial ready tasks correct')

# After A completes, B should be ready
from rapier.core.enums import TaskStatus
scheduler.mark_completed(task_a, TaskStatus.COMPLETED)
ready = scheduler.get_ready_tasks()
assert len(ready) == 1 and ready[0].task_id == 'B'
print('✓ Dependency resolution works')
"

# Test result pooling
python3 -c "
from rapier.engine import ResultPooler
from rapier.core import Task

# Create mock tasks with results
task1 = Task(task_id='A', method=None, inputs={})
task1.outputs = {'score': 0.8, 'molecules': ['mol1', 'mol2']}
task2 = Task(task_id='B', method=None, inputs={})
task2.outputs = {'score': 0.6, 'molecules': ['mol3', 'mol4']}

pooler = ResultPooler()

# Test 'all' rule
result = pooler.pool_results([task1, task2], 'all')
print(f'All rule: {len(result)} task results')

# Test 'best' rule
result = pooler.pool_results([task1, task2], 'best[score]')
print(f'Best by score: task {result.task_id} with score {result.outputs[\"score\"]}')
assert result.task_id == 'A', 'Should select task with highest score'
print('✓ Result pooling works')
"

# Test failure handling
python3 -c "
from rapier.engine import FailureHandler
from rapier.core import Task
from rapier.core.enums import FailurePolicy, TaskStatus

task = Task(task_id='A', method=None, inputs={})
task.failure_policy = FailurePolicy.RETRY
task.max_retries = 3
task.retry_count = 0

handler = FailureHandler()

# First failure should retry
action = handler.handle_failure(task, Exception('Test error'))
assert action.should_retry, 'First failure should retry'
print('✓ Retry policy works')

# After max retries, should not retry
task.retry_count = 3
action = handler.handle_failure(task, Exception('Test error'))
assert not action.should_retry, 'Should not retry after max attempts'
print('✓ Max retry limit works')
"

# Test parallel execution (mock, without real SLURM)
python3 -c "
from rapier.engine import ParallelExecutor
from rapier.core import Task
import time

def mock_task_fn(task_id):
    time.sleep(0.1)
    return f'Task {task_id} complete'

executor = ParallelExecutor(max_workers=4)

# Submit 4 tasks in parallel
tasks = [Task(task_id=f'T{i}', method=None, inputs={}) for i in range(4)]
futures = executor.submit_parallel(tasks, mock_task_fn)

# Wait for all to complete
results = executor.wait_all(futures)
assert len(results) == 4, f'Expected 4 results, got {len(results)}'
print('✓ Parallel execution works')
"

# Test full pipeline execution (integration test with mock methods)
python3 -c "
from rapier.engine import ExecutionEngine
from rapier.core import Pipeline, Task
from rapier.core.base_method import BaseMethod

class MockMethod(BaseMethod):
    def validate_inputs(self, inputs):
        return True, []
    def validate_environment(self):
        return True, []
    def execute(self, task):
        task.outputs = {'result': 'success'}
        return 'completed'

# Create pipeline: A -> B
pipeline = Pipeline(name='integration_test')
method = MockMethod(name='mock', version='1.0')
task_a = Task(task_id='A', method=method, inputs={})
task_b = Task(task_id='B', method=method, inputs={})
pipeline.add_task(task_a)
pipeline.add_task(task_b)
pipeline.add_connection('A', 'B')

# Execute (without actual SLURM submission)
engine = ExecutionEngine()
result = engine.execute(pipeline, dry_run=True)
print(f'Pipeline execution: {result.success}')
print(f'Tasks completed: {len(result.completed_tasks)}')
print('✓ Full pipeline execution works')
"
```

## Key Decisions

**1. Event-Driven Architecture**
- Scheduler reacts to task completion events
- Non-blocking execution loop
- Enables real-time monitoring and control
- Supports pause/resume/cancel operations

**2. Separation of Concerns**
- Scheduler: dependency resolution (what to run)
- TaskRunner: individual task execution (how to run)
- ParallelExecutor: concurrency management (when to run)
- FailureHandler: error policy enforcement (what to do on failure)

**3. State Persistence**
- ExecutionState saved after each task completion
- Enables pipeline resumption after crashes or interruptions
- Stores enough information to reconstruct execution context
- JSON format for easy inspection and debugging

**4. Failure Policy Granularity**
- Global pipeline default
- Per-task override
- Per-task-type (method) default
- Evaluated at failure time, not submission time

**5. Result Pooling Strategies**
- `any`: pass through any successful result (first wins)
- `all`: concatenate all successful results
- `majority`: require >50% success, take union
- `best[metric]`: select result with optimal metric value
- Custom functions: user can register pooling functions

**6. Parallel Execution Model**
- DAG-level parallelism (independent branches run concurrently)
- Task-level parallelism (SLURM array jobs for embarrassingly parallel work)
- Thread-based for I/O-bound coordination
- Process-based for CPU-bound data processing
