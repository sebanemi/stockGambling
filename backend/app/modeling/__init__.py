"""Baseline prediction models for CEDEAR direction.

The package has three layers:

* :mod:`app.modeling.base` - the :class:`PredictionModel` interface every
  algorithm implements, so backtests and the API never branch on it.
* :mod:`app.modeling.baselines` - majority, random, logistic regression and
  XGBoost implementations with joblib serialisation.
* :mod:`app.modeling.datasets` - feature-matrix + next-session-label assembly
  from the feature store (labels are future by construction; chronological
  discipline lives in the Phase 7 splitter).
* :mod:`app.modeling.registry` - ``sg_models`` / ``sg_model_runs`` persistence.
"""

from __future__ import annotations

from app.modeling.base import PredictionModel
from app.modeling.baselines import (
    ALGORITHMS,
    LogisticRegressionModel,
    MajorityBaseline,
    RandomBaseline,
    XGBoostModel,
    make_model,
)
from app.modeling.datasets import FeatureDataset, build_dataset
from app.modeling.registry import get_model, list_models, record_run, register_model

__all__ = [
    "ALGORITHMS",
    "FeatureDataset",
    "LogisticRegressionModel",
    "MajorityBaseline",
    "PredictionModel",
    "RandomBaseline",
    "XGBoostModel",
    "build_dataset",
    "get_model",
    "list_models",
    "make_model",
    "record_run",
    "register_model",
]
