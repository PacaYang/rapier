# Rapier Logging System - User Guide

## Overview

The Rapier logging system provides comprehensive visibility into pipeline execution with multi-level logging, real-time monitoring, and alert capabilities.

## Quick Start

### Basic Setup

```python
from rapier.logging import initialize_logging

# Initialize logging for your project
log_manager = initialize_logging(
    log_directory='/path/to/logs',
    config={
        'level': 'INFO',           # Log level: DEBUG, INFO, WARNING, ERROR, CRITICAL
        'console': True,           # Print to console
        'file': True,              # Write to files
        'structured': True,        # Use JSON format for files
        'colored': True            # Use colors in console
    }
)
```

### Pipeline Logging

```python
from rapier.logging import PipelineLogger

# Create pipeline logger
pipeline_logger = PipelineLogger('my_pipeline', log_dir='/path/to/logs')

# Log pipeline start
pipeline_logger.log_start(pipeline)

# Log task events
pipeline_logger.log_task_submitted(task)
pipeline_logger.log_task_completed(task, result)
pipeline_logger.log_task_failed(task, error)

# Log pipeline completion
pipeline_logger.log_pipeline_complete(execution_result)
```

### Task Logging

```python
from rapier.core.enums import TaskStatus
from rapier.logging import TaskLogger

# Create task logger
task_logger = TaskLogger(task, log_dir='/path/to/logs')

# Log validation
task_logger.log_validation('inputs', True, ['All files present'])

# Log execution
task_logger.log_execution_start('vina --receptor ...', slurm_job_id='12345')
task_logger.log_progress('Docking molecules', percentage=50.0)
task_logger.log_output_file('/path/to/output.sdf', exists=True, size=1024000)
task_logger.log_execution_complete(TaskStatus.COMPLETED, outputs={'result': 'output.sdf'})
```

### SLURM Job Logging

```python
from rapier.logging import SlurmLogger, SlurmJob

slurm_logger = SlurmLogger(log_dir='/path/to/logs')

# Log job submission
job = SlurmJob(
    job_id='12345',
    task_id='dock_001',
    script_path='/path/to/script.sh',
    partition='gpu',
    cpus=8,
    memory='16GB'
)
slurm_logger.log_job_submission(job)

# Log status changes
slurm_logger.log_job_status_change('12345', 'PENDING', 'RUNNING', task_id='dock_001')

# Log resource usage
slurm_logger.log_resource_usage(
    '12345',
    {'elapsed': '00:15:30', 'avg_cpu': '95%', 'max_rss': '12GB'},
    task_id='dock_001'
)
```

## Monitoring

### CLI Monitor (Terminal)

```python
from rapier.logging import CLIMonitor, ExecutionState

# Create CLI monitor (falls back to plain text if `rich` isn't installed)
monitor = CLIMonitor(use_colors=True, use_rich=True)

# Display pipeline progress
state = ExecutionState(pipeline_id='my_pipeline')
state.tasks_completed = 5
state.tasks_failed = 1
state.tasks_running = 2

monitor.display_progress(pipeline, state)

# Display task list (pipeline.tasks is a dict, so pass its values)
monitor.display_task_list(list(pipeline.tasks.values()))

# Display logs for a task (reads the directory given to initialize_logging)
monitor.display_logs(task_id='dock_001', follow=False)

# Print status messages
monitor.print_status_line("Job submitted", status='success')
monitor.print_status_line("Warning: High memory usage", status='warning')
monitor.print_status_line("Error: Job failed", status='error')
```

### Web Dashboard

Requires `pip install flask flask-cors`. The page is served from the repo's
`templates/dashboard.html`, so run it from a source checkout. The server has no
authentication; keep the default `host='localhost'` unless you're on a trusted
network.

