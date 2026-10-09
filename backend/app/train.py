"""Train direction models per symbol and horizon on stored data.

For every ``(symbol, horizon)`` with enough labelled history:

1. build the horizon-labelled dataset from stored feature snapshots;
2. walk-forward evaluation with horizon-scaled geometry (the test window is
   touched exactly once, by the final evaluation);
3. fit the serving artifact on everything *before* the test window;
4. persist the artifact, register ``(name, horizon)`` and record the run
   with the test-window metrics.

Failures are honest: too little history skips with a warning, a
single-class training window records a ``failed`` run instead of inventing
a boundary. Feature column order is stored in the model params so serving
aligns the live vector exactly as trained.

Usage::

    python -m app.train --symbols AAPL,MSFT --algorithm xgboost
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
from sqlalchemy import select

from app.bootstrap import DEFAULT_WATCHLIST
from app.core.config import get_settings
from app.core.logging import get_logger
from app.db.session import session_scope
from app.evaluation.metrics import classification_metrics
from app.evaluation.walk_forward import run_walk_forward
from app.modeling.baselines import ALGORITHMS
from app.modeling.datasets import build_dataset
from app.modeling.horizons import HORIZONS, horizon_sessions
from app.modeling.registry import record_run, register_model
from app.models.instrument import Instrument

logger = get_logger(__name__)


def fold_geometry(horizon: str) -> tuple[int, int, int]:
    """Return ``(min_train, test_size, step)`` scaled to a horizon.

    Short horizons use seasonal defaults (252 train, 63 test); longer
    horizons scale up so every window holds several full horizons.
    """
    sessions = horizon_sessions(horizon)
    test_size = max(63, sessions)
    return max(252, 4 * sessions), test_size, test_size


def train_symbol_horizon(
    symbol: str,
    horizon: str,
    algorithm: str,
    artifact_dir: Path,
    feature_version: str = "1.0",
) -> dict[str, object]:
    """Train, evaluate once and register one ``(symbol, algorithm, horizon)``."""
    settings = get_settings()
    name = f"{symbol}-{algorithm}"
    outcome: dict[str, object] = {"symbol": symbol, "horizon": horizon, "model": name}
    with session_scope(settings) as session:
        instrument = session.scalar(select(Instrument).where(Instrument.symbol == symbol))
        if instrument is None:
            outcome["status"] = "skipped"
            outcome["reason"] = "instrument_not_found"
            return outcome
        dataset = build_dataset(
            session, instrument.id, feature_version, horizon=horizon, limit=20000
        )
        n = int(dataset.y.shape[0])
        min_train, test_size, step = fold_geometry(horizon)
        if n < min_train + test_size:
            outcome["status"] = "skipped"
            outcome["reason"] = (
                f"only {n} labelled rows, need {min_train + test_size} for {horizon}"
            )
            logger.warning("train.skipped", **outcome)
            return outcome

        matrix = dataset.X.to_numpy(dtype=np.float64)
        labels = dataset.y.to_numpy(dtype=np.int64)
        # The test window is always the most recent one (evaluated once).
        # The walk-forward aggregate is computed over the largest exactly
        # coverable prefix; any ragged tail belongs to the test window.
        test_start = n - test_size
        cover = min_train + ((test_start - min_train) // test_size) * test_size
        row = register_model(
            session,
            name=name,
            algorithm=algorithm,
            feature_version=feature_version,
            params={"feature_names": list(dataset.feature_names)},
            horizon=horizon,
        )
        factory = ALGORITHMS[algorithm]
        try:
            serving = factory().fit(matrix[0:test_start], labels[0:test_start])
        except ValueError as exc:
            record_run(
                session,
                model_id=row.id,
                train_start=dataset.market_dates[0],
                train_end=dataset.market_dates[test_start - 1],
                n_train=test_start,
                n_features=matrix.shape[1],
                metrics={},
                status="failed",
                notes=str(exc)[:500],
            )
            outcome["status"] = "failed"
            outcome["reason"] = str(exc)
            logger.warning("train.failed", **outcome)
            return outcome

        # A walk-forward fold that holds a single class cannot define a
        # boundary; the runner refuses rather than inventing one. Long
        # horizons hit this honestly (a 2y window in a bull run is all up),
        # so the aggregate degrades to unavailable while the test window -
        # evaluated once below - still counts.
        try:
            report = run_walk_forward(
                factory,
                matrix[0:cover],
                labels[0:cover],
                min_train=min_train,
                test_size=test_size,
                step=step,
            )
            aggregate_note = (
                f"walk-forward {len(report.folds)} folds, "
                f"mean bal-acc {report.aggregated.get('balanced_accuracy')}"
            )
            walkforward: dict[str, object] = {
                f"wf_{key}": value for key, value in report.aggregated.items()
            }
            walkforward["n_folds"] = len(report.folds)
        except ValueError as exc:
            aggregate_note = f"walk-forward aggregate unavailable: {exc}"
            walkforward = {}
        proba_up = serving.predict_proba(matrix[test_start:n])[:, 1]
        test_labels = labels[test_start:n]
        test_metrics = classification_metrics(test_labels, proba_up)
        # Class balance of the test window, reported not hidden: a 1.0 score
        # on a 100%-up window is the trend, not skill (methodology section 8).
        test_up_rate = float(np.mean(test_labels)) if test_labels.size else None

        artifact_dir.mkdir(parents=True, exist_ok=True)
        path = artifact_dir / f"{name}-{horizon}.joblib"
        serving.save(path)
        # POSIX form: rows trained on Windows serve from Linux containers.
        row.artifact_path = path.as_posix()
        session.commit()
        dates = dataset.market_dates
        record_run(
            session,
            model_id=row.id,
            train_start=dates[0],
            train_end=dates[test_start - 1],
            n_train=test_start,
            n_features=matrix.shape[1],
            metrics=dict(
                test_metrics,
                **walkforward,
                n_test=test_size,
                test_up_rate=test_up_rate,
                test_start=dates[test_start].isoformat(),
                test_end=dates[n - 1].isoformat(),
            ),
            notes=aggregate_note[:500],
        )
        outcome["status"] = "succeeded"
        outcome["artifact"] = str(path)
        outcome["test_balanced_accuracy"] = test_metrics["balanced_accuracy"]
        logger.info("train.succeeded", **{k: v for k, v in outcome.items() if k != "artifact"})
        return outcome


def run_training(
    *,
    symbols: list[str] | None = None,
    horizons: list[str] | None = None,
    algorithm: str = "xgboost",
    artifact_dir: str | None = None,
) -> list[dict[str, object]]:
    """Train every requested combination and return the outcomes."""
    settings = get_settings()
    if algorithm not in ALGORITHMS:
        raise ValueError(f"Unknown algorithm {algorithm!r}. Registered: {sorted(ALGORITHMS)}")
    wanted_horizons = horizons or sorted(HORIZONS)
    for horizon in wanted_horizons:
        horizon_sessions(horizon)
    directory = Path(artifact_dir or settings.artifact_dir)
    outcomes: list[dict[str, object]] = []
    for symbol in [s.strip().upper() for s in (symbols or DEFAULT_WATCHLIST) if s.strip()]:
        for horizon in wanted_horizons:
            outcomes.append(train_symbol_horizon(symbol, horizon, algorithm, directory))
    return outcomes


def main() -> None:
    """CLI entry point: ``python -m app.train ...``."""
    parser = argparse.ArgumentParser(description="Train horizon models on stored data.")
    parser.add_argument("--symbols", default=",".join(DEFAULT_WATCHLIST))
    parser.add_argument("--horizons", default=",".join(sorted(HORIZONS)))
    parser.add_argument("--algorithm", default="xgboost")
    parser.add_argument("--artifact-dir", default=None)
    args = parser.parse_args()
    outcomes = run_training(
        symbols=args.symbols.split(","),
        horizons=args.horizons.split(","),
        algorithm=args.algorithm,
        artifact_dir=args.artifact_dir,
    )
    succeeded = sum(1 for o in outcomes if o["status"] == "succeeded")
    logger.info("train.finished", succeeded=succeeded, total=len(outcomes))


if __name__ == "__main__":
    main()


__all__ = ["fold_geometry", "main", "run_training", "train_symbol_horizon"]
