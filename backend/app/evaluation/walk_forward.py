"""Walk-forward runner: fit fresh, predict once, score honestly.

For each chronological fold the runner builds a **fresh** model instance via
``model_factory`` (reusing one instance across folds would carry fitted state
forward - a subtle leak), fits on the train window, predicts the untouched
test window, and scores it with :func:`classification_metrics` plus the
majority reference. Test coverage is verified exactly-once before returning.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import numpy as np
from numpy.typing import NDArray

from app.evaluation.metrics import (
    aggregate_folds,
    classification_metrics,
    majority_reference,
)
from app.evaluation.splits import WalkForwardFold, verify_test_coverage, walk_forward_folds
from app.modeling.base import PredictionModel


@dataclass(frozen=True, slots=True)
class FoldReport:
    """One fold's test metrics, with the majority null result alongside."""

    fold: WalkForwardFold
    metrics: dict[str, float | None]
    majority: dict[str, float | None]
    n_train: int
    n_test: int


@dataclass(frozen=True, slots=True)
class WalkForwardReport:
    """The full evaluation: per-fold reports plus cross-fold means."""

    folds: list[FoldReport]
    aggregated: dict[str, float | None]
    majority_aggregated: dict[str, float | None]
    test_positions: list[int] = field(default_factory=list)


def run_walk_forward(
    model_factory: Callable[[], PredictionModel],
    X: NDArray[np.float64] | Any,
    y: NDArray[np.int64] | Any,
    *,
    min_train: int,
    test_size: int,
    step: int,
) -> WalkForwardReport:
    """Evaluate a model factory walk-forward over ``(X, y)``.

    Args:
        model_factory: Zero-argument callable returning an **unfitted** model.
            Called once per fold so no fitted state crosses windows.
        X: Feature matrix in chronological order (oldest first).
        y: Binary labels aligned with ``X``.
        min_train: Training rows in the first fold.
        test_size: Test rows per fold.
        step: Boundary advance per fold.

    Returns:
        Per-fold metrics, cross-fold means, and the exactly-once test positions.
    """
    matrix = np.asarray(X, dtype=np.float64)
    labels = np.asarray(y).astype(np.int64).reshape(-1)
    if matrix.shape[0] != labels.shape[0]:
        raise ValueError(f"Shape mismatch: {matrix.shape} vs {labels.shape}")
    folds = walk_forward_folds(matrix.shape[0], min_train=min_train, test_size=test_size, step=step)
    verify_test_coverage(folds, matrix.shape[0])

    reports: list[FoldReport] = []
    for fold in folds:
        model = model_factory()
        model.fit(
            matrix[fold.train_start : fold.train_end], labels[fold.train_start : fold.train_end]
        )
        test_x = matrix[fold.test_start : fold.test_end]
        test_y = labels[fold.test_start : fold.test_end]
        proba_up = model.predict_proba(test_x)[:, 1]
        reports.append(
            FoldReport(
                fold=fold,
                metrics=classification_metrics(test_y, proba_up),
                majority=majority_reference(labels[fold.train_start : fold.train_end], test_y),
                n_train=fold.train_size,
                n_test=fold.test_size,
            )
        )
    test_positions = [i for fold in folds for i in range(fold.test_start, fold.test_end)]
    return WalkForwardReport(
        folds=reports,
        aggregated=aggregate_folds([r.metrics for r in reports]),
        majority_aggregated=aggregate_folds([r.majority for r in reports]),
        test_positions=test_positions,
    )


__all__ = ["FoldReport", "WalkForwardReport", "run_walk_forward"]