```python
from rapier.logging import Dashboard

# Create dashboard
dashboard = Dashboard(host='localhost', port=8080, log_dir='/path/to/logs')

# Start dashboard server (runs in background)
dashboard.start()

# Update dashboard with current status (tasks defaults to pipeline.tasks)
pipeline_data = dashboard.format_pipeline_data(pipeline, state)
dashboard.update_status(pipeline_data)

# Get snapshot
snapshot = dashboard.get_snapshot()

# Stop dashboard
dashboard.stop()
```

Access the dashboard in your browser at `http://localhost:8080`. JSON is
available at `/api/status` and `/api/pipeline/<pipeline_id>`.

### WebSocket Dashboard (Real-time Updates)

```python
from rapier.logging import DashboardWithWebSocket

# Requires: pip install flask flask-socketio (falls back to Dashboard without it)
dashboard = DashboardWithWebSocket(host='localhost', port=8080)
dashboard.start()

# Updates are pushed automatically via WebSocket
dashboard.update_status(pipeline_data)
```

## File Monitoring

```python
from rapier.logging import FileMonitor

monitor = FileMonitor()

# Watch for file creation and size
monitor.watch_file('/path/to/output.sdf', expected_size=1000000)

# Check if file is growing (not stalled)
is_growing = monitor.check_file_growth(
    '/path/to/output.sdf',
    min_interval_seconds=60,
    stall_threshold_seconds=300
)

# Validate file format ('sdf', 'pdb', 'csv', 'json', or 'txt')
is_valid = monitor.validate_file_format('/path/to/output.sdf', format='sdf')

# Get file info
info = monitor.get_file_info('/path/to/output.sdf')
# Returns: {'path': '...', 'size': 1024000, 'modified': '...', 'created': '...'}
```

## Log Aggregation & Querying

```python
from rapier.logging import LogAggregator

aggregator = LogAggregator(log_dir='/path/to/logs')

# Collect all logs for a task
task_logs = aggregator.collect_task_logs('dock_001')

# Search logs with text query and filters
error_logs = aggregator.search_logs(
    query='failed',
    filters={
        'level': 'ERROR',
        'task_id': 'dock_001',
        'start_time': '2026-01-01T00:00:00',
        'end_time': '2026-01-02T00:00:00'
    }
)

# Get execution timeline
timeline = aggregator.get_timeline(pipeline_id='my_pipeline')

# Get summary statistics
summary = aggregator.get_summary()
# Returns: {'total_entries': 1234, 'by_level': {...}, 'by_component': {...}, 'log_files': 5}
```

## Alert System

`AlertManager` registers three default conditions: `task_failure`,
`high_failure_rate` (over 50% of finished tasks failed), and `no_progress`
(nothing completed or running after 10 minutes). Message templates can use
`{pipeline_id}`, `{tasks_completed}`, `{tasks_failed}`, and `{failure_rate}`.

```python
from rapier.logging import AlertManager, AlertLevel, ExecutionState

alert_manager = AlertManager()

# Register custom alert condition
alert_manager.register_alert(
    name='long_running_task',
    check=lambda state: any(
        duration > 3600 for duration in state.task_durations.values()
    ),
    level=AlertLevel.WARNING,
    message_template='Task running for over 1 hour',
    cooldown_seconds=600
)

# Add notification handlers (webhook_handler needs `requests`;
# email_handler(smtp_config) sends ERROR/CRITICAL alerts over SMTP)
from rapier.logging.alerts import log_file_handler, webhook_handler

alert_manager.add_notification_method(
    log_file_handler('/path/to/alerts.log')
)

alert_manager.add_notification_method(
    webhook_handler('https://hooks.slack.com/services/YOUR/WEBHOOK/URL')
)

# Check alerts
state = ExecutionState(pipeline_id='my_pipeline')
state.tasks_failed = 3
triggered_alerts = alert_manager.check_alerts(state)

# Manually send notification
alert_manager.send_notification(
    level='WARNING',
    message='High memory usage detected',
    component='monitoring'
)

# Get recent alerts
recent = alert_manager.get_recent_alerts(minutes=60)
```

