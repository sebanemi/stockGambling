"""Chronological splits and walk-forward folds.

The single rule this module exists to enforce: **time never shuffles**.
There is no ``shuffle`` parameter anywhere here on purpose - a shuffled
"split" of a time series is look-ahead leakage with a respectable name, and
the leakage suite (`tests/test_leakage.py::TestNoShuffledSplits`) fails the
build if shuffling is ever enabled under ``app/``.

Folds are expanding-window over integer positions: fold ``k`` trains on
``[0, train_end)`` and tests ``[train_end, train_end + test_size)``, with
``train_end`` advancing by ``step``. The fold count is driven by the actual
sample size, never by hardcoded years.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class WalkForwardFold:
    """One expanding-window fold as half-open position ranges."""

    #: Contiguous positions; always ``train_end <= test_start``.
    train_start: int
    train_end: int
    test_start: int
    test_end: int

    @property
    def train_size(self) -> int:
        """Number of training positions."""
        return self.train_end - self.train_start

    @property
    def test_size(self) -> int:
        """Number of test positions."""
        return self.test_end - self.test_start


def walk_forward_folds(
    n_samples: int,
    *,
    min_train: int,
    test_size: int,
    step: int,
) -> list[WalkForwardFold]:
    """Build expanding-window folds for ``n_samples`` chronological rows.

    Args:
        n_samples: Total rows in chronological order (oldest first).
        min_train: Training rows in the first fold; later folds grow by step.
        test_size: Test rows per fold.
        step: How far the train/test boundary advances per fold.

    Returns:
        Folds oldest-first. With ``step == test_size`` the test windows
        partition the tail contiguously (see :func:`verify_test_coverage`).

    Raises:
        ValueError: When the arguments cannot yield even one fold.
    """
    if min_train < 1:
        raise ValueError(f"min_train must be at least 1, got {min_train}")
    if test_size < 1:
        raise ValueError(f"test_size must be at least 1, got {test_size}")
    if step < 1:
        raise ValueError(f"step must be at least 1, got {step}")
    if n_samples < min_train + test_size:
        raise ValueError(
            f"Need at least min_train + test_size = {min_train + test_size} samples, "
            f"got {n_samples}"
        )
    folds: list[WalkForwardFold] = []
    train_end = min_train
    while train_end + test_size <= n_samples:
        folds.append(
            WalkForwardFold(
                train_start=0,
                train_end=train_end,
                test_start=train_end,
                test_end=train_end + test_size,
            )
        )
        train_end += step
    return folds


def verify_test_coverage(folds: list[WalkForwardFold], n_samples: int) -> None:
    """Assert the test windows evaluate the tail exactly once.

    Every test position belongs to exactly one fold (no overlap, no gaps
    between the first test start and ``n_samples``), so the test period is
    evaluated exactly once per model - the Phase 7 exit criterion.

    Raises:
        ValueError: When coverage is not exactly-once.
    """
    if not folds:
        raise ValueError("No folds to verify")
    covered: list[int] = []
    for fold in folds:
        if fold.train_end > fold.test_start:
            raise ValueError(f"Fold trains on its own test window: {fold}")
        covered.extend(range(fold.test_start, fold.test_end))
    if len(set(covered)) != len(covered):
        raise ValueError("Test windows overlap: a session is evaluated twice")
    if covered != list(range(folds[0].test_start, n_samples)):
        raise ValueError(
            "Test windows must cover the tail contiguously exactly once: "
            f"got {covered[0] if covered else None}..{covered[-1] if covered else None} "
            f"for {n_samples} samples"
        )


__all__ = ["WalkForwardFold", "verify_test_coverage", "walk_forward_folds"]
