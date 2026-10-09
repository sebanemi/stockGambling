"""Real-data bootstrap: universe, prices, theoretical values, features.

Runs the same service code the worker tasks wrap, but as one supervised
pipeline for bringing a fresh database to a usable state:

1. metadata - CEDEAR universe from the configured providers (per-source
   failures become warnings; deactivation only considers sources that ran);
2. prices - local, underlying and FX bars over ``--years`` for ``--symbols``
   (intersected with the stored universe, missing tickers warned);
3. theoretical - one accounting-identity bar per instrument per day;
4. features - one stored snapshot per instrument per day (existing snapshots
   for the window are rebuilt, never duplicated).

Usage (inside the api image, which shares the database)::

    python -m app.bootstrap --symbols AAPL,MSFT,NVDA --years 6

Training is a separate step (:mod:`app.train`): data first, models after.
"""

from __future__ import annotations

import argparse
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select

from app.core.config import get_settings
from app.core.logging import get_logger
from app.core.time import to_market_date, utc_now
from app.db.session import session_scope
from app.features.build import compute_all_features, feature_version
from app.features.store import write_feature_snapshot
from app.ingestion.prices import run_market_ingestion
from app.ingestion.service import fetch_and_ingest
from app.ingestion.theoretical import refresh_instrument_theoretical
from app.models.features import FeatureStore
from app.models.instrument import Instrument
from app.providers.registry import (
    configured_cedear_providers,
    configured_fx_provider,
    configured_local_price_provider,
    configured_underlying_provider,
)

logger = get_logger(__name__)

#: Liquid, long-listed CEDEARs used when no explicit watchlist is given.
DEFAULT_WATCHLIST: list[str] = [
    "AAPL",
    "MSFT",
    "NVDA",
    "AMZN",
    "GOOGL",
    "META",
    "TSLA",
    "NFLX",
    "AMD",
    "KO",
]


def run_bootstrap(
    *,
    symbols: list[str] | None = None,
    years: float = 6.0,
    only: str | None = None,
) -> dict[str, object]:
    """Run the requested bootstrap stages and return a summary report."""
    settings = get_settings()
    end = to_market_date(utc_now())
    start = end - timedelta(days=int(years * 365.25))
    wanted = [(s.strip().upper()) for s in (symbols or DEFAULT_WATCHLIST) if s.strip()]
    stages = {"metadata", "prices", "theoretical", "features"}
    if only is not None:
        if only not in stages:
            raise ValueError(f"--only must be one of {sorted(stages)}, got {only!r}")
        stages = {only}
    report: dict[str, object] = {
        "symbols": wanted,
        "start": start.isoformat(),
        "end": end.isoformat(),
    }

    with session_scope(settings) as session:
        if "metadata" in stages:
            ingestion = fetch_and_ingest(
                session, configured_cedear_providers(settings), effective_date=end
            )
            report["metadata"] = ingestion.as_dict()

        instruments = list(
            session.scalars(
                select(Instrument).where(Instrument.symbol.in_(wanted)).order_by(Instrument.symbol)
            ).all()
        )
        known = {inst.symbol for inst in instruments}
        missing = sorted(set(wanted) - known)
        if missing:
            logger.warning("bootstrap.symbols_missing_from_universe", missing=missing)
        report["universe"] = sorted(known)
        report["missing"] = missing
        if not instruments:
            raise RuntimeError("None of the requested symbols is in the stored universe.")

        if "prices" in stages:
            price_report = run_market_ingestion(
                session,
                instruments,
                local_provider=configured_local_price_provider(settings),
                underlying_provider=configured_underlying_provider(settings),
                fx_provider=configured_fx_provider(settings),
                start=start,
                end=end,
                fx_pair=settings.fx_default_pair,
            )
            report["prices"] = price_report.as_dict()

        if "theoretical" in stages:
            made = 0
            warnings: list[str] = []
            for inst in instruments:
                day = start
                while day <= end:
                    tally = refresh_instrument_theoretical(
                        session,
                        inst,
                        prediction_instant=datetime(
                            day.year, day.month, day.day, 21, 0, tzinfo=UTC
                        ),
                    )
                    made += tally.inserted + tally.updated
                    warnings.extend(tally.warnings[:1])
                    day += timedelta(days=1)
            session.commit()
            report["theoretical"] = {"bars": made, "warnings": warnings[:10]}

        if "features" in stages:
            version = feature_version()
            session.execute(
                delete(FeatureStore).where(
                    FeatureStore.instrument_id.in_([inst.id for inst in instruments]),
                    FeatureStore.feature_version == version,
                    FeatureStore.market_date >= start,
                    FeatureStore.market_date <= end,
                )
            )
            session.commit()
            written = 0
            skipped = 0
            for inst in instruments:
                day = start
                while day <= end:
                    instant = datetime(day.year, day.month, day.day, 21, 0, tzinfo=UTC)
                    values = compute_all_features(session, inst.id, instant)
                    if all(v is None for v in values.values()):
                        skipped += 1
                    else:
                        write_feature_snapshot(
                            session,
                            instrument_id=inst.id,
                            as_of=instant,
                            feature_version=version,
                            market_date=day,
                            feature_values={
                                k: (float(v) if v is not None else None) for k, v in values.items()
                            },
                        )
                        written += 1
                    day += timedelta(days=1)
                session.commit()
                logger.info("bootstrap.features_symbol_done", symbol=inst.symbol, written=written)
            report["features"] = {
                "written": written,
                "skipped": skipped,
                "feature_version": version,
            }

    logger.info("bootstrap.finished", **{k: v for k, v in report.items() if k != "metadata"})
    return report


def main() -> None:
    """CLI entry point: ``python -m app.bootstrap ...``."""
    parser = argparse.ArgumentParser(description="Bootstrap real CEDEAR data.")
    parser.add_argument("--symbols", default=",".join(DEFAULT_WATCHLIST))
    parser.add_argument("--years", type=float, default=6.0)
    parser.add_argument(
        "--only", default=None, choices=["metadata", "prices", "theoretical", "features"]
    )
    args = parser.parse_args()
    run_bootstrap(symbols=args.symbols.split(","), years=args.years, only=args.only)


if __name__ == "__main__":
    main()


__all__ = ["DEFAULT_WATCHLIST", "main", "run_bootstrap"]
