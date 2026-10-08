"""Classification metrics for walk-forward evaluation.

Every function takes the test labels and the model's ``P(up)`` column and
returns plain floats (or ``None`` where the metric is undefined, e.g. ROC-AUC
on a single-class window - never a fabricated number). Degenerate windows
use ``zero_division=0`` so precision/recall/F1 degrade to 0.0 instead of
raising.

``majority_reference`` runs the majority baseline on the same split, so every
model report carries the null result it must beat.
"""

from __future__ import annotations

from typing import Any

import numpy as np
from numpy.typing import NDArray


def _safe_roc_auc(y_true: NDArray[np.int64], proba_up: NDArray[np.float64]) -> float | None:
    """ROC-AUC, or ``None`` when the window holds a single class."""
    from sklearn.metrics import roc_auc_score

    if np.unique(y_true).size < 2:
        return None
    return float(roc_auc_score(y_true, proba_up))


def _safe_log_loss(y_true: NDArray[np.int64], proba_up: NDArray[np.float64]) -> float:
    """Clipped log loss over the two-column envelope."""
    from sklearn.metrics import log_loss

    eps = 1e-15
    clipped = np.clip(proba_up, eps, 1.0 - eps)
    envelope = np.column_stack([1.0 - clipped, clipped])
    return float(log_loss(y_true, envelope, labels=[0, 1]))


def classification_metrics(
    y_true: NDArray[np.int64] | Any,
    proba_up: NDArray[np.float64] | Any,
) -> dict[str, float | None]:
    """Score one test window.

    Args:
        y_true: Binary labels (``1`` = up).
        proba_up: Model's ``P(up)`` per row.

    Returns:
        ``accuracy``, ``balanced_accuracy``, ``precision``, ``recall``,
        ``f1``, ``roc_auc`` (``None`` on single-class windows),
        ``log_loss`` and ``brier_score``.
    """
    from sklearn.metrics import (
        accuracy_score,
        balanced_accuracy_score,
        f1_score,
        precision_score,
        recall_score,
    )

    labels = np.asarray(y_true).astype(np.int64).reshape(-1)
    proba = np.asarray(proba_up, dtype=np.float64).reshape(-1)
    if labels.shape != proba.shape:
        raise ValueError(f"Shape mismatch: {labels.shape} vs {proba.shape}")
    if labels.size == 0:
        raise ValueError("Cannot score an empty window")
    hard = (proba >= 0.5).astype(np.int64)
    return {
        "accuracy": float(accuracy_score(labels, hard)),
        "balanced_accuracy": float(balanced_accuracy_score(labels, hard)),
        "precision": float(precision_score(labels, hard, zero_division=0)),
        "recall": float(recall_score(labels, hard, zero_division=0)),
        "f1": float(f1_score(labels, hard, zero_division=0)),
        "roc_auc": _safe_roc_auc(labels, proba),
        "log_loss": _safe_log_loss(labels, proba),
        "brier_score": float(np.mean((proba - labels) ** 2)),
    }


def majority_reference(
    y_train: NDArray[np.int64] | Any, y_test: NDArray[np.int64] | Any
) -> dict[str, float | None]:
    """Score the majority baseline on the same train/test split."""
    from app.modeling.baselines import MajorityBaseline

    train = np.asarray(y_train).astype(np.int64).reshape(-1)
    test = np.asarray(y_test).astype(np.int64).reshape(-1)
    n = test.shape[0]
    if n == 0:
        raise ValueError("Cannot score an empty test window")
    if train.size == 0:
        raise ValueError("Cannot fit the majority reference on empty training labels")
    model = MajorityBaseline().fit(np.zeros((train.size, 1)), train)
    proba_up = model.predict_proba(np.zeros((n, 1)))[:, 1]
    return classification_metrics(test, proba_up)


def aggregate_folds(fold_metrics: list[dict[str, float | None]]) -> dict[str, float | None]:
    """Mean each metric across folds, skipping ``None`` (undefined) entries."""
    if not fold_metrics:
        raise ValueError("No fold metrics to aggregate")
    aggregated: dict[str, float | None] = {}
    for key in fold_metrics[0]:
        values: list[float] = []
        for metrics in fold_metrics:
            value = metrics[key]
            if value is not None:
                values.append(value)
        aggregated[key] = float(np.mean(values)) if values else None
    return aggregated


__all__ = ["aggregate_folds", "classification_metrics", "majority_reference"]
