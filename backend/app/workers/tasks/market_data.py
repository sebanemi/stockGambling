"""Market-data ingestion tasks.

Runs in the worker, never in the API process, for the same reason as the
metadata task: a vendor that is slow must not delay a health check. The task
body is a thin shell around :mod:`app.ingestion.prices`, so the same code path
is exercised by the task, by a management script and by the tests.
"""

from __future__ import annotations

from sqlalchemy import select

from app.core.config import get_settings
from app.core.logging import get_logger
from app.db.session import session_scope
from app.ingestion.prices import JOB_NAME, backfill_window, run_market_ingestion
from app.models.instrument import Instrument
from app.providers.registry import (
    configured_fx_provider,
    configured_local_price_provider,
    configured_underlying_provider,
)
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
def ingest_cedear_prices(self: object, symbols: list[str] | None = None) -> dict[str, object]:
    """Refresh local, underlying and FX bars for the configured window.

    Args:
        symbols: BYMA symbols to refresh. ``None`` means every active
            instrument, which is the daily-job default; a test or a one-off
            backfill passes an explicit subset.

    Returns:
        The :class:`~app.ingestion.prices.PriceIngestionReport` as a plain
        dict, so it round-trips through the JSON result backend.

    Retries are configured on the task rather than inside the service, mirroring
    the metadata task: a network blip is retried by the queue, a *bad* payload
    is per-symbol and surfaces as a run warning that no retry fixes.
    """
    settings = get_settings()
    local_provider = configured_local_price_provider(settings)
    underlying_provider = configured_underlying_provider(settings)
    fx_provider = configured_fx_provider(settings)
    start, end = backfill_window(settings.market_data_backfill_days)

    logger.info(
        "ingestion.prices_task_started",
        task_id=getattr(self, "request", None),
        local_provider=local_provider.name,
        underlying_provider=underlying_provider.name,
        fx_provider=fx_provider.name,
        fx_pair=settings.fx_default_pair,
        start=start.isoformat(),
        end=end.isoformat(),
        symbols=symbols,
    )

    with session_scope(settings) as session:
        query = select(Instrument).order_by(Instrument.symbol)
        if symbols is not None:
            instruments = list(session.scalars(query.where(Instrument.symbol.in_(symbols))).all())
        else:
            instruments = list(session.scalars(query.where(Instrument.is_active.is_(True))).all())
        report = run_market_ingestion(
            session,
            instruments,
            local_provider=local_provider,
            underlying_provider=underlying_provider,
            fx_provider=fx_provider,
            start=start,
            end=end,
            fx_pair=settings.fx_default_pair,
        )

    return report.as_dict()


__all__ = ["TASK_NAME", "ingest_cedear_prices"]
