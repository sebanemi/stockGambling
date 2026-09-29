"""Structured logging configuration.

Logs are emitted as structured key/value pairs so that model-training runs
and data-ingestion jobs can be traced later. JSON is used in production for
log aggregation; a coloured console renderer is used in development.
"""

from __future__ import annotations

import logging
import sys
from typing import Any, cast

import structlog

from app.core.config import Settings, get_settings

_CONFIGURED = False


def configure_logging(settings: Settings | None = None) -> None:
    """Configure ``structlog`` and the stdlib ``logging`` module.

    Safe to call more than once; subsequent calls are ignored.
    """
    global _CONFIGURED
    if _CONFIGURED:
        return

    settings = settings or get_settings()
    level = getattr(logging, settings.log_level, logging.INFO)

    logging.basicConfig(format="%(message)s", stream=sys.stdout, level=level)
    for noisy in ("uvicorn.access", "sqlalchemy.engine.Engine"):
        logging.getLogger(noisy).setLevel(max(level, logging.WARNING))

    renderer: Any = (
        structlog.processors.JSONRenderer()
        if settings.log_format == "json"
        else structlog.dev.ConsoleRenderer(colors=sys.stdout.isatty())
    )

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.UnicodeDecoder(),
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )
    _CONFIGURED = True


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    """Return a bound structlog logger for ``name``."""
    configure_logging()
    return cast("structlog.stdlib.BoundLogger", structlog.get_logger(name))
