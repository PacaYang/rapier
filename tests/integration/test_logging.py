"""Integration tests for the logging system."""

import json
import logging

import pytest

from rapier.logging.log_manager import LogManager
from rapier.logging.formatters import StructuredFormatter, HumanFormatter


@pytest.fixture(autouse=True)
def reset_rapier_logger():
    """Ensure the shared 'rapier' logger is clean between tests."""
    yield
    root = logging.getLogger("rapier")
    for handler in root.handlers[:]:
        handler.close()
        root.removeHandler(handler)


class TestLogManager:
    def test_console_only(self):
        manager = LogManager()
        manager.configure_logging({"console": True, "file": False})
        logger = manager.get_logger("rapier.test")
        # Should not raise
        logger.info("hello")

    def test_log_file_created(self, tmp_path):
        manager = LogManager(str(tmp_path))
        manager.configure_logging({"file": True, "structured": True})
        logger = manager.get_logger("rapier.test")
        logger.info("a message")

        log_file = tmp_path / "rapier.log"
        assert log_file.exists()
        assert "a message" in log_file.read_text()

    def test_structured_format_is_json(self, tmp_path):
        manager = LogManager(str(tmp_path))
        manager.configure_logging({"file": True, "structured": True, "console": False})
        logger = manager.get_logger("rapier.structured")
        logger.info("structured message")
        manager.close_all()

        log_file = tmp_path / "rapier.log"
        lines = [l for l in log_file.read_text().splitlines() if l.strip()]
        # Each line should be valid JSON
        record = json.loads(lines[-1])
        assert record["message"] == "structured message"

    def test_get_log_file_path(self, tmp_path):
        manager = LogManager(str(tmp_path))
        path = manager.get_log_file("pipeline")
        assert path.endswith("pipeline.log")

    def test_get_log_file_console_only(self):
        manager = LogManager()
        assert manager.get_log_file("pipeline") is None

    def test_component_logger_separate_file(self, tmp_path):
        manager = LogManager(str(tmp_path))
        manager.configure_logging({"file": True, "console": False})
        logger = manager.create_component_logger("slurm", structured=False)
        logger.warning("slurm warning")
        manager.close_all()

        component_log = tmp_path / "slurm.log"
        assert component_log.exists()
        assert "slurm warning" in component_log.read_text()

    def test_logger_name_prefixing(self):
        manager = LogManager()
        manager.configure_logging({"console": True, "file": False})
        logger = manager.get_logger("engine.executor")
        assert logger.name == "rapier.engine.executor"


class TestFormatters:
    def _make_record(self, msg="test", level=logging.INFO):
        return logging.LogRecord(
            name="rapier.test",
            level=level,
            pathname=__file__,
            lineno=1,
            msg=msg,
            args=(),
            exc_info=None,
        )

    def test_structured_formatter_outputs_json(self):
        formatter = StructuredFormatter(include_context=True)
        record = self._make_record("hello")
        output = formatter.format(record)
        data = json.loads(output)
        assert data["message"] == "hello"
        assert data["level"] == "INFO"

    def test_human_formatter_includes_message(self):
        formatter = HumanFormatter(use_colors=False)
        record = self._make_record("readable")
        output = formatter.format(record)
        assert "readable" in output

    def test_human_formatter_no_color_codes(self):
        formatter = HumanFormatter(use_colors=False)
        record = self._make_record("nocolor")
        output = formatter.format(record)
        assert "\x1b[" not in output  # no ANSI escapes
