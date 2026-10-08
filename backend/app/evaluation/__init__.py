"""Walk-forward evaluation for CEDEAR direction models.

Three layers, one rule (time never shuffles):

* :mod:`app.evaluation.splits` - expanding-window folds driven by the actual
  sample size, plus the exactly-once test-coverage verifier.
* :mod:`app.evaluation.metrics` - accuracy, balanced accuracy, precision,
  recall, F1, ROC-AUC, log loss, Brier score, and the majority reference.
* :mod:`app.evaluation.walk_forward` - the runner: a fresh model per fold,
  predictions on untouched test windows, scored honestly.
"""

from __future__ import annotations

from app.evaluation.metrics import (
    aggregate_folds,
    classification_metrics,
    majority_reference,
)
from app.evaluation.splits import WalkForwardFold, verify_test_coverage, walk_forward_folds
from app.evaluation.walk_forward import FoldReport, WalkForwardReport, run_walk_forward

__all__ = [
    "FoldReport",
    "WalkForwardFold",
    "WalkForwardReport",
    "aggregate_folds",
    "classification_metrics",
    "majority_reference",
    "run_walk_forward",
    "verify_test_coverage",
    "walk_forward_folds",
]
