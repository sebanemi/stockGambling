"""Feature API tests: live snapshot and stored-snapshot history.

Both endpoints are read projections: the live one computes from stored bars
without persisting, the history one pages what the ``feature.build`` worker
wrote. Unknown symbols 404; unknown history is an empty page, never a guess.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.features.store import write_feature_snapshot
from app.models.instrument import Instrument
from app.models.market_data import FxRate, LocalPriceBar, UnderlyingPriceBar

pytestmark = pytest.mark.integration

D = date(2026, 3, 10)


def _utc_noon(day: date) -> datetime:
    return datetime(day.year, day.month, day.day, 12, 0, tzinfo=UTC)


def _seed(session: Session, symbol: str = "FEAT") -> Instrument:
    inst = Instrument(symbol=symbol, instrument_type="STOCK")
    session.add(inst)
    session.flush()
    days = [date(2026, 3, d) for d in range(1, 10)]
    for i, day in enumerate(days):
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


class TestLiveSnapshot:
    """The live endpoint computes from stored bars without persisting."""

    def test_returns_full_vector_with_provenance(
        self, client: TestClient, db_session: Session
    ) -> None:
        """The snapshot carries every family plus as_of and version."""
        _seed(db_session)
        response = client.get(
            "/api/v1/cedears/FEAT/features", params={"as_of": "2026-03-10T21:00:00+00:00"}
        )
        assert response.status_code == 200
        body = response.json()
        assert body["symbol"] == "FEAT"
        assert body["feature_version"] == "1.0"
        assert body["market_date"] == "2026-03-10"
        assert "cedear_return_1d" in body["features"]
        assert "underlying_return_1d" in body["features"]
        assert "fx_return_1d" in body["features"]
        assert "premium_discount" in body["features"]
        # 1d local return over the last two eligible closes: 108 vs 107.
        assert body["features"]["cedear_return_1d"] == pytest.approx((108 - 107) / 107)

    def test_unknown_symbol_404(self, client: TestClient, db_session: Session) -> None:
        """An unknown ticker 404s instead of returning an empty vector."""
        response = client.get("/api/v1/cedears/NOPE/features")
        assert response.status_code == 404


class TestStoredSnapshots:
    """The history endpoint pages what the worker persisted."""

    def test_empty_history_is_empty_page(self, client: TestClient, db_session: Session) -> None:
        """No stored snapshots means total zero, not a 404."""
        _seed(db_session, symbol="FEAT2")
        response = client.get("/api/v1/cedears/FEAT2/features/snapshots")
        assert response.status_code == 200
        assert response.json()["total"] == 0

    def test_written_snapshot_is_listed(self, client: TestClient, db_session: Session) -> None:
        """A snapshot written by the worker appears in the history page."""
        inst = _seed(db_session, symbol="FEAT3")
        write_feature_snapshot(
            db_session,
            instrument_id=inst.id,
            as_of=datetime(2026, 3, 10, 21, 0, tzinfo=UTC),
            feature_version="1.0",
            market_date=D,
            feature_values={"cedear_return_1d": 0.02},
        )
        response = client.get("/api/v1/cedears/FEAT3/features/snapshots")
        assert response.status_code == 200
        body = response.json()
        assert body["total"] == 1
        assert body["items"][0]["features"] == {"cedear_return_1d": 0.02}
