"""Phase 7 walk-forward evaluation tests (no database).

Splits stay chronological and cover the tail exactly once; metrics match
hand-computed values and degrade honestly on degenerate windows; the runner
fits a fresh model per fold and carries the majority reference.
"""

from __future__ import annotations

import numpy as np
import pytest

from app.evaluation.metrics import (
    aggregate_folds,
    classification_metrics,
    majority_reference,
)
from app.evaluation.splits import verify_test_coverage, walk_forward_folds
from app.evaluation.walk_forward import run_walk_forward
from app.modeling.baselines import MajorityBaseline

RNG = np.random.default_rng(11)


@pytest.mark.unit
class TestWalkForwardFolds:
    """Folds are chronological, expanding and driven by the sample size."""

    def test_folds_expand_and_never_overlap_test(self) -> None:
        """Train windows grow; each test starts where train ends."""
        folds = walk_forward_folds(10, min_train=4, test_size=2, step=2)
        assert [(f.train_start, f.train_end, f.test_start, f.test_end) for f in folds] == [
            (0, 4, 4, 6),
            (0, 6, 6, 8),
            (0, 8, 8, 10),
        ]
        for fold in folds:
            assert fold.train_end <= fold.test_start

    def test_fold_count_comes_from_data_not_years(self) -> None:
        """More samples with the same parameters means more folds."""
        few = walk_forward_folds(10, min_train=4, test_size=2, step=2)
        many = walk_forward_folds(20, min_train=4, test_size=2, step=2)
        assert len(many) > len(few)

    def test_step_smaller_than_test_size_overlaps_and_fails_coverage(self) -> None:
        """Overlapping test windows are built but rejected by the verifier."""
        folds = walk_forward_folds(10, min_train=4, test_size=4, step=2)
        with pytest.raises(ValueError, match="overlap"):
            verify_test_coverage(folds, 10)

    def test_too_little_data_raises(self) -> None:
        """A dataset shorter than train + test refuses to fold."""
        with pytest.raises(ValueError, match="at least"):
            walk_forward_folds(5, min_train=4, test_size=2, step=1)

    def test_non_positive_parameters_raise(self) -> None:
        """Zero or negative geometry is refused."""
        with pytest.raises(ValueError, match="min_train"):
            walk_forward_folds(10, min_train=0, test_size=2, step=1)
        with pytest.raises(ValueError, match="test_size"):
            walk_forward_folds(10, min_train=4, test_size=0, step=1)
        with pytest.raises(ValueError, match="step"):
            walk_forward_folds(10, min_train=4, test_size=2, step=0)


@pytest.mark.unit
class TestTestCoverage:
    """The test period is evaluated exactly once per model."""

    def test_contiguous_tail_passes(self) -> None:
        """Partitioned tail coverage verifies cleanly."""
        folds = walk_forward_folds(12, min_train=4, test_size=2, step=2)
        verify_test_coverage(folds, 12)

    def test_empty_folds_raise(self) -> None:
        """Nothing to verify means nothing evaluated."""
        with pytest.raises(ValueError, match="No folds"):
            verify_test_coverage([], 12)

    def test_train_on_test_window_raises(self) -> None:
        """A fold whose train covers its test is leakage, not evaluation."""
        from app.evaluation.splits import WalkForwardFold

        with pytest.raises(ValueError, match="own test window"):
            verify_test_coverage([WalkForwardFold(0, 6, 4, 6)], 10)


