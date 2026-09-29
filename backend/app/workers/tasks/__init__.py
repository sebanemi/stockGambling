"""Celery task registry.

Tasks are grouped by pipeline stage so that queue routing and retry policy can
be tuned per stage without touching the pipeline code. Actual ingestion and
training tasks are implemented in the phases that introduce them.
"""

from __future__ import annotations

from app.core.logging import get_logger
from app.workers.celery_app import celery_app

logger = get_logger(__name__)


@celery_app.task(  # type: ignore[misc]  # Celery's task decorator is untyped
    name="system.ping", bind=True, max_retries=3, default_retry_delay=10
)
def ping(self: object) -> dict[str, str]:
    """Trivial task used to verify the broker/worker round-trip."""
    logger.info("system.ping", task_id=getattr(self, "request", None))
    return {"status": "ok"}


@celery_app.task(name="system.describe")  # type: ignore[misc]
def describe() -> dict[str, str]:
    """Return the registered task names. Useful as a smoke test."""
    return {"tasks": ",".join(sorted(name for name in celery_app.tasks if name != "celery."))}
