# Stage 5: Logging & Monitoring System

## Goal
Implement a comprehensive, multi-level logging system that provides visibility into pipeline execution. Top-level dashboard showing overall progress, method-level logs for individual algorithm execution, SLURM job monitoring, and output file validation tracking. Support real-time updates and historical log retrieval.

## Files to Create

### `rapier/logging/__init__.py`
- Logging module initialization
- Export main classes: `LogManager`, `PipelineLogger`, `TaskLogger`, `Dashboard`

### `rapier/logging/log_manager.py`
- `LogManager` class - central logging coordinator
- Methods:
  - `get_logger(name: str, level: str) -> Logger`
  - `configure_logging(config: Dict)`
  - `set_log_directory(path: str)`
  - `get_log_file(component: str) -> str`
- Manages multiple loggers with hierarchical naming
- Configures formatters and handlers

### `rapier/logging/pipeline_logger.py`
- `PipelineLogger` class - top-level pipeline logging
- Methods:
  - `log_start(pipeline: Pipeline)`
  - `log_task_submitted(task: Task)`
  - `log_task_completed(task: Task, result: TaskResult)`
  - `log_task_failed(task: Task, error: Exception)`
  - `log_pipeline_complete(result: ExecutionResult)`
- Writes structured logs (JSON) for parsing
- Human-readable summary logs

### `rapier/logging/task_logger.py`
- `TaskLogger` class - method/task-level logging
- Properties: `task_id`, `log_file`, `error_file`
- Methods:
  - `log_validation(phase: str, success: bool, messages: List[str])`
  - `log_execution_start(command: str, slurm_job_id: str)`
  - `log_progress(message: str, percentage: float)`
  - `log_output_file(path: str, exists: bool, size: int)`
  - `log_execution_complete(status: TaskStatus, outputs: Dict)`
- Separate log file per task
- Captures stdout/stderr from SLURM jobs

### `rapier/logging/slurm_logger.py`
- `SlurmLogger` class - SLURM-specific logging
- Methods:
  - `log_job_submission(job: SlurmJob)`
  - `log_job_status_change(job_id: str, old_status: str, new_status: str)`
  - `log_queue_snapshot(jobs: Dict)`
  - `log_resource_usage(job_id: str, usage: Dict)` - from `sacct`
- Tracks job lifecycle in SLURM queue
- Records resource utilization (CPU, memory, time)

### `rapier/logging/dashboard.py`
- `Dashboard` class - real-time monitoring interface
- Methods:
  - `start()` - launch dashboard server
  - `stop()` - shutdown server
  - `update_status(pipeline_status: Dict)` - push updates
  - `get_snapshot() -> Dict` - current state
- Web-based dashboard (Flask or FastAPI)
- Real-time updates via WebSocket or SSE
- Shows task graph with color-coded status

### `rapier/logging/formatters.py`
- Custom log formatters
- `StructuredFormatter` - JSON format for machine parsing
- `HumanFormatter` - colored, readable format for console
- `TimestampFormatter` - adds high-precision timestamps
- Support for contextual information (task_id, pipeline_id)

### `rapier/logging/aggregator.py`
- `LogAggregator` class - collects logs from distributed tasks
- Methods:
  - `collect_task_logs(task_id: str) -> List[LogEntry]`
  - `search_logs(query: str, filters: Dict) -> List[LogEntry]`
  - `get_timeline(pipeline_id: str) -> List[Event]`
- Aggregates logs from multiple sources (pipeline, tasks, SLURM)
- Supports filtering and searching

### `rapier/logging/monitor.py`
- `FileMonitor` class - validates output files during execution
- Methods:
  - `watch_file(path: str, expected_size: int, callback: Callable)`
  - `check_file_growth(path: str) -> bool` - detect stalled output
  - `validate_file_format(path: str, format: str) -> bool`
- Detects stalled jobs (output file not growing)
- Validates file format (e.g., valid SDF, PDB)

### `rapier/logging/alerts.py`
- `AlertManager` class - notification system
- Methods:
  - `register_alert(condition: str, action: Callable)`
  - `check_alerts(state: ExecutionState)`
  - `send_notification(level: str, message: str)`
- Alert on failures, long-running tasks, resource issues
- Configurable notification methods (email, webhook, file)

### `templates/dashboard.html`
- HTML template for web dashboard
- Real-time task graph visualization
- Progress bars for each task
- Log viewer pane
- Resource utilization charts

### `rapier/logging/cli_monitor.py`
- `CLIMonitor` class - terminal-based monitoring
- Methods:
  - `display_progress(pipeline: Pipeline, state: ExecutionState)`
  - `display_task_list(tasks: List[Task])`
  - `display_logs(task_id: str, follow: bool)`
- Uses rich/tqdm for colored output and progress bars
- Alternative to web dashboard for headless environments

## Files to Modify

### `rapier/engine/executor.py`
- Add logging calls at key execution points
- Integrate with PipelineLogger and TaskLogger

### `rapier/engine/task_runner.py`
- Add logging calls during task execution
- Integrate with FileMonitor

### `rapier/slurm/monitor.py`
- Add logging calls during job monitoring
- Integrate with SlurmLogger

## Implementation Order

