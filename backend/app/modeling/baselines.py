"""Baseline models behind the :class:`PredictionModel` interface.

Four algorithms, weakest first:

* ``MajorityBaseline`` - always predicts the training majority class. The
  reference every real model must beat; a backtest that cannot beat it is a
  null result, not a strategy.
* ``RandomBaseline`` - predicts from the training class distribution with a
  seeded RNG. Deterministic given the seed.
* ``LogisticRegressionModel`` - L2 logistic regression on train-median-imputed
  features.
* ``XGBoostModel`` - gradient-boosted trees, single-threaded (``n_jobs=1``)
  so fits are reproducible run to run.

Missing values are imputed with the *training* medians stored at fit time;
no statistic ever crosses from evaluation data into the model.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import joblib
import numpy as np
from numpy.typing import NDArray

from app.modeling.base import PredictionModel


def _as_float_matrix(X: NDArray[np.float64] | Any) -> NDArray[np.float64]:
    """Coerce model input to a 2-D float matrix."""
    matrix = np.asarray(X, dtype=np.float64)
    if matrix.ndim == 1:
        matrix = matrix.reshape(1, -1)
    if matrix.ndim != 2:
        raise ValueError(f"Expected a 2-D feature matrix, got shape {matrix.shape}")
    return matrix


def _check_labels(y: NDArray[np.int64] | Any) -> NDArray[np.int64]:
    """Coerce labels to a 1-D binary vector."""
    labels = np.asarray(y).astype(np.int64).reshape(-1)
    if labels.size == 0:
        raise ValueError("Cannot fit on an empty label vector")
    if not set(np.unique(labels)).issubset({0, 1}):
        raise ValueError(f"Labels must be binary (0/1), got {np.unique(labels)}")
    return labels


class _ImputedModel(PredictionModel):
    """Shared train-median imputation for the learned models."""

    algorithm: str = "imputed"

    def __init__(self) -> None:
        """Initialise unfitted imputation state."""
        self.medians_: NDArray[np.float64] | None = None
        self.n_features_: int = 0

    def _store_medians(self, X: NDArray[np.float64]) -> NDArray[np.float64]:
        """Compute training medians (NaN-aware) and return the imputed matrix."""
        import warnings

        with warnings.catch_warnings():
            # An entirely-NaN column (e.g. a feature with no history yet) has
            # no median; that is expected, not worth a RuntimeWarning per fit.
            warnings.simplefilter("ignore", RuntimeWarning)
            medians = np.nanmedian(X, axis=0)
        # A column that is entirely NaN has no median: fall back to 0.0 rather
        # than dropping the column, so train and evaluation shapes always agree.
        medians = np.where(np.isnan(medians), 0.0, medians)
        self.medians_ = np.asarray(medians, dtype=np.float64)
        self.n_features_ = X.shape[1]
        return np.where(np.isnan(X), self.medians_, X)

    def _apply_medians(self, X: NDArray[np.float64]) -> NDArray[np.float64]:
        """Impute with the stored training medians."""
        if self.medians_ is None:
            raise RuntimeError(f"{self.algorithm} must be fitted before predicting")
        if X.shape[1] != self.n_features_:
            raise ValueError(f"Expected {self.n_features_} features, got {X.shape[1]}")
        return np.where(np.isnan(X), self.medians_, X)


class MajorityBaseline(PredictionModel):
    """Always predicts the training majority class (ties go to down/``0``)."""

    algorithm: str = "majority"

    def __init__(self) -> None:
        """Initialise an unfitted majority baseline."""
        self.majority_: int = 0
        self.proba_: NDArray[np.float64] = np.array([1.0, 0.0])

    def fit(self, X: NDArray[np.float64], y: NDArray[np.int64]) -> MajorityBaseline:
        """Record the majority class and its empirical distribution."""
        _as_float_matrix(X)
        labels = _check_labels(y)
        counts = np.bincount(labels, minlength=2).astype(np.float64)
        total = counts.sum()
        self.majority_ = int(np.argmax(counts))
        self.proba_ = counts / total if total > 0 else np.array([1.0, 0.0])
        return self

    def predict(self, X: NDArray[np.float64]) -> NDArray[np.int64]:
        """Repeat the majority class for every row."""
        n = _as_float_matrix(X).shape[0]
        return np.full(n, self.majority_, dtype=np.int64)

    def predict_proba(self, X: NDArray[np.float64]) -> NDArray[np.float64]:
        """Repeat the empirical training distribution for every row."""
        n = _as_float_matrix(X).shape[0]
        return np.tile(self.proba_, (n, 1))

    def save(self, path: str | Path) -> Path:
        """Serialise the fitted baseline."""
        target = Path(path)
        joblib.dump(
            {
                "algorithm": self.algorithm,
                "majority": self.majority_,
                "proba": [float(v) for v in self.proba_],
            },
            target,
        )
        return target

    @classmethod
    def load(cls, path: str | Path) -> MajorityBaseline:
        """Deserialise a baseline saved with :meth:`save`."""
        payload: dict[str, Any] = joblib.load(path)
        if payload.get("algorithm") != cls.algorithm:
            raise ValueError(f"Not a majority artifact: {path}")
        model = cls()
        model.majority_ = int(payload["majority"])
        model.proba_ = np.asarray(payload["proba"], dtype=np.float64)
        return model

    def get_metadata(self) -> dict[str, Any]:
        """Algorithm key and the fitted majority class."""
        return {"algorithm": self.algorithm, "majority_class": self.majority_}


class RandomBaseline(PredictionModel):
    """Predicts from the training class distribution with a seeded RNG."""

    algorithm: str = "random"

    def __init__(self, seed: int = 7) -> None:
        """Initialise with a fixed sampling seed (deterministic)."""
        self.seed = seed
        self.proba_: NDArray[np.float64] = np.array([0.5, 0.5])

    def fit(self, X: NDArray[np.float64], y: NDArray[np.int64]) -> RandomBaseline:
        """Record the empirical training distribution."""
        _as_float_matrix(X)
        labels = _check_labels(y)
        counts = np.bincount(labels, minlength=2).astype(np.float64)
        total = counts.sum()
        self.proba_ = counts / total if total > 0 else np.array([0.5, 0.5])
        return self

    def _rng(self) -> np.random.Generator:
        """A fresh generator per call, so batches are reproducible."""
        return np.random.default_rng(self.seed)

    def predict_proba(self, X: NDArray[np.float64]) -> NDArray[np.float64]:
        """Sample one-hot rows from the training distribution (seeded)."""
        n = _as_float_matrix(X).shape[0]
        draws = self._rng().choice(2, size=n, p=self.proba_)
        proba = np.zeros((n, 2), dtype=np.float64)
        proba[np.arange(n), draws] = 1.0
        return proba

    def predict(self, X: NDArray[np.float64]) -> NDArray[np.int64]:
        """Argmax of the sampled one-hot probabilities."""
        proba = self.predict_proba(X)
        labels: NDArray[np.int64] = proba.argmax(axis=1).astype(np.int64)
        return labels

    def save(self, path: str | Path) -> Path:
        """Serialise the fitted baseline."""
        target = Path(path)
        joblib.dump(
            {"algorithm": self.algorithm, "seed": self.seed, "proba": self.proba_},
            target,
        )
        return target

    @classmethod
    def load(cls, path: str | Path) -> RandomBaseline:
        """Deserialise a baseline saved with :meth:`save`."""
        payload: dict[str, Any] = joblib.load(path)
        if payload.get("algorithm") != cls.algorithm:
            raise ValueError(f"Not a random artifact: {path}")
        model = cls(seed=int(payload["seed"]))
        model.proba_ = np.asarray(payload["proba"], dtype=np.float64)
        return model

    def get_metadata(self) -> dict[str, Any]:
        """Algorithm key, seed and fitted distribution."""
        return {
            "algorithm": self.algorithm,
            "seed": self.seed,
            "class_distribution": [float(v) for v in self.proba_],
        }


class LogisticRegressionModel(_ImputedModel):
    """L2 logistic regression on median-imputed features."""

    algorithm: str = "logistic_regression"

    def __init__(self, max_iter: int = 1000) -> None:
        """Initialise with the solver budget."""
        super().__init__()
        self.max_iter = max_iter
        self._estimator: Any = None

    def fit(self, X: NDArray[np.float64], y: NDArray[np.int64]) -> LogisticRegressionModel:
        """Fit the regression on the imputed training matrix."""
        from sklearn.linear_model import LogisticRegression

        matrix = self._store_medians(_as_float_matrix(X))
        labels = _check_labels(y)
        if np.unique(labels).size < 2:
            raise ValueError(
                "Logistic regression needs both classes in training data; "
                "a single-class window cannot define a decision boundary."
            )
        self._estimator = LogisticRegression(max_iter=self.max_iter)
        self._estimator.fit(matrix, labels)
        return self

    def _estimator_or_raise(self) -> Any:
        """Return the fitted estimator or raise."""
        if self._estimator is None:
            raise RuntimeError("logistic_regression must be fitted before predicting")
        return self._estimator

    def predict(self, X: NDArray[np.float64]) -> NDArray[np.int64]:
        """Hard labels from the fitted regression."""
        matrix = self._apply_medians(_as_float_matrix(X))
        return np.asarray(self._estimator_or_raise().predict(matrix)).astype(np.int64)

    def predict_proba(self, X: NDArray[np.float64]) -> NDArray[np.float64]:
        """Two-column probabilities with columns ordered [down, up]."""
        from sklearn.exceptions import NotFittedError

        matrix = self._apply_medians(_as_float_matrix(X))
        try:
            raw_proba = self._estimator_or_raise().predict_proba(matrix)
        except NotFittedError as exc:
            raise RuntimeError("logistic_regression must be fitted first") from exc
        proba: NDArray[np.float64] = np.asarray(raw_proba, dtype=np.float64)
        return proba

    def save(self, path: str | Path) -> Path:
        """Serialise medians, hyperparameters and the sklearn estimator."""
        target = Path(path)
        joblib.dump(
            {
                "algorithm": self.algorithm,
                "max_iter": self.max_iter,
                "medians": self.medians_,
                "n_features": self.n_features_,
                "estimator": self._estimator,
            },
            target,
        )
        return target

    @classmethod
    def load(cls, path: str | Path) -> LogisticRegressionModel:
        """Deserialise a model saved with :meth:`save`."""
        payload: dict[str, Any] = joblib.load(path)
        if payload.get("algorithm") != cls.algorithm:
            raise ValueError(f"Not a logistic_regression artifact: {path}")
        model = cls(max_iter=int(payload["max_iter"]))
        model.medians_ = (
            np.asarray(payload["medians"], dtype=np.float64)
            if payload["medians"] is not None
            else None
        )
        model.n_features_ = int(payload["n_features"])
        model._estimator = payload["estimator"]
        return model

    def get_metadata(self) -> dict[str, Any]:
        """Algorithm key, hyperparameters and fitted feature count."""
        return {
            "algorithm": self.algorithm,
            "max_iter": self.max_iter,
            "n_features": self.n_features_,
            "fitted": self._estimator is not None,
        }


class XGBoostModel(_ImputedModel):
    """Gradient-boosted trees, single-threaded for reproducibility."""

    algorithm: str = "xgboost"

    def __init__(
        self, n_estimators: int = 100, max_depth: int = 3, learning_rate: float = 0.1
    ) -> None:
        """Initialise with booster hyperparameters."""
        super().__init__()
        self.n_estimators = n_estimators
        self.max_depth = max_depth
        self.learning_rate = learning_rate
        self._estimator: Any = None

    def fit(self, X: NDArray[np.float64], y: NDArray[np.int64]) -> XGBoostModel:
        """Fit the booster on the imputed training matrix."""
        from xgboost import XGBClassifier

        matrix = self._store_medians(_as_float_matrix(X))
        labels = _check_labels(y)
        if np.unique(labels).size < 2:
            raise ValueError(
                "XGBoost needs both classes in training data; "
                "a single-class window cannot define a decision boundary."
            )
        self._estimator = XGBClassifier(
            n_estimators=self.n_estimators,
            max_depth=self.max_depth,
            learning_rate=self.learning_rate,
            n_jobs=1,
            random_state=7,
            eval_metric="logloss",
        )
        self._estimator.fit(matrix, labels)
        return self

    def _estimator_or_raise(self) -> Any:
        """Return the fitted booster or raise."""
        if self._estimator is None:
            raise RuntimeError("xgboost must be fitted before predicting")
        return self._estimator

    def predict(self, X: NDArray[np.float64]) -> NDArray[np.int64]:
        """Hard labels from the fitted booster."""
        matrix = self._apply_medians(_as_float_matrix(X))
        return np.asarray(self._estimator_or_raise().predict(matrix)).astype(np.int64)

    def predict_proba(self, X: NDArray[np.float64]) -> NDArray[np.float64]:
        """Two-column probabilities with columns ordered [down, up]."""
        matrix = self._apply_medians(_as_float_matrix(X))
        proba = np.asarray(self._estimator_or_raise().predict_proba(matrix))
        if proba.ndim == 1:
            proba = np.column_stack([1.0 - proba, proba])
        return proba.astype(np.float64)

    def save(self, path: str | Path) -> Path:
        """Serialise medians, hyperparameters and the booster."""
        target = Path(path)
        joblib.dump(
            {
                "algorithm": self.algorithm,
                "n_estimators": self.n_estimators,
                "max_depth": self.max_depth,
                "learning_rate": self.learning_rate,
                "medians": self.medians_,
                "n_features": self.n_features_,
                "estimator": self._estimator,
            },
            target,
        )
        return target

    @classmethod
    def load(cls, path: str | Path) -> XGBoostModel:
        """Deserialise a model saved with :meth:`save`."""
        payload: dict[str, Any] = joblib.load(path)
        if payload.get("algorithm") != cls.algorithm:
            raise ValueError(f"Not an xgboost artifact: {path}")
        model = cls(
            n_estimators=int(payload["n_estimators"]),
            max_depth=int(payload["max_depth"]),
            learning_rate=float(payload["learning_rate"]),
        )
        model.medians_ = (
            np.asarray(payload["medians"], dtype=np.float64)
            if payload["medians"] is not None
            else None
        )
        model.n_features_ = int(payload["n_features"])
        model._estimator = payload["estimator"]
        return model

    def get_metadata(self) -> dict[str, Any]:
        """Algorithm key, hyperparameters and fitted feature count."""
        return {
            "algorithm": self.algorithm,
            "n_estimators": self.n_estimators,
            "max_depth": self.max_depth,
            "learning_rate": self.learning_rate,
            "n_features": self.n_features_,
            "fitted": self._estimator is not None,
        }


#: Every algorithm the platform can train, keyed by its registry name.
ALGORITHMS: dict[str, type[PredictionModel]] = {
    MajorityBaseline.algorithm: MajorityBaseline,
    RandomBaseline.algorithm: RandomBaseline,
    LogisticRegressionModel.algorithm: LogisticRegressionModel,
    XGBoostModel.algorithm: XGBoostModel,
}


def make_model(algorithm: str, **params: Any) -> PredictionModel:
    """Instantiate a registered algorithm by name.

    Raises:
        ValueError: When ``algorithm`` is not registered.
    """
    cls = ALGORITHMS.get(algorithm)
    if cls is None:
        raise ValueError(f"Unknown algorithm {algorithm!r}. Registered: {sorted(ALGORITHMS)}")
    return cls(**params)


__all__ = [
    "ALGORITHMS",
    "LogisticRegressionModel",
    "MajorityBaseline",
    "RandomBaseline",
    "XGBoostModel",
    "make_model",
]
