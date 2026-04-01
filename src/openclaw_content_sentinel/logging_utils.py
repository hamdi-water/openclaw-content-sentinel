"""
Phase 21: SRE Plane — Structured Logging & Trace-ID Propagation.

================================================================

This module implements a JSON-based logging formatter for Loki/ELK compatibility.
It automatically injects 'trace_id', 'worker_id', and 'persona_id' into all
log records if they are present in the logging context.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any

from .config import AppConfig


class StructuredJSONFormatter(logging.Formatter):
    """Formatter that outputs JSON strings for structured logging."""

    def __init__(self, config: AppConfig) -> None:
        super().__init__()
        self.config = config

    def format(self, record: logging.LogRecord) -> str:
        """Format the log record as a JSON string."""
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "module": record.module,
            "func": record.funcName,
            "line": record.lineno,
            "worker_id": getattr(self.config, "worker_id", "unknown"),
        }

        # Inject context-specific metadata
        if hasattr(record, "trace_id"):
            payload["trace_id"] = record.trace_id
        if hasattr(record, "persona_id"):
            payload["persona_id"] = record.persona_id
        if hasattr(record, "run_id"):
            payload["run_id"] = record.run_id

        # Include exception info if present
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)

        return json.dumps(payload)


def setup_logging(config: AppConfig, level: int = logging.INFO) -> None:
    """Initialize the global logging system with structured JSON output."""
    root = logging.getLogger()
    root.setLevel(level)

    # Remove existing handlers
    for handler in root.handlers[:]:
        root.removeHandler(handler)

    # Add JSON console handler
    console = logging.StreamHandler()
    console.setFormatter(StructuredJSONFormatter(config))
    root.addHandler(console)

    # Disable noisy third-party loggers
    logging.getLogger("chromadb").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)


def get_logger(name: str, run_id: str = "") -> logging.LoggerAdapter:
    """Return a logger adapter that injects run_id as trace_id."""
    logger = logging.getLogger(name)
    return logging.LoggerAdapter(logger, {"trace_id": run_id, "run_id": run_id})
