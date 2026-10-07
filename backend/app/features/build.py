"""Single entry point for building a complete feature snapshot.

Merges the five feature families for one instrument at one prediction
instant. Used by the API (on-demand, read-only) and by the worker task
(which persists the result). The function itself never writes.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy.orm import Session

from app.features.cedear_technical import compute_cedear_technical_features
from app.features.core import FeatureRegistry, get_feature_registry
from app.features.fx_features import compute_fx_features
from app.features.market_features import compute_market_features
from app.features.registry import get_registered_version
from app.features.relative_features import compute_relative_features
from app.features.underlying_technical import compute_underlying_technical_features


def compute_all_features(
    session: Session,
    instrument_id: int,
    as_of: datetime,
    registry: FeatureRegistry | None = None,
) -> dict[str, Decimal | float | int | None]:
    """Compute every registered feature for one instrument at ``as_of``.

    Every family enforces the same as-of rule: nothing published after the
    prediction instant is visible (underlying series carry the one-session
    lag; see :mod:`app.alignment.asof`). Missing inputs yield ``None``,
    never a default or a nearest value.
    """
    active = registry or get_feature_registry()
    snapshot: dict[str, Decimal | float | int | None] = {}
    snapshot.update(compute_cedear_technical_features(active, instrument_id, as_of, session))
    snapshot.update(compute_underlying_technical_features(active, instrument_id, as_of, session))
    snapshot.update(compute_fx_features(active, instrument_id, as_of, session))
    snapshot.update(compute_relative_features(active, instrument_id, as_of, session))
    snapshot.update(compute_market_features(active, instrument_id, as_of, session))
    return snapshot


def feature_version() -> str:
    """The feature-set version stamped on snapshots and model runs."""
    return get_registered_version()


__all__ = ["compute_all_features", "feature_version"]
