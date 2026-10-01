"""Log aggregation and querying across distributed tasks."""

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from .log_manager import get_log_manager


class LogEntry:
    """Container for a single log entry."""

    def __init__(
        self,
        timestamp: str,
        level: str,
        component: str,
        message: str,
        event: Optional[str] = None,
        data: Optional[Dict[str, Any]] = None,
        task_id: Optional[str] = None,
        pipeline_id: Optional[str] = None,
        slurm_job_id: Optional[str] = None
    ):
        self.timestamp = timestamp
        self.level = level
        self.component = component
        self.message = message
        self.event = event
        self.data = data or {}
        self.task_id = task_id
        self.pipeline_id = pipeline_id
        self.slurm_job_id = slurm_job_id

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            'timestamp': self.timestamp,
            'level': self.level,
            'component': self.component,
            'message': self.message,
            'event': self.event,
            'data': self.data,
            'task_id': self.task_id,
            'pipeline_id': self.pipeline_id,
            'slurm_job_id': self.slurm_job_id
        }


class Event:
    """Container for a timeline event."""

    def __init__(
        self,
        timestamp: str,
        event_type: str,
        description: str,
        component: str,
        data: Optional[Dict[str, Any]] = None
    ):
        self.timestamp = timestamp
        self.event_type = event_type
        self.description = description
        self.component = component
        self.data = data or {}


