"""CEDEAR metadata API tests.

These exercise the read contract against a migrated PostgreSQL schema: what is
listed, what is filtered, how the ratio is rendered, and - the subtle parts -
what ``as_of`` means, how an inactive instrument surfaces, and how an unknown
symbol differs from an unknown ratio.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.vocabulary import InstrumentType, ProgramStatus
from app.ingestion.service import JOB_NAME, run_metadata_ingestion
from app.models.instrument import IngestionRun, Instrument
from app.providers.base import CedearRecord, CedearSnapshot

pytestmark = pytest.mark.integration

DAY = date(2026, 3, 2)
LATER = date(2026, 3, 10)


def fetched_at() -> datetime:
    """A stable fetch timestamp for the test snapshots."""
    return datetime.now(UTC)


def record(
    symbol: str, *, ratio: Decimal | None = Decimal(10), **overrides: object
) -> CedearRecord:
    """Build a stock-like record, overridden per test."""
    fields: dict[str, object] = {
        "symbol": symbol,
        "custodian": "comafi",
        "name": f"{symbol} Incorporated",
        "instrument_type": InstrumentType.STOCK,
        "underlying_symbol": f"{symbol}-U",
        "underlying_market_raw": "NYSE",
        "ratio": ratio,
        "program_status": ProgramStatus.ACTIVE,
        "source_ref": f"https://example.test/{symbol}",
        "isin": "US0378331005",
    }
    fields.update(overrides)
    return CedearRecord(**fields)  # type: ignore[arg-type]


def universe(symbols: list[str], *, ratio: Decimal | None = Decimal(10)) -> CedearSnapshot:
    """A snapshot carrying the requested instruments, as a provider would return it."""
    return CedearSnapshot(
        provider="comafi",
        source_url="https://example.test/comafi",
        fetched_at=fetched_at(),
        records=tuple(record(symbol, ratio=ratio) for symbol in symbols),
    )


def load_universe(session: Session, symbols: list[str] | None = None) -> None:
    """Ingest a handful of instruments and commit, so the app connection sees them."""
    picked = symbols or ["AAPL", "MSFT", "NVDA"]
    run_metadata_ingestion(
        session,
        [universe(picked)],
        effective_date=DAY,
        expected_providers=["comafi"],
    )
    session.commit()


def add_instrument(session: Session, single: CedearRecord, *, day: date = LATER) -> None:
    """Ingest and commit one extra instrument (or a changed one)."""
    run_metadata_ingestion(
        session,
        [single_snapshot(single)],
        effective_date=day,
        expected_providers=["comafi"],
    )
    session.commit()


def single_snapshot(single: CedearRecord) -> CedearSnapshot:
    """A one-record snapshot for an incremental ingest."""
    return CedearSnapshot(
        provider="comafi",
        source_url="https://example.test/comafi",
        fetched_at=fetched_at(),
        records=(single,),
    )


class TestList:
    """The universe endpoint lists what ingestion stored, with filters."""

    def test_all_stored_instruments_are_listed(
        self, client: TestClient, db_session: Session
    ) -> None:
        """The stored universe is the response universe; there is no hardcoded list."""
        load_universe(db_session, ["AAPL", "MSFT"])
        response = client.get("/api/v1/cedears")
        assert response.status_code == 200
        body = response.json()
        assert [item["symbol"] for item in body["items"]] == ["AAPL", "MSFT"]
        assert body["total"] == 2

    def test_the_response_is_paginated(self, client: TestClient, db_session: Session) -> None:
        """A consumer must be able to page a universe of thousands."""
        load_universe(db_session, ["AAA", "BBB", "CCC"])
        response = client.get("/api/v1/cedears", params={"limit": 2, "offset": 1})
        body = response.json()
        assert [item["symbol"] for item in body["items"]] == ["BBB", "CCC"]
        assert body["total"] == 3
        assert body["limit"] == 2
        assert body["offset"] == 1

    def test_unknown_symbols_are_absent(self, client: TestClient, db_session: Session) -> None:
        """Nothing is invented if it was never ingested."""
        load_universe(db_session, ["AAPL"])
        response = client.get("/api/v1/cedears", params={"q": "ZZZZ"})
        assert response.json()["items"] == []

    def test_search_matches_symbol_prefix_chars(
        self, client: TestClient, db_session: Session
    ) -> None:
        """Query is case-insensitive and matches symbol, name and underlying."""
        load_universe(db_session, ["AAPL", "MSFT", "NVDA"])
        by_symbol = client.get("/api/v1/cedears", params={"q": "aapl"}).json()["items"]
        assert [item["symbol"] for item in by_symbol] == ["AAPL"]
        by_name = client.get("/api/v1/cedears", params={"q": "incorp"}).json()["items"]
        assert {item["symbol"] for item in by_name} == {"AAPL", "MSFT", "NVDA"}
        by_underlying = client.get("/api/v1/cedears", params={"q": "nvda-u"}).json()["items"]
        assert [item["symbol"] for item in by_underlying] == ["NVDA"]

    def test_inactive_instruments_are_hidden_by_default(
        self, client: TestClient, db_session: Session
    ) -> None:
        """The default list is the tradable universe, not a graveyard."""
        load_universe(db_session)
        instrument = db_session.scalar(select(Instrument).where(Instrument.symbol == "MSFT"))
        assert instrument is not None
        instrument.is_active = False
        db_session.commit()
        response = client.get("/api/v1/cedears")
        symbols = [item["symbol"] for item in response.json()["items"]]
        assert "MSFT" not in symbols

    def test_include_inactive_adds_them_back(self, client: TestClient, db_session: Session) -> None:
        """Delisted programs stay addressable for research."""
        load_universe(db_session)
        instrument = db_session.scalar(select(Instrument).where(Instrument.symbol == "MSFT"))
        assert instrument is not None
        instrument.is_active = False
        db_session.commit()
        response = client.get("/api/v1/cedears", params={"include_inactive": "true"})
        assert "MSFT" in [item["symbol"] for item in response.json()["items"]]

    def test_type_filter(self, client: TestClient, db_session: Session) -> None:
        """ETF CEDEARs are a different valuation surface and are filterable."""
        load_universe(db_session)
        etf = record("SPY", instrument_type=InstrumentType.ETF, underlying_market_raw="NYSE ARCA")
        add_instrument(db_session, etf)
        response = client.get("/api/v1/cedears", params={"instrument_type": "ETF"})
        assert [item["symbol"] for item in response.json()["items"]] == ["SPY"]

    def test_market_filter_is_case_insensitive(
        self, client: TestClient, db_session: Session
    ) -> None:
        """The filter accepts the canonical code in any case."""
        load_universe(db_session, ["AAPL"])
        response = client.get("/api/v1/cedears", params={"underlying_market": "nyse"})
        assert [item["symbol"] for item in response.json()["items"]] == ["AAPL"]

    def test_the_summary_carries_the_mappings(
        self, client: TestClient, db_session: Session
    ) -> None:
        """Everything a list consumer needs to route or price must be present."""
        load_universe(db_session, ["AAPL"])
        item = client.get("/api/v1/cedears").json()["items"][0]
        assert item["underlying_symbol"] == "AAPL-U"
        assert item["underlying_market"] == "NYSE"
        assert item["isin"] is not None
        assert item["instrument_type"] == "STOCK"
        assert item["program_status"] == "ACTIVE"
        assert item["is_active"] is True


class TestRatioIncluded:
    """The list endpoint reports the ratio in force on ``as_of``."""

    def test_the_current_ratio_is_included(self, client: TestClient, db_session: Session) -> None:
        """A list consumer must not need one request per row to price."""
        load_universe(db_session, ["AAPL"])
        item = client.get("/api/v1/cedears").json()["items"][0]
        assert item["current_ratio"] == "10.0000000000"
        assert item["current_ratio_formatted"] == "10:1"
        assert item["ratio_effective_from"] == DAY.isoformat()
        assert item["ratio_effective_to"] is None

    def test_as_of_returns_a_historical_ratio(
        self, client: TestClient, db_session: Session
    ) -> None:
        """A back-test on a past date must get the past ratio, not the current one."""
        load_universe(db_session, ["AAPL"])
        add_instrument(db_session, record("AAPL", ratio=Decimal(30)))
        before = client.get("/api/v1/cedears", params={"as_of": DAY.isoformat()}).json()["items"][0]
        after = client.get("/api/v1/cedears", params={"as_of": LATER.isoformat()}).json()["items"][
            0
        ]
        assert before["current_ratio"] == "10.0000000000"
        assert after["current_ratio"] == "30.0000000000"

    def test_as_of_before_any_period_returns_null(
        self, client: TestClient, db_session: Session
    ) -> None:
        """No period covers the date, so the ratio is unknown, not nearest."""
        load_universe(db_session)
        item = client.get(
            "/api/v1/cedears", params={"as_of": (DAY - timedelta(days=1)).isoformat()}
        ).json()["items"][0]
        assert item["current_ratio"] is None
        assert item["current_ratio_formatted"] is None

    def test_an_instrument_without_a_ratio_has_null(
        self, client: TestClient, db_session: Session
    ) -> None:
        """``ASTS``-style ambiguous ratios must read as null, never as 0 or 1."""
        load_universe(db_session)
        run_metadata_ingestion(
            db_session,
            [single_snapshot(record("ASTS", ratio=None))],
            effective_date=DAY,
            expected_providers=["comafi"],
        )
        db_session.commit()
        item = client.get("/api/v1/cedears", params={"q": "ASTS"}).json()["items"][0]
        assert item["current_ratio"] is None


class TestDetail:
    """One instrument, with the full history a historical pricer needs."""

    def test_unknown_symbol_is_a_404(self, client: TestClient, db_session: Session) -> None:
        """An unknown symbol and an unknown ratio are different situations."""
        response = client.get("/api/v1/cedears/ZZZZ")
        assert response.status_code == 404
        assert response.json()["detail"]["error"] == "instrument_not_found"

    def test_symbol_lookup_is_case_insensitive(
        self, client: TestClient, db_session: Session
    ) -> None:
        """Tickers are uppercase; a lowercase consumer must still work."""
        load_universe(db_session, ["AAPL"])
        response = client.get("/api/v1/cedears/aapl")
        assert response.status_code == 200
        assert response.json()["symbol"] == "AAPL"

    def test_the_history_is_complete_and_ordered(
        self, client: TestClient, db_session: Session
    ) -> None:
        """Both periods must be present, oldest first."""
        load_universe(db_session, ["AAPL"])
        add_instrument(db_session, record("AAPL", ratio=Decimal(30)))
        body = client.get("/api/v1/cedears/AAPL").json()
        assert [p["ratio_formatted"] for p in body["ratio_history"]] == ["10:1", "30:1"]
        assert body["ratio_history"][0]["effective_to"] == LATER.isoformat()
        assert body["ratio_history"][1]["effective_to"] is None

    def test_first_seen_and_last_seen_are_iso8601(
        self, client: TestClient, db_session: Session
    ) -> None:
        """Consumers parse these; a malformed timestamp would break them silently."""
        load_universe(db_session, ["AAPL"])
        body = client.get("/api/v1/cedears/AAPL").json()
        assert "T" in body["first_seen_at"]
        assert "T" in body["last_seen_at"]

    def test_attributes_are_returned_on_the_detail(
        self, client: TestClient, db_session: Session
    ) -> None:
        """Unmapped fields are reachable for debugging, not part of the contract."""
        load_universe(db_session, ["AAPL"])
        body = client.get("/api/v1/cedears/AAPL").json()
        assert isinstance(body["attributes"], dict)


def test_the_list_endpoint_does_not_touch_the_audit_trail(
    client: TestClient, db_session: Session
) -> None:
    """Reading must be read-only; no ingestion run should be created by a GET."""
    load_universe(db_session)
    client.get("/api/v1/cedears")
    client.get("/api/v1/cedears/AAPL")
    assert len(list(db_session.scalars(select(IngestionRun)))) == 1
    run = db_session.scalars(select(IngestionRun)).one()
    assert run.job == JOB_NAME
