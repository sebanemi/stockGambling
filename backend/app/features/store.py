"""Feature store writer and reader.

Persists computed feature sets and retrieves historical snapshots
for auditability and model reproducibility.
"""

from __future__ import annotations

import json
from datetime import date, datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.features import FeatureStore


def decode_feature_values(snapshot: FeatureStore) -> dict[str, Any]:
    """Decode a snapshot's JSON payload into a plain dict."""
    raw: Any = snapshot.feature_values_json
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return dict(raw)
    loaded: Any = json.loads(raw)
    return dict(loaded) if isinstance(loaded, dict) else {}


def write_feature_snapshot(
    session: Session,
    instrument_id: int,
    as_of: datetime,
    feature_version: str,
    market_date: date,
    feature_values: dict[str, float | int | None],
) -> FeatureStore:
    """Persist a feature snapshot for one instrument at one instant."""
    snapshot = FeatureStore(
        instrument_id=instrument_id,
        as_of=as_of,
        feature_version=feature_version,
        market_date=market_date,
        feature_values_json=json.dumps(feature_values) if feature_values is not None else None,
    )
    session.add(snapshot)
    session.commit()
    return snapshot


def write_feature_snapshots_bulk(
    session: Session,
    rows: Sequence[tuple[int, datetime, str, date, dict[str, float | int | None]]],
) -> list[FeatureStore]:
    """Persist many snapshots in one transaction.

    Each row is ``(instrument_id, as_of, feature_version, market_date,
    feature_values)``. A single commit keeps per-symbol failures cheap to
    retry without leaving a half-written batch behind.
    """
    snapshots = [
        FeatureStore(
            instrument_id=instrument_id,
            as_of=as_of,
            feature_version=version,
            market_date=market_date,
            feature_values_json=json.dumps(values) if values is not None else None,
        )
        for instrument_id, as_of, version, market_date, values in rows
    ]
    session.add_all(snapshots)
    session.commit()
    return snapshots


def read_feature_snapshot(
    session: Session,
    instrument_id: int,
    as_of: datetime,
    feature_version: str,
) -> FeatureStore | None:
    """Retrieve a feature snapshot by its unique key."""
    return session.scalar(
        select(FeatureStore).where(
            FeatureStore.instrument_id == instrument_id,
            FeatureStore.as_of == as_of,
            FeatureStore.feature_version == feature_version,
        )
    )


def list_feature_snapshots(
    session: Session,
    instrument_id: int | None = None,
    feature_version: str | None = None,
    market_date: date | None = None,
    start: date | None = None,
    end: date | None = None,
    limit: int = 100,
    offset: int = 0,
) -> Sequence[FeatureStore]:
    """List feature snapshots with optional filters and pagination."""
    stmt = select(FeatureStore)
    if instrument_id is not None:
        stmt = stmt.where(FeatureStore.instrument_id == instrument_id)
    if feature_version is not None:
        stmt = stmt.where(FeatureStore.feature_version == feature_version)
    if market_date is not None:
        stmt = stmt.where(FeatureStore.market_date == market_date)
    if start is not None:
        stmt = stmt.where(FeatureStore.market_date >= start)
    if end is not None:
        stmt = stmt.where(FeatureStore.market_date <= end)
    stmt = stmt.order_by(FeatureStore.as_of.desc()).limit(limit).offset(offset)
    return session.scalars(stmt).all()


def count_feature_snapshots(
    session: Session,
    instrument_id: int | None = None,
    feature_version: str | None = None,
    market_date: date | None = None,
    start: date | None = None,
    end: date | None = None,
) -> int:
    """Count snapshots matching the same filters as :func:`list_feature_snapshots`."""
    from sqlalchemy import func

    stmt = select(func.count()).select_from(FeatureStore)
    if instrument_id is not None:
        stmt = stmt.where(FeatureStore.instrument_id == instrument_id)
    if feature_version is not None:
        stmt = stmt.where(FeatureStore.feature_version == feature_version)
    if market_date is not None:
        stmt = stmt.where(FeatureStore.market_date == market_date)
    if start is not None:
        stmt = stmt.where(FeatureStore.market_date >= start)
    if end is not None:
        stmt = stmt.where(FeatureStore.market_date <= end)
    return session.scalar(stmt) or 0
