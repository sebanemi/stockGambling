"""Phase 9 prediction-API tests (migrated PostgreSQL schema).

Every endpoint is a read projection of stored rows - or, for backtests, a
simulation over stored bars that is persisted and then served verbatim:

* price histories page stored bars with their nulls intact;
* models/experiments page the registry audit trail;
* prediction serves probabilities from a registered artifact (never fitted
  at request time) with the two-column envelope summing to 1;
* backtests refuse inverted windows, short histories and misaligned signals
  instead of guessing.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.modeling.baselines import MajorityBaseline
from app.modeling.registry import record_run, register_model
from app.models.instrument import Instrument
from app.models.market_data import FxRate, LocalPriceBar, UnderlyingPriceBar

pytestmark = pytest.mark.integration

AS_OF = "2026-03-10T21:00:00+00:00"


def _utc_noon(day: date) -> datetime:
    return datetime(day.year, day.month, day.day, 12, 0, tzinfo=UTC)


def _seed(session: Session, symbol: str = "API9") -> Instrument:
    """Nine rising sessions of local, underlying and FX bars, committed."""
    inst = Instrument(symbol=symbol, instrument_type="STOCK")
    session.add(inst)
    session.flush()
    for i, day in enumerate([date(2026, 3, d) for d in range(1, 10)]):
        close = Decimal(100 + i)
        session.add(
            LocalPriceBar(
                instrument_id=inst.id,
                symbol=symbol,
                market_date=day,
                timestamp=_utc_noon(day),
                open=close,
                high=close + 1,
                low=close - 1,
                close=close,
                volume=1000,
                currency="ARS",
                source="test",
            )
        )
        session.add(
            UnderlyingPriceBar(
                instrument_id=inst.id,
                symbol=f"{symbol}-U",
                market_date=day,
                timestamp=_utc_noon(day),
                open=close,
                high=close + 1,
                low=close - 1,
                close=close,
                volume=1000,
                currency="USD",
                source="test",
            )
        )
        session.add(
            FxRate(
                pair="USDARS",
                market_date=day,
                timestamp=_utc_noon(day),
                close=Decimal(1000 + i),
                currency="ARS",
                source="test",
            )
        )
    session.commit()
    return inst


class TestPriceHistories:
    """History, underlying and FX endpoints page stored bars."""

    def test_local_history_pages_newest_first(
        self, client: TestClient, db_session: Session
    ) -> None:
        """Bars come back newest first with totals ignoring pagination."""
        _seed(db_session)
        body = client.get("/api/v1/cedears/API9/history").json()
        assert body["total"] == 9
        assert body["items"][0]["market_date"] == "2026-03-09"
        assert body["items"][0]["close"] == "108.000000"
        assert body["items"][0]["currency"] == "ARS"

    def test_history_range_filter(self, client: TestClient, db_session: Session) -> None:
        """Inclusive start/end filters narrow the window."""
        _seed(db_session)
        body = client.get(
            "/api/v1/cedears/API9/history",
            params={"start": "2026-03-02", "end": "2026-03-03", "limit": 100},
        ).json()
        assert body["total"] == 2
        assert [item["market_date"] for item in body["items"]] == [
            "2026-03-03",
            "2026-03-02",
        ]

    def test_underlying_keeps_own_market(self, client: TestClient, db_session: Session) -> None:
        """Underlying bars carry their own ticker and currency."""
        _seed(db_session)
        body = client.get("/api/v1/cedears/API9/underlying").json()
        assert body["total"] == 9
        assert body["items"][0]["symbol"] == "API9-U"
        assert body["items"][0]["currency"] == "USD"

    def test_fx_series_is_global(self, client: TestClient, db_session: Session) -> None:
        """FX returns the reference pair; an unknown pair is an empty page."""
        _seed(db_session)
        body = client.get("/api/v1/cedears/API9/fx").json()
        assert body["total"] == 9
        assert body["items"][0]["pair"] == "USDARS"
        assert body["items"][0]["close"] == "1008.000000"
        empty = client.get("/api/v1/cedears/API9/fx", params={"pair": "EURARS"}).json()
        assert empty["total"] == 0
        assert empty["items"] == []

    def test_empty_history_is_empty_page(self, client: TestClient, db_session: Session) -> None:
        """Stored nothing means total zero, never a 404."""
        db_session.add(Instrument(symbol="BARE", instrument_type="STOCK"))
        db_session.commit()
        for route in ("history", "underlying"):
            body = client.get(f"/api/v1/cedears/BARE/{route}").json()
            assert body["total"] == 0
            assert body["items"] == []

    def test_unknown_symbol_404(self, client: TestClient, db_session: Session) -> None:
        """All three routes agree on the unknown-ticker shape."""
        for route in ("history", "underlying", "fx"):
            response = client.get(f"/api/v1/cedears/NOPE/{route}")
            assert response.status_code == 404
            assert response.json()["detail"]["error"] == "instrument_not_found"


class TestModels:
    """The registry endpoints list and detail stored configurations."""

    def test_list_and_detail_with_runs(self, client: TestClient, db_session: Session) -> None:
        """Registration plus a run round-trips through list and detail."""
        model = register_model(
            db_session,
            name="api9-majority",
            algorithm="majority",
            feature_version="1.0",
            params={},
        )
        record_run(
            db_session,
            model_id=model.id,
            train_start=date(2026, 1, 1),
            train_end=date(2026, 3, 31),
            n_train=64,
            n_features=50,
            metrics={"accuracy": 0.55},
        )
        listing = client.get("/api/v1/models").json()
        assert listing["total"] == 1
        assert listing["items"][0]["name"] == "api9-majority"
        detail = client.get(f"/api/v1/models/{model.id}").json()
        assert detail["algorithm"] == "majority"
        assert len(detail["runs"]) == 1
        assert detail["runs"][0]["metrics"] == {"accuracy": 0.55}
        assert detail["runs"][0]["train_start"] == "2026-01-01"

    def test_algorithm_filter(self, client: TestClient, db_session: Session) -> None:
        """Listing filters by algorithm key."""
        register_model(
            db_session, name="m-maj", algorithm="majority", feature_version="1.0", params={}
        )
        register_model(
            db_session, name="m-xgb", algorithm="xgboost", feature_version="1.0", params={}
        )
        body = client.get("/api/v1/models", params={"algorithm": "xgboost"}).json()
        assert body["total"] == 1
        assert body["items"][0]["name"] == "m-xgb"

    def test_unknown_model_404(self, client: TestClient, db_session: Session) -> None:
        """An unregistered id 404s with the shared error shape."""
        response = client.get("/api/v1/models/999999")
        assert response.status_code == 404
        assert response.json()["detail"]["error"] == "model_not_found"


def _register_majority(
    db_session: Session,
    tmp_path: Path,
    *,
    name: str,
    artifact: bool = True,
    horizon: str = "1d",
) -> None:
    """Fit a majority baseline on a balanced toy problem and register it."""
    rng = np.random.default_rng(9)
    features = rng.normal(size=(20, 4))
    labels = np.array([0, 1] * 10, dtype=np.int64)
    fitted = MajorityBaseline().fit(features, labels)
    path: str | None = None
    if artifact:
        path = str(fitted.save(tmp_path / f"{name}.joblib"))
    register_model(
        db_session,
        name=name,
        algorithm="majority",
        feature_version="1.0",
        params={},
        horizon=horizon,
        artifact_path=path,
    )


class TestPrediction:
    """Probabilities come from the registered artifact, never from thin air."""

    def test_probabilities_sum_to_one_with_provenance(
        self, client: TestClient, db_session: Session, tmp_path: Path
    ) -> None:
        """The envelope holds and every field traces to stored data."""
        _seed(db_session)
        _register_majority(db_session, tmp_path, name="pred-ok")
        body = client.get(
            "/api/v1/cedears/API9/prediction",
            params={"model": "pred-ok", "as_of": AS_OF},
        ).json()
        assert body["symbol"] == "API9"
        assert body["horizon"] == "1d"
        assert body["market_date"] == "2026-03-10"
        assert body["model"]["name"] == "pred-ok"
        assert body["model"]["algorithm"] == "majority"
        assert body["feature_version"] == "1.0"
        assert body["feature_version_match"] is True
        assert body["probability_up"] + body["probability_down"] == pytest.approx(1.0)
        assert body["probability_up"] == pytest.approx(0.5)
        # Latest stored close on or before the market date: 2026-03-09 -> 108.
        assert body["actual_close"] == "108.000000"
        assert body["current_ratio"] is None

    def test_unknown_model_404(
        self, client: TestClient, db_session: Session, tmp_path: Path
    ) -> None:
        """An unregistered model name 404s instead of predicting."""
        _seed(db_session)
        response = client.get("/api/v1/cedears/API9/prediction", params={"model": "ghost"})
        assert response.status_code == 404
        assert response.json()["detail"]["error"] == "model_not_found"

    def test_missing_model_param_404(self, client: TestClient, db_session: Session) -> None:
        """No model parameter means no prediction is attempted."""
        _seed(db_session)
        response = client.get("/api/v1/cedears/API9/prediction")
        assert response.status_code == 404

    def test_untrained_model_409(
        self, client: TestClient, db_session: Session, tmp_path: Path
    ) -> None:
        """A registered model without an artifact is refused, not defaulted."""
        _seed(db_session)
        _register_majority(db_session, tmp_path, name="pred-bare", artifact=False)
        response = client.get("/api/v1/cedears/API9/prediction", params={"model": "pred-bare"})
        assert response.status_code == 409
        assert response.json()["detail"]["error"] == "model_not_trained"

    def test_malformed_as_of_422(
        self, client: TestClient, db_session: Session, tmp_path: Path
    ) -> None:
        """A naive instant is a 422, never silently assumed UTC."""
        _seed(db_session)
        _register_majority(db_session, tmp_path, name="pred-tz")
        response = client.get(
            "/api/v1/cedears/API9/prediction",
            params={"model": "pred-tz", "as_of": "2026-03-10T12:00:00"},
        )
        assert response.status_code == 422
        assert response.json()["detail"]["error"] == "invalid_as_of"

    def test_unknown_symbol_404(
        self, client: TestClient, db_session: Session, tmp_path: Path
    ) -> None:
        """Prediction for an unknown ticker 404s on the instrument first."""
        _register_majority(db_session, tmp_path, name="pred-sym")
        response = client.get("/api/v1/cedears/NOPE/prediction", params={"model": "pred-sym"})
        assert response.status_code == 404
        assert response.json()["detail"]["error"] == "instrument_not_found"

    def test_unknown_horizon_422(
        self, client: TestClient, db_session: Session, tmp_path: Path
    ) -> None:
        """A horizon outside the served set is refused before any model loads."""
        _seed(db_session)
        _register_majority(db_session, tmp_path, name="pred-h")
        response = client.get(
            "/api/v1/cedears/API9/prediction",
            params={"model": "pred-h", "horizon": "2d"},
        )
        assert response.status_code == 422
        assert response.json()["detail"]["error"] == "unknown_horizon"

    def test_wrong_horizon_404(
        self, client: TestClient, db_session: Session, tmp_path: Path
    ) -> None:
        """A 1d model never answers a 1w question."""
        _seed(db_session)
        _register_majority(db_session, tmp_path, name="pred-1d")
        response = client.get(
            "/api/v1/cedears/API9/prediction",
            params={"model": "pred-1d", "horizon": "1w"},
        )
        assert response.status_code == 404
        assert response.json()["detail"]["error"] == "model_not_found"

    def test_horizon_provenance(
        self, client: TestClient, db_session: Session, tmp_path: Path
    ) -> None:
        """The response echoes the served horizon and its meaning."""
        _seed(db_session)
        _register_majority(db_session, tmp_path, name="pred-w", horizon="1w")
        body = client.get(
            "/api/v1/cedears/API9/prediction",
            params={"model": "pred-w", "horizon": "1w", "as_of": AS_OF},
        ).json()
        assert body["horizon"] == "1w"
        assert "5 sessions" in body["horizon_detail"]
        assert body["model"]["horizon"] == "1w"
        assert body["probability_up"] + body["probability_down"] == pytest.approx(1.0)


class TestBacktests:
    """Simulations run over stored bars and persist verbatim."""

    def test_create_and_fetch_round_trip(self, client: TestClient, db_session: Session) -> None:
        """POST persists; GET returns the identical stored run."""
        _seed(db_session)
        created = client.post(
            "/api/v1/backtests",
            json={
                "symbol": "API9",
                "start": "2026-03-01",
                "end": "2026-03-09",
                "signals": [1] * 8,
            },
        )
        assert created.status_code == 201
        body = created.json()
        assert body["n_bars"] == 9
        assert len(body["equity_curve"]) == 9
        assert len(body["signals"]) == 8
        assert body["metrics"]["trade_count"] == pytest.approx(0.0)
        # Always-long matches buy-and-hold on the same window.
        assert body["metrics"]["total_return"] == pytest.approx(
            body["benchmark_metrics"]["total_return"]
        )
        fetched = client.get(f"/api/v1/backtests/{body['id']}").json()
        assert fetched == body
        listing = client.get("/api/v1/backtests").json()
        assert listing["total"] == 1
        assert listing["items"][0]["id"] == body["id"]

    def test_epoch_backtest_201(self, client: TestClient, db_session: Session) -> None:
        """Two-session epochs need one signal per two intervals."""
        _seed(db_session)
        created = client.post(
            "/api/v1/backtests",
            json={
                "symbol": "API9",
                "start": "2026-03-01",
                "end": "2026-03-09",
                "signals": [1, 1, 0, 0],
                "holding_period": 2,
            },
        )
        assert created.status_code == 201
        body = created.json()
        assert body["holding_period"] == 2
        assert len(body["equity_curve"]) == 9

    def test_signal_length_mismatch_422(self, client: TestClient, db_session: Session) -> None:
        """Eight bars need exactly seven signals."""
        _seed(db_session)
        response = client.post(
            "/api/v1/backtests",
            json={
                "symbol": "API9",
                "start": "2026-03-01",
                "end": "2026-03-09",
                "signals": [1, 0],
            },
        )
        assert response.status_code == 422
        assert response.json()["detail"]["error"] == "signal_length_mismatch"

    def test_inverted_window_422(self, client: TestClient, db_session: Session) -> None:
        """A window ending before it starts is refused."""
        _seed(db_session)
        response = client.post(
            "/api/v1/backtests",
            json={
                "symbol": "API9",
                "start": "2026-03-09",
                "end": "2026-03-01",
                "signals": [1],
            },
        )
        assert response.status_code == 422
        assert response.json()["detail"]["error"] == "invalid_window"

    def test_short_history_422(self, client: TestClient, db_session: Session) -> None:
        """A one-bar window holds no price interval to simulate."""
        _seed(db_session)
        response = client.post(
            "/api/v1/backtests",
            json={
                "symbol": "API9",
                "start": "2026-03-01",
                "end": "2026-03-01",
                "signals": [],
            },
        )
        assert response.status_code == 422
        assert response.json()["detail"]["error"] == "insufficient_history"

    def test_unknown_symbol_404(self, client: TestClient, db_session: Session) -> None:
        """Backtesting an unknown ticker 404s."""
        response = client.post(
            "/api/v1/backtests",
            json={
                "symbol": "NOPE",
                "start": "2026-03-01",
                "end": "2026-03-09",
                "signals": [1] * 8,
            },
        )
        assert response.status_code == 404

    def test_unknown_backtest_404(self, client: TestClient, db_session: Session) -> None:
        """An unpersisted run id 404s."""
        response = client.get("/api/v1/backtests/999999")
        assert response.status_code == 404
        assert response.json()["detail"]["error"] == "backtest_not_found"


class TestExperiments:
    """Experiment records page the training-run audit trail."""

    def test_lists_runs_with_model_identity(self, client: TestClient, db_session: Session) -> None:
        """Each record carries model, window and metrics from stored rows."""
        model = register_model(
            db_session,
            name="api9-exp",
            algorithm="logistic_regression",
            feature_version="1.0",
            params={"max_iter": 1000},
        )
        record_run(
            db_session,
            model_id=model.id,
            train_start=date(2026, 1, 1),
            train_end=date(2026, 6, 30),
            n_train=120,
            n_features=50,
            metrics={"balanced_accuracy": 0.53},
        )
        body = client.get("/api/v1/experiments").json()
        assert body["total"] == 1
        item = body["items"][0]
        assert item["model_name"] == "api9-exp"
        assert item["algorithm"] == "logistic_regression"
        assert item["metrics"] == {"balanced_accuracy": 0.53}
        filtered = client.get("/api/v1/experiments", params={"model_name": "nope"}).json()
        assert filtered["total"] == 0
