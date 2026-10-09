"""Seed demo backtests from trained models (demo bootstrap step).

For each watchlist symbol with a registered artifact, predict per-epoch
signals over the most recent stored window and persist one backtest per
horizon through the same :func:`save_backtest` path the API uses. Model
outputs that refuse (missing artifact, width mismatch) become warnings;
a symbol with no usable model is skipped, never fabricated.

Usage (with the database up)::

    python -m app.demo_backtests --symbols AAPL,MSFT
"""

from __future__ import annotations

import argparse

import numpy as np
from sqlalchemy import select

from app.backtesting.store import save_backtest
from app.bootstrap import DEFAULT_WATCHLIST
from app.core.config import get_settings
from app.core.logging import get_logger
from app.db.session import session_scope
from app.features.store import decode_feature_values, list_feature_snapshots
from app.modeling.baselines import ALGORITHMS
from app.modeling.horizons import HORIZONS, horizon_sessions
from app.modeling.registry import get_model
from app.models.instrument import Instrument
from app.models.market_data import LocalPriceBar

logger = get_logger(__name__)

#: Recent window per horizon: epochs of history to simulate.
EPOCHS_PER_HORIZON: dict[str, int] = {
    "1d": 252,
    "1w": 52,
    "1m": 12,
    "3m": 8,
    "6m": 6,
    "1y": 3,
    "2y": 2,
}


def seed_symbol_backtests(symbol: str, *, horizons: list[str]) -> list[dict[str, object]]:
    """Persist one backtest per horizon for ``symbol`` from its artifacts."""
    settings = get_settings()
    outcomes: list[dict[str, object]] = []
    with session_scope(settings) as session:
        instrument = session.scalar(select(Instrument).where(Instrument.symbol == symbol))
        if instrument is None:
            return [{"symbol": symbol, "status": "skipped", "reason": "instrument_not_found"}]
        for horizon in horizons:
            outcome: dict[str, object] = {"symbol": symbol, "horizon": horizon}
            holding = horizon_sessions(horizon)
            epochs = EPOCHS_PER_HORIZON[horizon]
            row = get_model(session, f"{symbol}-xgboost", horizon)
            if row is None or not row.artifact_path:
                outcome.update(status="skipped", reason="no artifact")
                outcomes.append(outcome)
                continue
            snaps = list(
                list_feature_snapshots(
                    session, instrument_id=instrument.id, feature_version="1.0", limit=2000
                )
            )
            snaps.sort(key=lambda r: (r.market_date, r.as_of))
            by_date = {snap.market_date: snap for snap in snaps}
            bars = list(
                session.scalars(
                    select(LocalPriceBar)
                    .where(LocalPriceBar.instrument_id == instrument.id)
                    .order_by(LocalPriceBar.market_date.desc())
                    .limit(epochs * holding + 1)
                ).all()
            )
            bars.reverse()
            closes = [float(b.close) for b in bars if b.close is not None]
            if len(closes) != epochs * holding + 1:
                outcome.update(status="skipped", reason="gappy price window")
                outcomes.append(outcome)
                continue
            order = row.params.get("feature_names")
            if not isinstance(order, list) or not order:
                outcome.update(status="skipped", reason="no stored feature order")
                outcomes.append(outcome)
                continue
            try:
                model = ALGORITHMS[row.algorithm].load(row.artifact_path)
            except Exception as exc:  # noqa: BLE001 - unreadable artifact skips honestly
                outcome.update(status="skipped", reason=f"artifact unreadable: {exc}")
                outcomes.append(outcome)
                continue
            signals: list[int] = []
            for step in range(epochs):
                snap = by_date.get(bars[step * holding].market_date)
                if snap is None:
                    outcome.update(
                        status="skipped",
                        reason=f"no snapshot for {bars[step * holding].market_date}",
                    )
                    break
                values = decode_feature_values(snap)
                vector = np.array(
                    [
                        [
                            float(values[key])
                            if isinstance(values.get(key), int | float)
                            else np.nan
                            for key in order
                        ]
                    ],
                    dtype=np.float64,
                )
                try:
                    signals.append(int(model.predict(vector)[0]))
                except Exception as exc:  # noqa: BLE001 - unusable vector skips honestly
                    outcome.update(status="skipped", reason=f"predict refused: {exc}")
                    break
            else:
                saved = save_backtest(
                    session,
                    instrument_id=instrument.id,
                    start=bars[0].market_date,
                    end=bars[-1].market_date,
                    closes=closes,
                    signals=signals,
                    holding_period=holding,
                    notes=f"demo signals from {row.name} ({horizon})",
                )
                outcome.update(
                    status="succeeded",
                    backtest_id=saved.id,
                    total_return=saved.metrics.get("total_return"),
                )
                outcomes.append(outcome)
                continue
            outcomes.append(outcome)
    for outcome in outcomes:
        logger.info("demo_backtest.outcome", **outcome)
    return outcomes


def main() -> None:
    """CLI entry point: ``python -m app.demo_backtests ...``."""
    parser = argparse.ArgumentParser(description="Seed demo backtests from trained models.")
    parser.add_argument("--symbols", default=",".join(DEFAULT_WATCHLIST))
    parser.add_argument("--horizons", default=",".join(sorted(HORIZONS)))
    args = parser.parse_args()
    total = 0
    for symbol in [s.strip().upper() for s in args.symbols.split(",") if s.strip()]:
        outcomes = seed_symbol_backtests(symbol, horizons=args.horizons.split(","))
        total += sum(1 for o in outcomes if o["status"] == "succeeded")
    logger.info("demo_backtests.finished", backtests=total)


if __name__ == "__main__":
    main()


__all__ = ["EPOCHS_PER_HORIZON", "main", "seed_symbol_backtests"]
