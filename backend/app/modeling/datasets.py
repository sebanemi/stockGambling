"""Training-dataset construction from the feature store.

A dataset row pairs the stored feature snapshot for BYMA date ``D`` with the
CEDEAR direction ``H`` sessions ahead, where ``H`` is the forecast horizon
(see :mod:`app.modeling.horizons`):

* ``y = 1`` when the stored local close ``H`` sessions after ``D`` is
  strictly above the close on ``D`` (or the latest close on/before ``D``);
* ``y = 0`` otherwise (down or flat - flat counts as "not up").

Horizons count sessions, never calendar days. The last ``H`` snapshot dates
have no label and are dropped, so longer horizons yield fewer rows from the
same history.

The label uses future information *by construction*: labels exist so models
can learn, and the chronological discipline that keeps them honest lives in
the walk-forward splitter (Phase 7), which never lets a model train on a
label whose session it could not have known. This module only assembles the
matrix; it never splits it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.features.store import decode_feature_values, list_feature_snapshots
from app.modeling.horizons import HORIZONS, horizon_sessions
from app.models.market_data import LocalPriceBar


@dataclass(frozen=True, slots=True)
class FeatureDataset:
    """One instrument's aligned feature matrix and horizon labels."""

    instrument_id: int
    feature_version: str
    horizon: str
    feature_names: list[str]
    market_dates: list[date]
    X: pd.DataFrame
    y: pd.Series[int]


def _horizon_direction(
    closes: dict[date, float], snapshot_dates: list[date], horizon_sessions_ahead: int
) -> dict[date, int]:
    """Label each snapshot date by the close ``H`` stored sessions ahead.

    ``closes`` maps every stored local market date to its close. A snapshot
    date with fewer than ``H`` later sessions has no label and is dropped.
    """
    ordered = sorted(closes)
    labels: dict[date, int] = {}
    for current in snapshot_dates:
        later = [d for d in ordered if d > current]
        if len(later) < horizon_sessions_ahead:
            continue
        target = later[horizon_sessions_ahead - 1]
        base = closes.get(current)
        if base is None:
            earlier = [d for d in ordered if d <= current]
            if not earlier:
                continue
            base = closes[max(earlier)]
        labels[current] = 1 if closes[target] > base else 0
    return labels


def build_dataset(
    session: Session,
    instrument_id: int,
    feature_version: str,
    *,
    horizon: str = "1d",
    limit: int = 10000,
) -> FeatureDataset:
    """Assemble ``(X, y)`` for one instrument, feature version and horizon.

    Snapshots stream oldest-first so row order is chronological. Feature
    columns are the union of keys across snapshots (missing keys decode as
    ``NaN``); models impute from training medians at fit time.
    """
    horizon_sessions(horizon)
    snapshots = list(
        list_feature_snapshots(
            session,
            instrument_id=instrument_id,
            feature_version=feature_version,
            limit=limit,
            offset=0,
        )
    )
    snapshots.sort(key=lambda row: (row.market_date, row.as_of))
    if not snapshots:
        empty = pd.DataFrame()
        return FeatureDataset(
            instrument_id=instrument_id,
            feature_version=feature_version,
            horizon=horizon,
            feature_names=[],
            market_dates=[],
            X=empty,
            y=pd.Series(dtype="int64"),
        )

    rows: list[dict[str, float | int | None]] = []
    snapshot_dates: list[date] = []
    for snap in snapshots:
        values = decode_feature_values(snap)
        rows.append(
            {
                k: (float(v) if isinstance(v, bool | int | float) else None)
                for k, v in values.items()
            }
        )
        snapshot_dates.append(snap.market_date)

    frame = pd.DataFrame(rows, dtype="float64")
    feature_names = list(frame.columns)

    bars = session.scalars(
        select(LocalPriceBar).where(LocalPriceBar.instrument_id == instrument_id)
    ).all()
    closes = {bar.market_date: float(bar.close) for bar in bars if bar.close is not None}
    labels = _horizon_direction(closes, snapshot_dates, HORIZONS[horizon])

    keep = [d for d in snapshot_dates if d in labels]
    positions = [i for i, d in enumerate(snapshot_dates) if d in labels]
    aligned = frame.iloc[positions].reset_index(drop=True) if positions else frame.iloc[0:0]
    return FeatureDataset(
        instrument_id=instrument_id,
        feature_version=feature_version,
        horizon=horizon,
        feature_names=feature_names,
        market_dates=keep,
        X=aligned,
        y=pd.Series([labels[d] for d in keep], dtype="int64"),
    )


__all__ = ["FeatureDataset", "build_dataset"]
