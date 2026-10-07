"""Metadata ingestion tasks.

Ingestion runs in the worker, never in the API process: a fetch that hangs on a
slow Argentine institutional site must not delay a health check. The task body
is a thin shell around :mod:`app.ingestion` so the same code path is exercised
by the task, by a management script and by the tests.
"""

from __future__ import annotations

from datetime import date

from app.core.config import get_settings
from app.core.logging import get_logger
from app.db.session import session_scope
from app.ingestion import JOB_NAME, fetch_and_ingest
from app.providers.registry import configured_cedear_providers
from app.workers.celery_app import celery_app

logger = get_logger(__name__)

TASK_NAME = JOB_NAME


@celery_app.task(  # type: ignore[misc]  # Celery's task decorator is untyped
    name=TASK_NAME,
    bind=True,
    max_retries=2,
    default_retry_delay=300,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_jitter=True,
)
def ingest_cedear_metadata(self: object, effective_date: str | None = None) -> dict[str, object]:
    """Refresh the CEDEAR universe and its ratio history.

    Args:
        effective_date: ISO date the new ratio periods start on. ``None`` means
            today in Argentine time, which is the right default: a run at 21:00
            UTC is still the same BYMA session.

    Returns:
        The :class:`~app.ingestion.service.IngestionReport` as a plain dict, so
        it round-trips through the JSON result backend.

    Retries are configured on the task rather than inside the service: a failed
    fetch is retried by the queue, whereas a *bad* payload is rejected by the
    provider and recorded in the run's quarantine, which no retry can fix.
    """
    settings = get_settings()
    providers = configured_cedear_providers(settings)
    parsed = date.fromisoformat(effective_date) if effective_date else None

    logger.info(
        "ingestion.task_started",
        task_id=getattr(self, "request", None),
        providers=[provider.name for provider in providers],
        effective_date=parsed.isoformat() if parsed else None,
    )

    with session_scope(settings) as session:
        report = fetch_and_ingest(session, providers, effective_date=parsed)

    return report.as_dict()


__all__ = ["TASK_NAME", "ingest_cedear_metadata"]
