"""Celery application.

Long-running, CPU-heavy pipeline stages (data ingestion, feature builds,
model training, backtests) run in the worker rather than in the API process so
that a training run can never block a prediction request.
"""

from __future__ import annotations

from celery import Celery
from celery.signals import setup_logging, worker_process_init

from app.core.config import get_settings
from app.core.logging import configure_logging, get_logger

logger = get_logger(__name__)


def _create_celery_app() -> Celery:
    """Build the Celery application from settings."""
    settings = get_settings()
    application = Celery(
        "stockgambling",
        broker=settings.celery_broker_url,
        backend=settings.celery_result_backend_url,
        include=["app.workers.tasks"],
    )
    application.conf.update(
        task_default_queue=settings.celery_task_default_queue,
        task_serializer="json",
        result_serializer="json",
        accept_content=["json"],
        timezone="UTC",
        enable_utc=True,
        task_acks_late=True,
        worker_prefetch_multiplier=1,
        task_always_eager=settings.celery_task_always_eager,
        task_time_limit=settings.celery_task_time_limit,
        task_soft_time_limit=settings.celery_task_soft_time_limit,
        task_track_started=True,
        broker_connection_retry_on_startup=True,
    )
    return application


celery_app = _create_celery_app()


@setup_logging.connect  # type: ignore[misc]  # Celery signals ship no type stubs
def _configure_worker_logging(**_: object) -> None:
    """Route Celery's stdlib logging through structlog."""
    configure_logging()


@worker_process_init.connect  # type: ignore[misc]  # Celery signals ship no type stubs
def _log_worker_boot(sender: object = None, **_: object) -> None:
    """Log worker start-up with the resolved concurrency."""
    settings = get_settings()
    logger.info(
        "worker.startup",
        hostname=getattr(sender, "hostname", None),
        concurrency=settings.celery_worker_concurrency,
    )


__all__ = ["celery_app"]
