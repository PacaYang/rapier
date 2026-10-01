"""Web-based dashboard for real-time pipeline monitoring."""

import json
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..core.pipeline import Pipeline
from ..core.task import Task
from ..core.enums import TaskStatus
from .alerts import ExecutionState
from .log_manager import get_log_manager


class Dashboard:
    """Real-time monitoring dashboard (web-based)."""

    def __init__(
        self,
        host: str = 'localhost',
        port: int = 8080,
        log_dir: Optional[str] = None
    ):
        """Initialize dashboard.

        Args:
            host: Server host address
            port: Server port
            log_dir: Directory for log files
        """
        self.host = host
        self.port = port
        self.log_dir = Path(log_dir) if log_dir else None

        self.log_manager = get_log_manager()
        self.logger = self.log_manager.get_logger('dashboard')

        self.server_thread: Optional[threading.Thread] = None
        self.running = False

        # Current state
        self.pipeline_state: Dict[str, Any] = {}

    def start(self) -> None:
        """Launch dashboard server in background thread."""
        if self.running:
            self.logger.warning("Dashboard already running")
            return

        self.running = True
        self.server_thread = threading.Thread(target=self._run_server, daemon=True)
        self.server_thread.start()

        self.logger.info(f"Dashboard started at http://{self.host}:{self.port}")

    def stop(self) -> None:
        """Shutdown dashboard server."""
        if not self.running:
            return

        self.running = False

        if self.server_thread:
            self.server_thread.join(timeout=5)

        self.logger.info("Dashboard stopped")

    def update_status(self, pipeline_status: Dict[str, Any]) -> None:
        """Push status update to dashboard.

        Args:
            pipeline_status: Dictionary with status information:
                - pipeline_id: Pipeline identifier
                - pipeline_name: Pipeline name
                - tasks: List of task dictionaries
                - state: ExecutionState data
        """
        self.pipeline_state = pipeline_status
        self.logger.debug(f"Dashboard status updated: {pipeline_status.get('pipeline_id')}")

    def get_snapshot(self) -> Dict[str, Any]:
        """Get current state snapshot.

        Returns:
            Dictionary with current dashboard state
        """
        return {
            'timestamp': datetime.now().isoformat(),
            'running': self.running,
            'host': self.host,
            'port': self.port,
            'pipeline': self.pipeline_state
        }

    def _run_server(self) -> None:
        """Run the web server (internal method)."""
        try:
            # Try to import Flask
            from flask import Flask, render_template, jsonify
            from flask_cors import CORS

            app = Flask(__name__, template_folder=str(Path(__file__).parent.parent.parent / 'templates'))
            CORS(app)

            @app.route('/')
            def index():
                """Serve dashboard HTML."""
                return render_template('dashboard.html')

            @app.route('/api/status')
            def get_status():
                """Get current status as JSON."""
                return jsonify(self.get_snapshot())

            @app.route('/api/pipeline/<pipeline_id>')
            def get_pipeline(pipeline_id):
                """Get pipeline details."""
                if self.pipeline_state.get('pipeline_id') == pipeline_id:
                    return jsonify(self.pipeline_state)
                return jsonify({'error': 'Pipeline not found'}), 404

            # Run server
            app.run(host=self.host, port=self.port, debug=False, use_reloader=False)

        except ImportError:
            self.logger.error("Flask not installed. Install with: pip install flask flask-cors")
            self.running = False
        except Exception as e:
            self.logger.error(f"Error running dashboard server: {e}")
            self.running = False

    def format_pipeline_data(
        self,
        pipeline: Pipeline,
        state: ExecutionState,
        tasks: Optional[List[Task]] = None
    ) -> Dict[str, Any]:
        """Format pipeline data for dashboard display.

        Args:
            pipeline: Pipeline instance
            state: Execution state
            tasks: Optional list of tasks (uses pipeline.tasks if not provided)

        Returns:
            Formatted pipeline data dictionary
        """
        if tasks is None:
            tasks = list(pipeline.tasks.values()) if hasattr(pipeline, 'tasks') else []

        task_data = []
        for task in tasks:
            task_info = {
                'id': task.task_id,
                'method': task.method.name if task.method else 'unknown',
                'status': task.status.value if hasattr(task.status, 'value') else str(task.status),
                'dependencies': task.dependencies if hasattr(task, 'dependencies') else []
            }
            task_data.append(task_info)

        return {
            'pipeline_id': pipeline.name,
            'pipeline_name': pipeline.name,
            'tasks': task_data,
            'state': {
                'start_time': state.start_time.isoformat(),
                'tasks_completed': state.tasks_completed,
                'tasks_failed': state.tasks_failed,
                'tasks_running': state.tasks_running,
                'elapsed_seconds': (datetime.now() - state.start_time).total_seconds()
            }
        }


# WebSocket support for real-time updates (optional, requires additional dependencies)

class DashboardWithWebSocket(Dashboard):
    """Dashboard with WebSocket support for real-time updates."""

    def __init__(self, host: str = 'localhost', port: int = 8080, log_dir: Optional[str] = None):
        super().__init__(host, port, log_dir)
        self.clients: List[Any] = []

    def _run_server(self) -> None:
        """Run server with WebSocket support."""
        try:
            from flask import Flask, render_template, jsonify
            from flask_socketio import SocketIO, emit

            app = Flask(__name__, template_folder=str(Path(__file__).parent.parent.parent / 'templates'))
            socketio = SocketIO(app, cors_allowed_origins="*")

            @app.route('/')
            def index():
                return render_template('dashboard.html')

            @app.route('/api/status')
            def get_status():
                return jsonify(self.get_snapshot())

            @socketio.on('connect')
            def handle_connect():
                self.logger.info("Client connected to dashboard")
                # Send current state to new client
                emit('status_update', self.pipeline_state)

            @socketio.on('disconnect')
            def handle_disconnect():
                self.logger.info("Client disconnected from dashboard")

            # Override update_status to push via WebSocket
            original_update = self.update_status

            def update_with_push(pipeline_status: Dict[str, Any]) -> None:
                original_update(pipeline_status)
                socketio.emit('status_update', pipeline_status)

            self.update_status = update_with_push

            # Run server
            socketio.run(app, host=self.host, port=self.port, debug=False, use_reloader=False)

        except ImportError:
            self.logger.error(
                "Flask-SocketIO not installed. "
                "Install with: pip install flask-socketio"
            )
            # Fall back to regular dashboard
            super()._run_server()
        except Exception as e:
            self.logger.error(f"Error running WebSocket dashboard: {e}")
            self.running = False
