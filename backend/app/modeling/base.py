"""Prediction-model interface.

Every algorithm - from the majority baseline to XGBoost - implements
:class:`PredictionModel`, so the backtesting engine (Phase 8) and the
prediction API (Phase 9) stay indifferent to the algorithm. A model consumes
rows of the feature store and emits next-session direction probabilities.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray


class PredictionModel(ABC):
    """The interface every predictive model implements."""

    #: Stable algorithm key, e.g. ``"majority"`` or ``"xgboost"``.
    algorithm: str = "abstract"

    @abstractmethod
    def fit(self, X: NDArray[np.float64], y: NDArray[np.int64]) -> PredictionModel:
        """Fit on a chronological training matrix. Returns ``self``."""

    @abstractmethod
    def predict(self, X: NDArray[np.float64]) -> NDArray[np.int64]:
        """Hard direction labels (``1`` = up, ``0`` = down)."""

    @abstractmethod
    def predict_proba(self, X: NDArray[np.float64]) -> NDArray[np.float64]:
        """Two-column probabilities ``[P(down), P(up)]``; rows sum to 1."""

    @abstractmethod
    def save(self, path: str | Path) -> Path:
        """Serialise the fitted state to ``path`` (joblib). Returns ``path``."""

    @classmethod
    @abstractmethod
    def load(cls, path: str | Path) -> PredictionModel:
        """Deserialise a model saved with :meth:`save`."""

    @abstractmethod
    def get_metadata(self) -> dict[str, Any]:
        """Algorithm, hyperparameters and fitted-state summary."""


__all__ = ["PredictionModel"]
