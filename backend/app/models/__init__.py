"""ORM models.

Importing this package registers every model on ``Base.metadata``. Alembic's
``env.py`` imports it explicitly so that ``--autogenerate`` always sees the
full schema; never rely on a side effect from another module.
"""

from app.models.backtest import Backtest
from app.models.features import FeatureStore
from app.models.instrument import (
    IngestionRun,
    Instrument,
    InstrumentRatioHistory,
)
from app.models.market_data import FxRate, LocalPriceBar, UnderlyingPriceBar
from app.models.model import Model, ModelRun
from app.models.theoretical import TheoreticalPriceBar

__all__ = [
    "Backtest",
    "FeatureStore",
    "FxRate",
    "IngestionRun",
    "Instrument",
    "InstrumentRatioHistory",
    "LocalPriceBar",
    "Model",
    "ModelRun",
    "TheoreticalPriceBar",
    "UnderlyingPriceBar",
]
