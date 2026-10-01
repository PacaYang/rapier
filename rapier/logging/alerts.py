"""Alert system for notifications and monitoring."""

from datetime import datetime, timedelta
from typing import Any, Callable, Dict, List, Optional
from enum import Enum

from .log_manager import get_log_manager


class AlertLevel(Enum):
    """Alert severity levels."""
    INFO = 'INFO'
    WARNING = 'WARNING'
    ERROR = 'ERROR'
    CRITICAL = 'CRITICAL'


class Alert:
    """Container for an alert."""

    def __init__(
        self,
        level: AlertLevel,
        message: str,
        component: str,
        data: Optional[Dict[str, Any]] = None,
        timestamp: Optional[datetime] = None
    ):
        self.level = level
        self.message = message
        self.component = component
        self.data = data or {}
        self.timestamp = timestamp or datetime.now()

    def __str__(self) -> str:
        return f"[{self.level.value}] {self.component}: {self.message}"


class AlertCondition:
    """Defines a condition that triggers an alert."""

    def __init__(
        self,
        name: str,
        check: Callable[[Dict[str, Any]], bool],
        level: AlertLevel,
        message_template: str,
        cooldown_seconds: int = 300
    ):
        self.name = name
        self.check = check
        self.level = level
        self.message_template = message_template
        self.cooldown_seconds = cooldown_seconds
        self.last_triggered: Optional[datetime] = None

    def can_trigger(self) -> bool:
        """Check if alert can be triggered (respects cooldown)."""
        if self.last_triggered is None:
            return True

        elapsed = (datetime.now() - self.last_triggered).total_seconds()
        return elapsed >= self.cooldown_seconds


class ExecutionState:
    """Container for pipeline execution state."""

    def __init__(self, pipeline_id: str, start_time: Optional[datetime] = None):
        self.pipeline_id = pipeline_id
        self.start_time = start_time or datetime.now()
        self.tasks_completed = 0
        self.tasks_failed = 0
        self.tasks_running = 0
        self.task_durations: Dict[str, float] = {}
        self.resource_usage: Dict[str, Dict[str, Any]] = {}


class AlertManager:
    """Notification system for monitoring and alerting."""

    def __init__(self, log_dir: Optional[str] = None):
        """Initialize alert manager.

        Args:
            log_dir: Directory for log files
        """
        self.log_manager = get_log_manager()
        self.logger = self.log_manager.get_logger('alerts')

        self.conditions: List[AlertCondition] = []
        self.notifications: List[Callable[[Alert], None]] = []
        self.alert_history: List[Alert] = []

        # Register default conditions
        self._register_default_conditions()

    def _register_default_conditions(self) -> None:
        """Register default alert conditions."""
        # Task failure
        self.register_alert(
            name='task_failure',
            check=lambda state: state.tasks_failed > 0,
            level=AlertLevel.ERROR,
            message_template=f'Task failed in pipeline {{pipeline_id}}',
            cooldown_seconds=60
        )

        # High failure rate
        self.register_alert(
            name='high_failure_rate',
            check=lambda state: (
                state.tasks_completed + state.tasks_failed > 0 and
                state.tasks_failed / (state.tasks_completed + state.tasks_failed) > 0.5
            ),
            level=AlertLevel.CRITICAL,
            message_template='High failure rate detected: {{failure_rate:.1%}}',
            cooldown_seconds=300
        )

        # No progress
        self.register_alert(
            name='no_progress',
            check=lambda state: (
                (datetime.now() - state.start_time).total_seconds() > 600 and
                state.tasks_completed == 0 and
                state.tasks_running == 0
            ),
            level=AlertLevel.WARNING,
            message_template='No progress in 10 minutes',
            cooldown_seconds=600
        )

    def register_alert(
        self,
        name: str,
        check: Callable[[ExecutionState], bool],
        level: AlertLevel,
        message_template: str,
        cooldown_seconds: int = 300
    ) -> None:
        """Register a new alert condition.

        Args:
            name: Unique alert name
            check: Function that returns True when alert should trigger
            level: Alert severity level
            message_template: Message template (can use {pipeline_id}, etc.)
            cooldown_seconds: Minimum seconds between alerts
        """
        condition = AlertCondition(
            name=name,
            check=check,
            level=level,
            message_template=message_template,
            cooldown_seconds=cooldown_seconds
        )
        self.conditions.append(condition)

        self.logger.debug(f"Registered alert condition: {name}")

    def add_notification_method(self, handler: Callable[[Alert], None]) -> None:
        """Add a notification handler.

        Args:
            handler: Function that handles an alert
        """
        self.notifications.append(handler)
        self.logger.debug(f"Added notification handler: {handler.__name__}")

    def check_alerts(self, state: ExecutionState) -> List[Alert]:
        """Check all alert conditions against current state.

        Args:
            state: Current execution state

        Returns:
            List of triggered alerts
        """
        triggered = []

        for condition in self.conditions:
            if not condition.can_trigger():
                continue

            try:
                if condition.check(state):
                    # Format message
                    message = condition.message_template.format(
                        pipeline_id=state.pipeline_id,
                        tasks_completed=state.tasks_completed,
                        tasks_failed=state.tasks_failed,
                        failure_rate=(
                            state.tasks_failed / (state.tasks_completed + state.tasks_failed)
                            if state.tasks_completed + state.tasks_failed > 0
                            else 0
                        )
                    )

                    alert = Alert(
                        level=condition.level,
                        message=message,
                        component='alert_manager',
                        data={
                            'condition': condition.name,
                            'pipeline_id': state.pipeline_id
                        }
                    )

                    triggered.append(alert)
                    condition.last_triggered = datetime.now()
                    self.alert_history.append(alert)

                    # Send notifications
                    self._send_notification(alert)

            except Exception as e:
                self.logger.error(f"Error checking alert condition {condition.name}: {e}")

        return triggered

    def _send_notification(self, alert: Alert) -> None:
        """Send alert through all notification channels.

        Args:
            alert: Alert to send
        """
        self.logger.log(
            getattr(self.logger, alert.level.value.lower()).level
            if hasattr(getattr(self.logger, alert.level.value.lower(), None), 'level')
            else 20,
            f"ALERT: {alert}"
        )

        for handler in self.notifications:
            try:
                handler(alert)
            except Exception as e:
                self.logger.error(f"Error in notification handler {handler.__name__}: {e}")

    def send_notification(self, level: str, message: str, component: str = 'manual') -> None:
        """Manually send a notification.

        Args:
            level: Alert level (INFO, WARNING, ERROR, CRITICAL)
            message: Notification message
            component: Component name
        """
        alert_level = AlertLevel[level.upper()]
        alert = Alert(
            level=alert_level,
            message=message,
            component=component
        )

        self.alert_history.append(alert)
        self._send_notification(alert)

    def get_recent_alerts(self, minutes: int = 60) -> List[Alert]:
        """Get recent alerts within time window.

        Args:
            minutes: Number of minutes to look back

        Returns:
            List of recent alerts
        """
        cutoff = datetime.now() - timedelta(minutes=minutes)
        return [
            alert for alert in self.alert_history
            if alert.timestamp >= cutoff
        ]

    def clear_history(self) -> None:
        """Clear alert history."""
        self.alert_history.clear()
        self.logger.debug("Cleared alert history")


