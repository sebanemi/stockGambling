"""Feature-computation task.

Runs in the worker, never in the API process. Computes the full feature
snapshot for each instrument and persists it through
:mod:`app.features.store`, so the API only ever reads stored or freshly
computed-but-unpersisted values - it never invents them.
"""

from __future__ import annotations

from sqlalchemy import select

from app.core.config import get_settings
from app.core.logging import get_logger
from app.core.time import to_market_date, utc_now
from app.db.session import session_scope
from app.features.build import compute_all_features, feature_version
from app.features.store import write_feature_snapshot
from app.models.instrument import Instrument
from app.workers.celery_app import celery_app

logger = get_logger(__name__)

TASK_NAME = "feature.build"


@celery_app.task(  # type: ignore[misc]
    name=TASK_NAME,
    bind=True,
    max_retries=2,
    default_retry_delay=300,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_jitter=True,
)
def build_feature_snapshots(self: object, symbols: list[str] | None = None) -> dict[str, object]:
    """Compute and persist feature snapshots for the requested symbols.

    Args:
        symbols: BYMA symbols to refresh. ``None`` means every active
            instrument, which is the daily-job default.

    Returns:
        A plain-dict report (``symbols`` requested, ``written`` persisted,
        ``skipped`` without enough history, ``warnings`` per-symbol notes,
        ``feature_version`` and the ``as_of`` instant) so it round-trips
        through the JSON result backend.
    """
    settings = get_settings()
    as_of = utc_now()
    version = feature_version()
    market_date = to_market_date(as_of)

    logger.info(
        "feature.build_started",
        task_id=getattr(self, "request", None),
        symbols=symbols,
    )

    written = 0
    skipped = 0
    warnings: list[str] = []
    with session_scope(settings) as session:
        query = select(Instrument).order_by(Instrument.symbol)
        if symbols is not None:
            normalised = [s.strip().upper() for s in symbols]
            instruments = list(
                session.scalars(query.where(Instrument.symbol.in_(normalised))).all()
            )
            known = {i.symbol for i in instruments}
            for missing in sorted(set(normalised) - known):
                warnings.append(f"{missing}: instrument_not_found")
        else:
            instruments = list(session.scalars(query.where(Instrument.is_active.is_(True))).all())

        for instrument in instruments:
            try:
                values = compute_all_features(session, instrument.id, as_of)
                if all(v is None for v in values.values()):
                    skipped += 1
                    warnings.append(f"{instrument.symbol}: insufficient_history")
                    continue
                serialisable = {k: (float(v) if v is not None else None) for k, v in values.items()}
                write_feature_snapshot(
                    session,
                    instrument_id=instrument.id,
                    as_of=as_of,
                    feature_version=version,
                    market_date=market_date,
                    feature_values=serialisable,
                )
                written += 1
            except Exception as exc:  # noqa: BLE001 - per-symbol warnings, never fail batch
                skipped += 1
                warnings.append(f"{instrument.symbol}: {exc}")

    report: dict[str, object] = {
        "as_of": as_of.isoformat(),
        "market_date": market_date.isoformat(),
        "feature_version": version,
        "symbols": symbols,
        "written": written,
        "skipped": skipped,
        "warnings": warnings,
    }
    logger.info("feature.build_finished", written=written, skipped=skipped)
    return report


__all__ = ["TASK_NAME", "build_feature_snapshots"]