## Log File Structure

The logging system creates these files:

```
/path/to/logs/
├── rapier.log                           # Main system log (all components; JSON when structured=True)
├── pipeline_<pipeline_id>.log           # Pipeline events (JSON lines: timestamp, pipeline_id, event, data)
├── pipeline_<pipeline_id>_summary.txt   # Pipeline human-readable summary
├── task_<task_id>.log                   # Task execution logs (JSON)
├── task_<task_id>_error.log             # Task error logs (if errors occurred)
└── slurm.log                            # SLURM job tracking (JSON)
```

## Log Entry Format

### Structured (JSON) Format

Entries in `rapier.log` look like this (the per-pipeline, task, and SLURM files
use the same `timestamp`/`event`/`data` keys without `component`/`message`):

```json
{
  "timestamp": "2026-06-24T18:50:00.123456",
  "level": "INFO",
  "component": "rapier.pipeline.my_pipeline",
  "message": "Task completed: dock_001 in 120.5s",
  "event": "task_completed",
  "data": {
    "task_id": "dock_001",
    "duration": 120.5,
    "outputs": {"result": "output.sdf"}
  },
  "pipeline_id": "my_pipeline",
  "task_id": "dock_001"
}
```

### Human-Readable Format

```
2026-06-24 18:50:00 [INFO] rapier.pipeline.my_pipeline: Task completed: dock_001 in 120.5s (pipeline=my_pipeline, task=dock_001)
```

## Querying Logs with jq

Since logs are JSON, you can use `jq` for powerful queries:

```bash
# Get all error entries
cat /path/to/logs/rapier.log | jq 'select(.level == "ERROR")'

# Get task completion times
cat /path/to/logs/pipeline_*.log | jq 'select(.event == "task_completed") | {task: .data.task_id, duration: .data.duration}'

# Get resource usage for all jobs
cat /path/to/logs/slurm.log | jq 'select(.event == "resource_usage") | .data.usage'

# Count events by type
cat /path/to/logs/rapier.log | jq -r '.event' | sort | uniq -c
```

## Best Practices

1. **Initialize Early**: Call `initialize_logging()` at the start of your script
2. **Use Structured Logging**: Enable JSON format for production to enable analysis
3. **Set Appropriate Levels**: Use DEBUG for development, INFO for production
4. **Monitor File Growth**: Use FileMonitor to detect stalled jobs early
5. **Configure Alerts**: Set up alerts for critical failures and anomalies
6. **Aggregate Logs**: Use LogAggregator to search across all components
7. **Archive Old Logs**: Implement log rotation to manage disk space

## Troubleshooting

### No logs appearing in files
- Check that log directory exists and is writable
- Verify `file: True` in config
- Check log level (messages below threshold won't appear)

### Console output too verbose
- Set `level: 'WARNING'` or higher
- Disable console: `console: False`

### Dashboard won't start
- Install Flask: `pip install flask flask-cors` (or `flask-socketio` for `DashboardWithWebSocket`)
- Check port is not in use: `lsof -i :8080`
- Check firewall settings

### Can't find logs for a task
- Verify task_id matches exactly
- Check log directory path
- Use LogAggregator.get_summary() to see what's available

## Examples

See these example scripts:
- `test_stage5_logging.py` - Comprehensive integration test
- `demo_cli_monitor.py` - Visual CLI monitor demonstration

## API Reference

For complete API documentation, see the docstrings in each module:
- `rapier.logging.log_manager` - Central logging coordination
- `rapier.logging.pipeline_logger` - Pipeline-level logging
- `rapier.logging.task_logger` - Task-level logging
- `rapier.logging.slurm_logger` - SLURM job logging
- `rapier.logging.monitor` - File monitoring
- `rapier.logging.aggregator` - Log aggregation
- `rapier.logging.alerts` - Alert system
- `rapier.logging.cli_monitor` - Terminal monitoring
- `rapier.logging.dashboard` - Web dashboard