1. Create formatters (`formatters.py`) - used by all loggers
2. Implement log manager (`log_manager.py`) - central coordinator
3. Implement pipeline logger (`pipeline_logger.py`)
4. Implement task logger (`task_logger.py`)
5. Implement SLURM logger (`slurm_logger.py`)
6. Implement file monitor (`monitor.py`)
7. Implement log aggregator (`aggregator.py`)
8. Implement alert manager (`alerts.py`)
9. Implement CLI monitor (`cli_monitor.py`)
10. Implement web dashboard (`dashboard.py`, `dashboard.html`)
11. Integrate logging into engine and SLURM components

## Verification Steps

```bash
# Test log manager initialization
python3 -c "
from rapier.logging import LogManager

manager = LogManager()
manager.set_log_directory('/tmp/rapier_test_logs')
logger = manager.get_logger('test.component', 'INFO')
logger.info('Test log message')
print('✓ Log manager works')
"

# Test structured logging
python3 -c "
from rapier.logging import PipelineLogger
from rapier.core import Pipeline
import json

pipeline = Pipeline(name='test_pipeline')
logger = PipelineLogger('test_pipeline', log_dir='/tmp/rapier_test_logs')
logger.log_start(pipeline)

# Check that log file was created and contains JSON
import os
log_file = os.path.join('/tmp/rapier_test_logs', 'pipeline_test_pipeline.log')
assert os.path.exists(log_file), f'Log file not found: {log_file}'

with open(log_file) as f:
    first_line = f.readline()
    log_entry = json.loads(first_line)
    assert log_entry['event'] == 'pipeline_start'
    print('✓ Structured logging works')
"

# Test task logging
python3 -c "
from rapier.logging import TaskLogger
from rapier.core import Task
from rapier.core.enums import TaskStatus

task = Task(task_id='test_task_001', method=None, inputs={})
logger = TaskLogger(task, log_dir='/tmp/rapier_test_logs')

logger.log_validation('inputs', True, ['Input file exists', 'Format valid'])
logger.log_execution_start('python test.py', '12345')
logger.log_progress('Processing molecules', 50.0)
logger.log_execution_complete(TaskStatus.COMPLETED, {'output': 'results.sdf'})

print('✓ Task logging works')
"

# Test file monitoring
python3 -c "
from rapier.logging import FileMonitor
import tempfile
import os

# Create test file
with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.txt') as f:
    test_file = f.name
    f.write('test content\n')

monitor = FileMonitor()
is_growing = monitor.check_file_growth(test_file)
print(f'File growth check: {is_growing}')

os.unlink(test_file)
print('✓ File monitoring works')
"

# Test log aggregation
python3 -c "
from rapier.logging import LogAggregator
import json
import os

log_dir = '/tmp/rapier_test_logs'
aggregator = LogAggregator(log_dir)

# Search for specific events
entries = aggregator.search_logs('pipeline_start', {})
print(f'Found {len(entries)} pipeline_start events')
print('✓ Log aggregation works')
"

# Test CLI monitor display
python3 -c "
from rapier.logging import CLIMonitor
from rapier.core import Pipeline, Task
from rapier.engine.state import ExecutionState
from rapier.core.enums import TaskStatus

pipeline = Pipeline(name='test')
task1 = Task(task_id='A', method=None, inputs=)
task1.status = TaskStatus.COMPLETED
task2 = Task(task_id='B', method=None, inputs={})
task2.status = TaskStatus.RUNNING
pipeline.add_task(task1)
pipeline.add_task(task2)

state = ExecutionState(pipeline_id='test', start_time=None)
state.tasks_completed = 1
state.tasks_failed = 0

monitor = CLIMonitor()
monitor.display_progress(pipeline, state)
print('✓ CLI monitor works')
"

# Test dashboard (without starting server)
python3 -c "
from rapier.logging import Dashboard

dashboard = Dashboard(host='localhost', port=8080)
snapshot = dashboard.get_snapshot()
print(f'Dashboard snapshot: {snapshot}')
print('✓ Dashboard initialization works')
# Note: Full dashboard test requires starting server and making HTTP request
"
```

## Key Decisions

**1. Multi-Level Logging Hierarchy**
- Platform level: system-wide events
- Pipeline level: workflow execution events
- Task level: individual method execution
- SLURM level: job queue and resource usage
- Each level has its own log file and logger instance

**2. Structured Logging (JSON)**
- All logs written as JSON lines for easy parsing
- Standard fields: `timestamp`, `level`, `component`, `event`, `data`
- Enables log analysis with tools like `jq`, ELK stack, Splunk
- Human-readable version available for console output

**3. Real-Time Dashboard**
- Web-based UI for monitoring (accessible remotely)
- WebSocket or Server-Sent Events for live updates
- Task graph visualization with D3.js or similar
- Alternative CLI monitor for headless systems

**4. File Monitoring for Early Failure Detection**
- Check output files periodically during execution
- Detect stalled jobs (file not growing for N minutes)
- Validate file formats as they're written
- Alert on unexpected file sizes or missing outputs

**5. Log Retention and Cleanup**
- Keep detailed logs for recent executions (default: 30 days)
- Compress old logs (gzip)
- Summary logs retained longer than detailed logs
- Configurable retention policies per log level

**6. Alert System**
- Condition-based alerts: `if task_duration > 2 * expected: alert`
- Severity levels: INFO, WARNING, ERROR, CRITICAL
- Multiple notification channels: log file, email, webhook, Slack
- Rate limiting to avoid alert spam
