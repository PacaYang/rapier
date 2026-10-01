"""Terminal-based monitoring interface for pipeline execution."""

import sys
from datetime import datetime
from typing import Dict, List, Optional

from ..core.pipeline import Pipeline
from ..core.task import Task
from ..core.enums import TaskStatus
from .alerts import ExecutionState
from .log_manager import get_log_manager


class CLIMonitor:
    """Terminal-based monitoring for headless environments."""

    def __init__(self, use_colors: bool = True, use_rich: bool = True):
        """Initialize CLI monitor.

        Args:
            use_colors: Whether to use ANSI colors
            use_rich: Whether to use rich library (if available)
        """
        self.use_colors = use_colors
        self.use_rich = use_rich and self._check_rich_available()

        self.log_manager = get_log_manager()
        self.logger = self.log_manager.get_logger('monitor.cli')

        # ANSI color codes
        self.colors = {
            'RESET': '\033[0m',
            'BOLD': '\033[1m',
            'GREEN': '\033[32m',
            'YELLOW': '\033[33m',
            'RED': '\033[31m',
            'BLUE': '\033[34m',
            'CYAN': '\033[36m',
            'GRAY': '\033[90m',
        } if use_colors else {k: '' for k in ['RESET', 'BOLD', 'GREEN', 'YELLOW', 'RED', 'BLUE', 'CYAN', 'GRAY']}

    def _check_rich_available(self) -> bool:
        """Check if rich library is available."""
        try:
            import rich
            return True
        except ImportError:
            return False

    def display_progress(self, pipeline: Pipeline, state: ExecutionState) -> None:
        """Display pipeline execution progress.

        Args:
            pipeline: Pipeline instance
            state: Current execution state
        """
        if self.use_rich:
            self._display_progress_rich(pipeline, state)
        else:
            self._display_progress_simple(pipeline, state)

    def _display_progress_simple(self, pipeline: Pipeline, state: ExecutionState) -> None:
        """Display progress using simple text output."""
        c = self.colors

        # Calculate progress
        total_tasks = len(pipeline.tasks)
        completed = state.tasks_completed
        failed = state.tasks_failed
        running = state.tasks_running
        pending = total_tasks - completed - failed - running

        progress_pct = (completed / total_tasks * 100) if total_tasks > 0 else 0

        # Print header
        print(f"\n{c['BOLD']}Pipeline: {pipeline.name}{c['RESET']}")
        print(f"{c['GRAY']}{'=' * 60}{c['RESET']}")

        # Print summary
        print(f"\n{c['BOLD']}Progress:{c['RESET']} {progress_pct:.1f}% "
              f"({completed}/{total_tasks} tasks)")

        # Print status breakdown
        print(f"  {c['GREEN']}✓ Completed:{c['RESET']} {completed}")
        if running > 0:
            print(f"  {c['BLUE']}⟳ Running:{c['RESET']} {running}")
        if pending > 0:
            print(f"  {c['GRAY']}○ Pending:{c['RESET']} {pending}")
        if failed > 0:
            print(f"  {c['RED']}✗ Failed:{c['RESET']} {failed}")

        # Print elapsed time
        elapsed = (datetime.now() - state.start_time).total_seconds()
        print(f"\n{c['BOLD']}Elapsed:{c['RESET']} {self._format_duration(elapsed)}")

        print(f"{c['GRAY']}{'=' * 60}{c['RESET']}\n")

    def _display_progress_rich(self, pipeline: Pipeline, state: ExecutionState) -> None:
        """Display progress using rich library."""
        try:
            from rich.console import Console
            from rich.table import Table
            from rich.progress import Progress, BarColumn, TextColumn, TimeElapsedColumn

            console = Console()

            # Create summary table
            table = Table(title=f"Pipeline: {pipeline.name}", show_header=True)
            table.add_column("Status", style="bold")
            table.add_column("Count", justify="right")

            total_tasks = len(pipeline.tasks)
            table.add_row("✓ Completed", f"[green]{state.tasks_completed}[/green]")
            table.add_row("⟳ Running", f"[blue]{state.tasks_running}[/blue]")
            table.add_row("○ Pending", f"[dim]{total_tasks - state.tasks_completed - state.tasks_failed - state.tasks_running}[/dim]")
            table.add_row("✗ Failed", f"[red]{state.tasks_failed}[/red]")

            console.print(table)

            # Print elapsed time
            elapsed = (datetime.now() - state.start_time).total_seconds()
            console.print(f"\n[bold]Elapsed:[/bold] {self._format_duration(elapsed)}")

        except ImportError:
            # Fall back to simple display
            self._display_progress_simple(pipeline, state)

    def display_task_list(self, tasks: List[Task]) -> None:
        """Display list of tasks with their status.

        Args:
            tasks: List of tasks to display
        """
        if self.use_rich:
            self._display_task_list_rich(tasks)
        else:
            self._display_task_list_simple(tasks)

    def _display_task_list_simple(self, tasks: List[Task]) -> None:
        """Display task list using simple text output."""
        c = self.colors

        print(f"\n{c['BOLD']}Tasks:{c['RESET']}")
        print(f"{c['GRAY']}{'=' * 60}{c['RESET']}")

        for task in tasks:
            status = task.status
            if status == TaskStatus.COMPLETED:
                status_str = f"{c['GREEN']}✓ COMPLETED{c['RESET']}"
            elif status == TaskStatus.RUNNING:
                status_str = f"{c['BLUE']}⟳ RUNNING{c['RESET']}"
            elif status == TaskStatus.FAILED:
                status_str = f"{c['RED']}✗ FAILED{c['RESET']}"
            else:
                status_str = f"{c['GRAY']}○ {status.value}{c['RESET']}"

            method_name = task.method.name if task.method else 'unknown'
            print(f"  [{task.task_id}] {status_str} - {method_name}")

        print(f"{c['GRAY']}{'=' * 60}{c['RESET']}\n")

    def _display_task_list_rich(self, tasks: List[Task]) -> None:
        """Display task list using rich library."""
        try:
            from rich.console import Console
            from rich.table import Table

            console = Console()

            table = Table(title="Tasks", show_header=True)
            table.add_column("Task ID", style="cyan")
            table.add_column("Status", style="bold")
            table.add_column("Method")

            for task in tasks:
                status = task.status
                if status == TaskStatus.COMPLETED:
                    status_str = "[green]✓ COMPLETED[/green]"
                elif status == TaskStatus.RUNNING:
                    status_str = "[blue]⟳ RUNNING[/blue]"
                elif status == TaskStatus.FAILED:
                    status_str = "[red]✗ FAILED[/red]"
                else:
                    status_str = f"[dim]○ {status.value}[/dim]"

                method_name = task.method.name if task.method else 'unknown'
                table.add_row(task.task_id, status_str, method_name)

            console.print(table)

        except ImportError:
            self._display_task_list_simple(tasks)

    def display_logs(self, task_id: str, follow: bool = False) -> None:
        """Display logs for a specific task.

        Args:
            task_id: Task identifier
            follow: Whether to follow log updates (tail -f style)
        """
        from .aggregator import LogAggregator

        log_dir = self.log_manager.log_directory
        if not log_dir:
            print("No log directory configured")
            return

        aggregator = LogAggregator(str(log_dir))
        entries = aggregator.collect_task_logs(task_id)

        if not entries:
            print(f"No logs found for task {task_id}")
            return

        c = self.colors
        print(f"\n{c['BOLD']}Logs for task {task_id}:{c['RESET']}")
        print(f"{c['GRAY']}{'=' * 60}{c['RESET']}")

        for entry in entries:
            timestamp = entry.timestamp[:19] if len(entry.timestamp) >= 19 else entry.timestamp

            # Color code by level
            if entry.level == 'ERROR':
                level_str = f"{c['RED']}{entry.level}{c['RESET']}"
            elif entry.level == 'WARNING':
                level_str = f"{c['YELLOW']}{entry.level}{c['RESET']}"
            elif entry.level == 'INFO':
                level_str = f"{c['GREEN']}{entry.level}{c['RESET']}"
            else:
                level_str = f"{c['GRAY']}{entry.level}{c['RESET']}"

            print(f"[{timestamp}] {level_str} {entry.message}")

        print(f"{c['GRAY']}{'=' * 60}{c['RESET']}\n")

        if follow:
            print("Note: Follow mode (-f) not yet implemented")

    def _format_duration(self, seconds: float) -> str:
        """Format duration in human-readable format.

        Args:
            seconds: Duration in seconds

        Returns:
            Formatted duration string
        """
        if seconds < 60:
            return f"{seconds:.1f}s"
        elif seconds < 3600:
            minutes = int(seconds / 60)
            secs = int(seconds % 60)
            return f"{minutes}m {secs}s"
        else:
            hours = int(seconds / 3600)
            minutes = int((seconds % 3600) / 60)
            return f"{hours}h {minutes}m"

    def print_status_line(self, message: str, status: str = 'info') -> None:
        """Print a status line with appropriate styling.

        Args:
            message: Status message
            status: Status type (info, success, warning, error)
        """
        c = self.colors

        if status == 'success':
            prefix = f"{c['GREEN']}✓{c['RESET']}"
        elif status == 'warning':
            prefix = f"{c['YELLOW']}⚠{c['RESET']}"
        elif status == 'error':
            prefix = f"{c['RED']}✗{c['RESET']}"
        else:
            prefix = f"{c['BLUE']}ℹ{c['RESET']}"

        print(f"{prefix} {message}")

    def clear_screen(self) -> None:
        """Clear the terminal screen."""
        if sys.platform == 'win32':
            import os
            os.system('cls')
        else:
            print('\033[2J\033[H', end='')