# Default notification handlers

def log_file_handler(log_path: str) -> Callable[[Alert], None]:
    """Create a notification handler that writes to a log file.

    Args:
        log_path: Path to alert log file

    Returns:
        Notification handler function
    """
    def handler(alert: Alert) -> None:
        with open(log_path, 'a', encoding='utf-8') as f:
            timestamp = alert.timestamp.strftime('%Y-%m-%d %H:%M:%S')
            f.write(f"[{timestamp}] {alert}\n")

    return handler


def webhook_handler(url: str) -> Callable[[Alert], None]:
    """Create a notification handler that posts to a webhook.

    Args:
        url: Webhook URL

    Returns:
        Notification handler function
    """
    def handler(alert: Alert) -> None:
        import requests
        import json

        payload = {
            'level': alert.level.value,
            'message': alert.message,
            'component': alert.component,
            'timestamp': alert.timestamp.isoformat(),
            'data': alert.data
        }

        try:
            response = requests.post(url, json=payload, timeout=10)
            response.raise_for_status()
        except Exception as e:
            # Silently fail - don't want alerts to break execution
            pass

    return handler


def email_handler(smtp_config: Dict[str, str]) -> Callable[[Alert], None]:
    """Create a notification handler that sends emails.

    Args:
        smtp_config: SMTP configuration with keys:
            - host: SMTP server host
            - port: SMTP server port
            - username: SMTP username
            - password: SMTP password
            - from_addr: From email address
            - to_addrs: List of recipient addresses

    Returns:
        Notification handler function
    """
    def handler(alert: Alert) -> None:
        import smtplib
        from email.mime.text import MIMEText

        # Only send emails for ERROR and CRITICAL alerts
        if alert.level not in [AlertLevel.ERROR, AlertLevel.CRITICAL]:
            return

        msg = MIMEText(f"{alert}\n\nData: {alert.data}")
        msg['Subject'] = f"[{alert.level.value}] Rapier Alert: {alert.component}"
        msg['From'] = smtp_config['from_addr']
        msg['To'] = ', '.join(smtp_config['to_addrs'])

        try:
            with smtplib.SMTP(smtp_config['host'], smtp_config['port']) as server:
                server.starttls()
                server.login(smtp_config['username'], smtp_config['password'])
                server.send_message(msg)
        except Exception as e:
            # Silently fail
            pass

    return handler
