"""Feature engineering for CEDEAR prediction.

This module computes all features used by the prediction models. Every feature
is a pure function of historical data available at its `as_of` instant.

Leakage prevention is structural:
- Features only read from already-persisted tables (local/underlying/FX/theoretical).
- Windows are defined by `market_date` (not UTC index position).
- The `as_of` parameter is the BYMA close instant for the prediction day.
- No feature uses data from the prediction day or later.
"""

from __future__ import annotations

from app.features.build import compute_all_features, feature_version
from app.features.cedear_technical import compute_cedear_technical_features
from app.features.core import (
    FeatureDefinition,
    FeatureRegistry,
    FeatureSet,
    get_feature_registry,
)
from app.features.fx_features import compute_fx_features
from app.features.market_data import MarketDataReader, get_market_data_reader
from app.features.market_features import compute_market_features
from app.features.relative_features import compute_relative_features
from app.features.store import (
    list_feature_snapshots,
    read_feature_snapshot,
    write_feature_snapshot,
)
from app.features.underlying_technical import compute_underlying_technical_features

__all__ = [
    "FeatureDefinition",
    "FeatureRegistry",
    "FeatureSet",
    "MarketDataReader",
    "compute_all_features",
    "compute_cedear_technical_features",
    "compute_fx_features",
    "compute_market_features",
    "compute_relative_features",
    "compute_underlying_technical_features",
    "feature_version",
    "get_feature_registry",
    "get_market_data_reader",
    "list_feature_snapshots",
    "read_feature_snapshot",
    "write_feature_snapshot",
]