class LogAggregator:
    """Collects logs from distributed tasks and enables querying."""

    def __init__(self, log_dir: str):
        """Initialize log aggregator.

        Args:
            log_dir: Directory containing log files
        """
        self.log_dir = Path(log_dir)
        self.log_manager = get_log_manager()
        self.logger = self.log_manager.get_logger('aggregator')

    def collect_task_logs(self, task_id: str) -> List[LogEntry]:
        """Collect all logs for a specific task.

        Args:
            task_id: Task identifier

        Returns:
            List of log entries for the task
        """
        entries = []

        # Find task log file
        task_log = self.log_dir / f'task_{task_id}.log'
        if task_log.exists():
            entries.extend(self._parse_log_file(task_log, task_id=task_id))

        # Also search other logs for this task_id
        for log_file in self.log_dir.glob('*.log'):
            if log_file.name.startswith(f'task_{task_id}'):
                continue  # Already processed

            entries.extend(self._parse_log_file(log_file, filter_task_id=task_id))

        # Sort by timestamp
        entries.sort(key=lambda e: e.timestamp)

        self.logger.debug(f"Collected {len(entries)} log entries for task {task_id}")
        return entries

    def search_logs(self, query: str, filters: Optional[Dict[str, Any]] = None) -> List[LogEntry]:
        """Search logs with text query and filters.

        Args:
            query: Text to search for in messages
            filters: Optional filters:
                - level: Log level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
                - component: Component name
                - event: Event type
                - task_id: Task identifier
                - pipeline_id: Pipeline identifier
                - start_time: Start timestamp (ISO format)
                - end_time: End timestamp (ISO format)

        Returns:
            List of matching log entries
        """
        if filters is None:
            filters = {}

        entries = []

        # Scan all log files
        for log_file in self.log_dir.glob('*.log'):
            file_entries = self._parse_log_file(log_file)

            for entry in file_entries:
                # Apply filters
                if not self._matches_filters(entry, filters):
                    continue

                # Apply text search
                if query and query.lower() not in entry.message.lower():
                    continue

                entries.append(entry)

        # Sort by timestamp
        entries.sort(key=lambda e: e.timestamp)

        self.logger.debug(f"Search returned {len(entries)} matching entries")
        return entries

    def get_timeline(self, pipeline_id: str) -> List[Event]:
        """Get timeline of events for a pipeline.

        Args:
            pipeline_id: Pipeline identifier

        Returns:
            List of events in chronological order
        """
        events = []

        # Find pipeline log file
        pipeline_log = self.log_dir / f'pipeline_{pipeline_id}.log'
        if pipeline_log.exists():
            events.extend(self._parse_timeline(pipeline_log))

        # Also check main log
        main_log = self.log_dir / 'rapier.log'
        if main_log.exists():
            events.extend(self._parse_timeline(main_log, filter_pipeline_id=pipeline_id))

        # Sort by timestamp
        events.sort(key=lambda e: e.timestamp)

        self.logger.debug(f"Timeline has {len(events)} events for pipeline {pipeline_id}")
        return events

    def _parse_log_file(
        self,
        log_file: Path,
        task_id: Optional[str] = None,
        filter_task_id: Optional[str] = None
    ) -> List[LogEntry]:
        """Parse a log file into log entries.

        Args:
            log_file: Path to log file
            task_id: Task ID to associate with all entries
            filter_task_id: Only return entries matching this task ID

        Returns:
            List of log entries
        """
        entries = []

        try:
            with open(log_file, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue

                    try:
                        # Try to parse as JSON (structured log)
                        data = json.loads(line)
                        entry = LogEntry(
                            timestamp=data.get('timestamp', ''),
                            level=data.get('level', 'INFO'),
                            component=data.get('component', 'unknown'),
                            message=data.get('message', ''),
                            event=data.get('event'),
                            data=data.get('data', {}),
                            task_id=data.get('task_id', task_id),
                            pipeline_id=data.get('pipeline_id'),
                            slurm_job_id=data.get('slurm_job_id')
                        )

                        # Apply filter if specified
                        if filter_task_id and entry.task_id != filter_task_id:
                            continue

                        entries.append(entry)

                    except json.JSONDecodeError:
                        # Not JSON, skip or parse as plain text
                        pass

        except Exception as e:
            self.logger.error(f"Error parsing log file {log_file}: {e}")

        return entries

    def _parse_timeline(
        self,
        log_file: Path,
        filter_pipeline_id: Optional[str] = None
    ) -> List[Event]:
        """Parse a log file for timeline events.

        Args:
            log_file: Path to log file
            filter_pipeline_id: Only return events for this pipeline

        Returns:
            List of events
        """
        events = []

        try:
            with open(log_file, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue

                    try:
                        data = json.loads(line)

                        # Filter by pipeline if specified
                        if filter_pipeline_id and data.get('pipeline_id') != filter_pipeline_id:
                            continue

                        # Only include entries with events
                        if 'event' not in data:
                            continue

                        event = Event(
                            timestamp=data.get('timestamp', ''),
                            event_type=data['event'],
                            description=data.get('message', ''),
                            component=data.get('component', 'unknown'),
                            data=data.get('data', {})
                        )
                        events.append(event)

                    except json.JSONDecodeError:
                        pass

        except Exception as e:
            self.logger.error(f"Error parsing timeline from {log_file}: {e}")

        return events

    def _matches_filters(self, entry: LogEntry, filters: Dict[str, Any]) -> bool:
        """Check if log entry matches filters.

        Args:
            entry: Log entry
            filters: Filter dictionary

        Returns:
            True if entry matches all filters
        """
        if 'level' in filters and entry.level != filters['level']:
            return False

        if 'component' in filters and entry.component != filters['component']:
            return False

        if 'event' in filters and entry.event != filters['event']:
            return False

        if 'task_id' in filters and entry.task_id != filters['task_id']:
            return False

        if 'pipeline_id' in filters and entry.pipeline_id != filters['pipeline_id']:
            return False

        if 'start_time' in filters and entry.timestamp < filters['start_time']:
            return False

        if 'end_time' in filters and entry.timestamp > filters['end_time']:
            return False

        return True

    def get_summary(self) -> Dict[str, Any]:
        """Get summary statistics for all logs.

        Returns:
            Dictionary with summary statistics
        """
        total_entries = 0
        by_level = {'DEBUG': 0, 'INFO': 0, 'WARNING': 0, 'ERROR': 0, 'CRITICAL': 0}
        by_component = {}

        for log_file in self.log_dir.glob('*.log'):
            entries = self._parse_log_file(log_file)
            total_entries += len(entries)

            for entry in entries:
                by_level[entry.level] = by_level.get(entry.level, 0) + 1
                by_component[entry.component] = by_component.get(entry.component, 0) + 1

        return {
            'total_entries': total_entries,
            'by_level': by_level,
            'by_component': by_component,
            'log_files': len(list(self.log_dir.glob('*.log')))
        }