@pytest.mark.unit
class TestClassificationMetrics:
    """Metric values match hand computation; degenerate windows degrade."""

    def test_perfect_predictions(self) -> None:
        """A perfect window scores 1.0 everywhere it is defined."""
        metrics = classification_metrics(np.array([0, 0, 1, 1]), np.array([0.1, 0.2, 0.8, 0.9]))
        assert metrics["accuracy"] == pytest.approx(1.0)
        assert metrics["balanced_accuracy"] == pytest.approx(1.0)
        assert metrics["precision"] == pytest.approx(1.0)
        assert metrics["recall"] == pytest.approx(1.0)
        assert metrics["f1"] == pytest.approx(1.0)
        assert metrics["roc_auc"] == pytest.approx(1.0)
        assert metrics["brier_score"] == pytest.approx(0.025)

    def test_all_wrong_predictions(self) -> None:
        """An inverted window scores 0 accuracy with high log loss."""
        metrics = classification_metrics(np.array([0, 0, 1, 1]), np.array([0.9, 0.8, 0.2, 0.1]))
        assert metrics["accuracy"] == pytest.approx(0.0)
        assert metrics["roc_auc"] == pytest.approx(0.0)
        assert metrics["log_loss"] is not None and metrics["log_loss"] > 1.0

    def test_single_class_window_has_no_auc(self) -> None:
        """ROC-AUC is undefined (None) on a single-class window."""
        metrics = classification_metrics(np.array([1, 1, 1]), np.array([0.9, 0.8, 0.7]))
        assert metrics["roc_auc"] is None
        assert metrics["accuracy"] == pytest.approx(1.0)

    def test_shape_mismatch_and_empty_raise(self) -> None:
        """Mismatched or empty inputs are refused."""
        with pytest.raises(ValueError, match="Shape mismatch"):
            classification_metrics(np.array([0, 1]), np.array([0.5]))
        with pytest.raises(ValueError, match="empty"):
            classification_metrics(np.array([]), np.array([]))

    def test_majority_reference_uses_training_majority(self) -> None:
        """The null result predicts the training majority on the test window."""
        reference = majority_reference(np.array([0, 0, 0, 1]), np.array([0, 1, 1]))
        assert reference["accuracy"] == pytest.approx(1 / 3)

    def test_aggregate_skips_undefined(self) -> None:
        """Means skip None entries; all-None aggregates to None."""
        aggregated = aggregate_folds(
            [
                {"accuracy": 0.5, "roc_auc": None},
                {"accuracy": 0.7, "roc_auc": 0.8},
            ]
        )
        assert aggregated["accuracy"] == pytest.approx(0.6)
        assert aggregated["roc_auc"] == pytest.approx(0.8)
        with pytest.raises(ValueError, match="No fold metrics"):
            aggregate_folds([])


@pytest.mark.unit
class TestWalkForwardRunner:
    """Fresh models per fold, untouched test windows, majority attached."""

    def test_runner_evaluates_tail_exactly_once(self) -> None:
        """Test positions partition the tail with no repeats."""
        X = RNG.normal(size=(20, 3))
        y = (X[:, 0] > 0).astype(np.int64)
        report = run_walk_forward(MajorityBaseline, X, y, min_train=8, test_size=4, step=4)
        assert report.test_positions == list(range(8, 20))
        assert len(report.folds) == 3
        assert report.aggregated["accuracy"] is not None
        assert report.majority_aggregated["accuracy"] is not None

    def test_runner_builds_a_fresh_model_per_fold(self) -> None:
        """Three folds mean three factory calls - no state crosses windows."""
        calls = 0

        def factory() -> MajorityBaseline:
            nonlocal calls
            calls += 1
            return MajorityBaseline()

        X = RNG.normal(size=(14, 2))
        y = np.array([0, 1] * 7)
        run_walk_forward(factory, X, y, min_train=6, test_size=2, step=2)
        assert calls == 4

    def test_runner_shape_mismatch_raises(self) -> None:
        """Unaligned matrices are refused before any fitting."""
        with pytest.raises(ValueError, match="Shape mismatch"):
            run_walk_forward(
                MajorityBaseline,
                np.zeros((10, 2)),
                np.zeros(9, dtype=np.int64),
                min_train=4,
                test_size=2,
                step=2,
            )

    def test_each_fold_carries_its_majority(self) -> None:
        """Every fold report holds both the model and the null result."""
        X = RNG.normal(size=(12, 2))
        y = np.array([0, 0, 0, 1, 1, 1, 0, 0, 1, 1, 0, 1])
        report = run_walk_forward(MajorityBaseline, X, y, min_train=4, test_size=2, step=2)
        for fold_report in report.folds:
            assert fold_report.n_train >= 4
            assert fold_report.n_test == 2
            assert "accuracy" in fold_report.metrics
            assert "accuracy" in fold_report.majority
