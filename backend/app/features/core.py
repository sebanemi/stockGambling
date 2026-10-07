"""Core feature definitions and registry.

Every feature is defined once, with its inputs, lookback, and `as_of` timestamp.
No feature computes from future data.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence


@dataclass(frozen=True, slots=True)
class FeatureDefinition:
    """A single feature's contract."""

    name: str
    family: str  # cedear_technical | underlying_technical | fx | relative | market
    description: str
    inputs: Sequence[str] = field(default_factory=tuple)
    lookback_days: int = 1
    as_of_instant: datetime = field(init=False)  # filled by computation context


@dataclass(frozen=True, slots=True)
class FeatureResult:
    """One feature value for one instrument at one instant."""

    feature_name: str
    instrument_id: int | None  # None for market-level features
    market_date: date
    value: Decimal | float | int | None
    as_of: datetime


@dataclass(frozen=True, slots=True)
class FeatureSet:
    """A complete feature snapshot for one instrument at one `as_of`."""

    instrument_id: int
    as_of: datetime
    market_date: date
    features: dict[str, Decimal | float | int | None]


class FeatureRegistry:
    """Versioned registry of feature definitions."""

    def __init__(self, version: str = "1.0") -> None:
        """Initialise the feature registry."""
        self.version = version
        self.definitions: dict[str, FeatureDefinition] = {}

    def register(self, feature: FeatureDefinition) -> None:
        """Register a feature definition."""
        self.definitions[feature.name] = feature

    def get(self, name: str) -> FeatureDefinition | None:
        """Retrieve a feature definition by name."""
        return self.definitions.get(name)

    def list_family(self, family: str) -> list[FeatureDefinition]:
        """List all feature definitions for a family."""
        return [f for f in self.definitions.values() if f.family == family]


def get_feature_registry() -> FeatureRegistry:
    """The global feature registry instance."""
    from app.features.cedear_technical import register_cedear_features
    from app.features.fx_features import register_fx_features
    from app.features.market_features import register_market_features
    from app.features.relative_features import register_relative_features
    from app.features.underlying_technical import register_underlying_features

    registry = FeatureRegistry(version="1.0")
    register_cedear_features(registry)
    register_underlying_features(registry)
    register_fx_features(registry)
    register_relative_features(registry)
    register_market_features(registry)
    return registry
