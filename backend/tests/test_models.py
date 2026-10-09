"""Phase 6 baseline-model tests.

Unit (no database): every algorithm honours the `PredictionModel` contract
on synthetic matrices, and a save/load round-trip reproduces predictions
exactly. Integration: the model registry persists and the dataset builder
aligns stored snapshots with next-session labels.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import numpy as np
import pytest
from numpy.typing import NDArray
from sqlalchemy.orm import Session

from app.features.store import write_feature_snapshot
from app.modeling.base import PredictionModel
from app.modeling.baselines import (
    ALGORITHMS,
    LogisticRegressionModel,
    MajorityBaseline,
    RandomBaseline,
    XGBoostModel,
    make_model,
)
from app.modeling.datasets import build_dataset
from app.modeling.registry import get_model, list_models, record_run, register_model
from app.models.instrument import Instrument
from app.models.market_data import LocalPriceBar

RNG = np.random.default_rng(42)


def _matrix(n: int = 60, p: int = 6) -> tuple[NDArray[np.float64], NDArray[np.int64]]:
    """A separable synthetic problem: sign of the first feature."""
    X = RNG.normal(size=(n, p))
    y = (X[:, 0] > 0).astype(np.int64)
    return X.astype(np.float64), y


def _utc_noon(day: date) -> datetime:
    """Midday UTC stamp for a market date."""
    return datetime(day.year, day.month, day.day, 12, 0, tzinfo=UTC)


@pytest.mark.unit
class TestAlgorithmRegistry:
    """The platform knows exactly four algorithms by stable names."""

    def test_registered_algorithms(self) -> None:
        """Majority, random, logistic regression and XGBoost are registered."""
        assert sorted(ALGORITHMS) == [
            "logistic_regression",
            "majority",
            "random",
            "xgboost",
        ]

    def test_make_model_unknown_raises(self) -> None:
        """An unregistered name is refused, never defaulted."""
        with pytest.raises(ValueError, match="Unknown algorithm"):
            make_model("neural_net")

    def test_make_model_builds_each_algorithm(self) -> None:
        """Every registered name instantiates its interface."""
        for name in ALGORITHMS:
            model = make_model(name)
            assert isinstance(model, PredictionModel)
            assert model.algorithm == name


@pytest.mark.unit
class TestModelContract:
    """Shapes, probability envelope and determinism per algorithm."""

    @pytest.mark.parametrize("algorithm", sorted(ALGORITHMS))
    def test_predict_shapes_and_proba_sums_to_one(self, algorithm: str) -> None:
        """Predictions align with rows; probability rows sum to 1."""
        X, y = _matrix()
        model = make_model(algorithm).fit(X, y)
        labels = model.predict(X)
        proba = model.predict_proba(X)
        assert labels.shape == (X.shape[0],)
        assert set(np.unique(labels)).issubset({0, 1})
        assert proba.shape == (X.shape[0], 2)
        assert np.allclose(proba.sum(axis=1), 1.0)

    @pytest.mark.parametrize("algorithm", ["majority", "logistic_regression", "xgboost"])
    def test_predict_matches_argmax_for_deterministic_models(self, algorithm: str) -> None:
        """Hard labels agree with the most probable class."""
        X, y = _matrix()
        model = make_model(algorithm).fit(X, y)
        assert np.array_equal(model.predict(X), model.predict_proba(X).argmax(axis=1))

    def test_majority_baseline_predicts_training_majority(self) -> None:
        """The baseline repeats the majority class of its training labels."""
        X = np.zeros((10, 3))
        model = MajorityBaseline().fit(X, np.array([0, 0, 0, 0, 0, 0, 0, 1, 1, 1]))
        assert np.all(model.predict(X) == 0)
        assert model.get_metadata()["majority_class"] == 0

    def test_random_baseline_is_seeded(self) -> None:
        """Two fits with the same seed draw the same predictions."""
        X, y = _matrix()
        first = RandomBaseline(seed=3).fit(X, y).predict(X)
        second = RandomBaseline(seed=3).fit(X, y).predict(X)
        assert np.array_equal(first, second)

    def test_predict_before_fit_raises(self) -> None:
        """Unfitted learned models refuse to predict."""
        X, _ = _matrix(n=4)
        with pytest.raises(RuntimeError, match="fitted"):
            LogisticRegressionModel().predict(X)
        with pytest.raises(RuntimeError, match="fitted"):
            XGBoostModel().predict_proba(X)

    def test_single_class_training_is_refused(self) -> None:
        """A one-class window cannot define a boundary; fit raises honestly."""
        X = RNG.normal(size=(20, 4))
        y = np.ones(20, dtype=np.int64)
        with pytest.raises(ValueError, match="both classes"):
            LogisticRegressionModel().fit(X, y)
        with pytest.raises(ValueError, match="both classes"):
            XGBoostModel().fit(X, y)

    def test_nan_features_are_imputed_not_dropped(self) -> None:
        """NaNs survive the round trip via training medians."""
        X, y = _matrix()
        X[::7, 0] = np.nan
        model = LogisticRegressionModel().fit(X, y)
        assert model.predict(X).shape == (X.shape[0],)


@pytest.mark.unit
class TestSerializationRoundTrip:
    """Save/load reproduces identical predictions from disk."""

    @pytest.mark.parametrize("algorithm", sorted(ALGORITHMS))
    def test_round_trip_reproduces_predictions(self, algorithm: str, tmp_path: Path) -> None:
        """A reloaded model predicts exactly like the fitted one."""
        X, y = _matrix()
        model = make_model(algorithm).fit(X, y)
        path = model.save(tmp_path / f"{algorithm}.joblib")
        assert path.exists()
        reloaded = type(model).load(path)
        assert np.array_equal(reloaded.predict(X), model.predict(X))
        assert np.allclose(reloaded.predict_proba(X), model.predict_proba(X))

    def test_load_rejects_foreign_artifact(self, tmp_path: Path) -> None:
        """Loading a majority artifact as XGBoost is refused."""
        X, y = _matrix(n=20)
        path = MajorityBaseline().fit(X, y).save(tmp_path / "majority.joblib")
        with pytest.raises(ValueError, match="Not an xgboost artifact"):
            XGBoostModel.load(path)

    def test_metadata_describes_fitted_state(self) -> None:
        """Metadata carries the algorithm key and feature count."""
        X, y = _matrix()
        assert make_model("xgboost").fit(X, y).get_metadata()["n_features"] == X.shape[1]


def _seed_dataset(session: Session, symbol: str = "MODEL1") -> tuple[Instrument, list[date]]:
    """Five snapshots with rising-then-flat closes for label checks."""
    inst = Instrument(symbol=symbol, instrument_type="STOCK")
    session.add(inst)
    session.flush()
    days = [date(2026, 4, d) for d in range(1, 6)]
    closes = ["100", "101", "102", "102", "103"]
    for day, close in zip(days, closes, strict=True):
        write_feature_snapshot(
            session,
            instrument_id=inst.id,
            as_of=datetime(day.year, day.month, day.day, 21, 0, tzinfo=UTC),
            feature_version="1.0",
            market_date=day,
            feature_values={"f1": float(close), "f2": 0.5},
        )
        session.add(
            LocalPriceBar(
                instrument_id=inst.id,
                symbol=symbol,
                market_date=day,
                timestamp=_utc_noon(day),
                open=Decimal(close),
                high=Decimal(close),
                low=Decimal(close),
                close=Decimal(close),
                volume=1000,
                currency="ARS",
                source="test",
            )
        )
    session.commit()
    return inst, days


@pytest.mark.integration
class TestDatasetBuilder:
    """Snapshots align with next-session direction labels."""

    def test_labels_follow_next_session(self, db_session: Session) -> None:
        """Up, up, flat(->0) and up; the dateless last snapshot is dropped."""
        inst, days = _seed_dataset(db_session)
        dataset = build_dataset(db_session, inst.id, "1.0")
        assert dataset.horizon == "1d"
        assert dataset.market_dates == days[:4]
        assert dataset.y.tolist() == [1, 1, 0, 1]
        assert dataset.X.shape == (4, 2)
        assert dataset.feature_names == ["f1", "f2"]

    def test_weekly_labels_look_five_sessions_ahead(self, db_session: Session) -> None:
        """A 1w dataset labels each date by the close five sessions later."""
        inst = Instrument(symbol="MODELW", instrument_type="STOCK")
        db_session.add(inst)
        db_session.flush()
        days = [date(2026, 5, d) for d in range(1, 9)]
        closes = ["100", "101", "102", "103", "104", "103", "102", "102"]
        for day, close in zip(days, closes, strict=True):
            write_feature_snapshot(
                session=db_session,
                instrument_id=inst.id,
                as_of=datetime(day.year, day.month, day.day, 21, 0, tzinfo=UTC),
                feature_version="1.0",
                market_date=day,
                feature_values={"f1": float(close)},
            )
            db_session.add(
                LocalPriceBar(
                    instrument_id=inst.id,
                    symbol="MODELW",
                    market_date=day,
                    timestamp=_utc_noon(day),
                    open=Decimal(close),
                    high=Decimal(close),
                    low=Decimal(close),
                    close=Decimal(close),
                    volume=1000,
                    currency="ARS",
                    source="test",
                )
            )
        db_session.commit()
        dataset = build_dataset(db_session, inst.id, "1.0", horizon="1w")
        assert dataset.horizon == "1w"
        # Days 1-3 see five sessions ahead (104, 103, 102 -> up, up, flat).
        assert dataset.market_dates == days[:3]
        assert dataset.y.tolist() == [1, 1, 0]

    def test_unknown_horizon_is_refused(self, db_session: Session) -> None:
        """A horizon outside the served set never builds a dataset."""
        inst, _ = _seed_dataset(db_session)
        with pytest.raises(ValueError, match="Unknown horizon"):
            build_dataset(db_session, inst.id, "1.0", horizon="2d")

    def test_empty_store_is_empty_dataset(self, db_session: Session) -> None:
        """No snapshots means an empty matrix, never an exception."""
        inst = Instrument(symbol="MODEL-EMPTY", instrument_type="STOCK")
        db_session.add(inst)
        db_session.flush()
        dataset = build_dataset(db_session, inst.id, "1.0")
        assert len(dataset.y) == 0
        assert dataset.X.empty

    def test_end_to_end_fit_on_stored_features(self, db_session: Session) -> None:
        """A baseline fits on the assembled matrix and scores rows."""
        inst, _ = _seed_dataset(db_session, symbol="MODEL2")
        dataset = build_dataset(db_session, inst.id, "1.0")
        X = dataset.X.to_numpy(dtype=np.float64)
        y = dataset.y.to_numpy(dtype=np.int64)
        model = MajorityBaseline().fit(X, y)
        assert model.predict(X).shape == (len(y),)
        assert model.predict_proba(X).shape == (len(y), 2)


@pytest.mark.integration
class TestModelRegistry:
    """Model rows and training runs persist and round-trip."""

    def test_register_get_and_list(self, db_session: Session) -> None:
        """Registration is idempotent per (name, horizon); listing filters apply."""
        first = register_model(
            db_session,
            name="m1",
            algorithm="majority",
            feature_version="1.0",
            params={},
        )
        again = register_model(
            db_session, name="m1", algorithm="majority", feature_version="1.0", params={}
        )
        assert first.id == again.id
        assert first.horizon == "1d"
        assert get_model(db_session, "m1") is not None
        assert get_model(db_session, "nope") is None
        register_model(db_session, name="x1", algorithm="xgboost", feature_version="1.0", params={})
        assert [(m.name, m.horizon) for m in list_models(db_session)] == [
            ("m1", "1d"),
            ("x1", "1d"),
        ]
        assert [m.name for m in list_models(db_session, algorithm="xgboost")] == ["x1"]

    def test_same_name_serves_many_horizons(self, db_session: Session) -> None:
        """One name with two horizons registers two independent rows."""
        one_d = register_model(
            db_session, name="multi", algorithm="majority", feature_version="1.0", params={}
        )
        one_w = register_model(
            db_session,
            name="multi",
            algorithm="majority",
            feature_version="1.0",
            params={},
            horizon="1w",
        )
        assert one_d.id != one_w.id
        assert get_model(db_session, "multi", "1d") is not None
        assert get_model(db_session, "multi", "1w") is not None
        assert get_model(db_session, "multi", "1m") is None
        assert [m.horizon for m in list_models(db_session, horizon="1w")] == ["1w"]
        with pytest.raises(ValueError, match="Unknown horizon"):
            register_model(
                db_session,
                name="bad",
                algorithm="majority",
                feature_version="1.0",
                params={},
                horizon="1y2",
            )

    def test_record_run(self, db_session: Session) -> None:
        """A training run stores its window, size and metrics."""
        model = register_model(
            db_session,
            name="m2",
            algorithm="logistic_regression",
            feature_version="1.0",
            params={"max_iter": 1000},
            artifact_path="artifacts/m2.joblib",
        )
        run = record_run(
            db_session,
            model_id=model.id,
            train_start=date(2026, 1, 1),
            train_end=date(2026, 3, 31),
            n_train=64,
            n_features=50,
            metrics={"accuracy": 0.55},
        )
        assert run.id is not None
        assert run.metrics == {"accuracy": 0.55}
        assert run.status == "succeeded"
        assert model.artifact_path == "artifacts/m2.joblib"
