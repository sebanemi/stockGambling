"""Theoretical price computation task.

Runs in the worker, never in the API process. The task body is a thin shell
around :mod:`app.ingestion.theoretical`, so the same code path is exercised
by the task, by a management script and by the tests.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.logging import get_logger
from app.db.session import session_scope
from app.ingestion.theoretical import JOB_NAME, run_theoretical_ingestion
from app.models.instrument import Instrument
from app.models.market_data import LocalPriceBar
from app.workers.celery_app import celery_app

logger = get_logger(__name__)

TASK_NAME = JOB_NAME


@celery_app.task(  # type: ignore[misc]
    name=TASK_NAME,
    bind=True,
    max_retries=2,
    default_retry_delay=300,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_jitter=True,
)
def ingest_theoretical_prices(self: object, symbols: list[str] | None = None) -> dict[str, object]:
    """Compute theoretical CEDEAR prices for the configured window.

    Args:
        symbols: BYMA symbols to refresh. ``None`` means every active
            instrument, which is the daily-job default; a test or a one-off
            backfill passes an explicit subset.

    Returns:
        The :class:`~app.ingestion.theoretical.TheoreticalIngestionReport`
        as a plain dict, so it round-trips through the JSON result backend.

    The task computes the theoretical price for each instrument using the
    BYMA session close as the prediction instant. For a daily job, this is
    the most recent completed BYMA session.
    """
    settings = get_settings()

    logger.info(
        "ingestion.theoretical_task_started",
        task_id=getattr(self, "request", None),
        symbols=symbols,
    )

    with session_scope(settings) as session:
        # Determine the prediction instant (BYMA session close for the
        # most recent trading day).
        prediction_instant = _get_latest_byma_close(session)

        query = select(Instrument).order_by(Instrument.symbol)
        if symbols is not None:
            instruments = list(session.scalars(query.where(Instrument.symbol.in_(symbols))).all())
        else:
            instruments = list(session.scalars(query.where(Instrument.is_active.is_(True))).all())

        report = run_theoretical_ingestion(
            session,
            instruments,
            prediction_instant=prediction_instant,
        )

    return report.as_dict()


def _get_latest_byma_close(session: Session) -> datetime:
    """Return the timestamp of the most recent BYMA close in the data.

    This is used as the prediction instant for the daily theoretical
    price computation job.
    """
    latest = session.scalar(
        select(LocalPriceBar.timestamp).order_by(LocalPriceBar.timestamp.desc()).limit(1)
    )

    if latest is None:
        # Fallback: current time in Argentina
        from app.core.time import utc_now

        return utc_now()

    return latest


__all__ = ["TASK_NAME", "ingest_theoretical_prices"]
