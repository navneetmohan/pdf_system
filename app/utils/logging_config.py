"""
Structured logging configuration.
Emits JSON in production (easy to ingest into Loki/Datadog/CloudWatch).
Emits readable text in development.
"""
from __future__ import annotations

import json
import logging
import logging.handlers
import os
import time
from pathlib import Path

from flask import Flask, g, request


class _JSONFormatter(logging.Formatter):
    """Format log records as single-line JSON."""

    def format(self, record: logging.LogRecord) -> str:
        log_data = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        # Include extra fields passed via logger.info(..., extra={...})
        for key, value in record.__dict__.items():
            if key not in (
                "args", "asctime", "created", "exc_info", "exc_text", "filename",
                "funcName", "id", "levelname", "levelno", "lineno", "message",
                "module", "msecs", "msg", "name", "pathname", "process",
                "processName", "relativeCreated", "stack_info", "thread", "threadName",
            ):
                log_data[key] = value

        if record.exc_info:
            log_data["exception"] = self.formatException(record.exc_info)

        return json.dumps(log_data, default=str)


def configure_logging(app: Flask):
    log_level_str = app.config.get("LOG_LEVEL", "INFO")
    log_level = getattr(logging, log_level_str, logging.INFO)
    use_json = app.config.get("LOG_JSON", True)
    log_file = app.config.get("LOG_FILE", "logs/app.log")

    # Ensure log directory exists
    log_dir = Path(log_file).parent
    log_dir.mkdir(parents=True, exist_ok=True)

    # Choose formatter
    if use_json:
        formatter = _JSONFormatter()
    else:
        formatter = logging.Formatter(
            "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )

    # Root logger
    root = logging.getLogger()
    root.setLevel(log_level)

    # Console handler
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    root.addHandler(console_handler)

    # Rotating file handler
    try:
        file_handler = logging.handlers.RotatingFileHandler(
            log_file,
            maxBytes=10 * 1024 * 1024,  # 10 MB per file
            backupCount=5,
            encoding="utf-8",
        )
        file_handler.setFormatter(formatter)
        root.addHandler(file_handler)
    except OSError as e:
        logging.warning("Could not create log file handler: %s", e)

    # Suppress noisy third-party loggers
    for noisy in ("werkzeug", "urllib3", "charset_normalizer"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    # ── Request logging middleware ─────────────────────────────────────────────
    req_logger = logging.getLogger("request")

    @app.before_request
    def _before():
        g._request_start = time.perf_counter()

    @app.after_request
    def _after(response):
        duration_ms = round((time.perf_counter() - getattr(g, "_request_start", time.perf_counter())) * 1000, 2)
        req_logger.info(
            "%s %s %s",
            request.method, request.path, response.status_code,
            extra={
                "method": request.method,
                "path": request.path,
                "status": response.status_code,
                "duration_ms": duration_ms,
                "ip": request.remote_addr,
                "user_agent": request.user_agent.string[:200],
            },
        )
        return response
