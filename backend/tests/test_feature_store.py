"""Feature store tests."""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from sqlalchemy.orm import Session

from app.features.store import (
    count_feature_snapshots,
    decode_feature_values,
    list_feature_snapshots,
    read_feature_snapshot,
    write_feature_snapshot,
    write_feature_snapshots_bulk,
)
from app.models.features import FeatureStore
from app.models.instrument import Instrument

pytestmark = pytest.mark.integration


def _instrument(session: Session, symbol: str) -> Instrument:
    inst = Instrument(symbol=symbol, instrument_type="STOCK")
    session.add(inst)
    session.flush()
    return inst


class TestFeatureStore:
    """Writer/reader round-trips, bulk loads and history pagination."""

    def test_write_and_read_snapshot(self, db_session: Session) -> None:
        """A written snapshot reads back with its payload intact."""
        inst = _instrument(db_session, "STORE1")
        snapshot = write_feature_snapshot(
            db_session,
            instrument_id=inst.id,
            as_of=datetime(2026, 1, 1, 18, 0, 0, tzinfo=UTC),
            feature_version="1.0",
            market_date=date(2026, 1, 1),
            feature_values={"cedear_return_1d": 0.01},
        )
        assert snapshot.id is not None
        read = read_feature_snapshot(
            db_session,
            instrument_id=inst.id,
            as_of=datetime(2026, 1, 1, 18, 0, 0, tzinfo=UTC),
            feature_version="1.0",
        )
        assert read is not None
        assert read.instrument_id == inst.id
        assert decode_feature_values(read) == {"cedear_return_1d": 0.01}

    def test_bulk_write_and_pagination(self, db_session: Session) -> None:
        """Bulk writes commit once; listing pages newest-first."""
        inst = _instrument(db_session, "STORE2")
        rows: list[tuple[int, datetime, str, date, dict[str, float | int | None]]] = [
            (
                inst.id,
                datetime(2026, 2, d, 21, 0, tzinfo=UTC),
                "1.0",
                date(2026, 2, d),
                {"cedear_return_1d": 0.01 * d},
            )
            for d in range(1, 6)
        ]
        written = write_feature_snapshots_bulk(db_session, rows)
        assert len(written) == 5
        assert count_feature_snapshots(db_session, instrument_id=inst.id) == 5

        page = list_feature_snapshots(db_session, instrument_id=inst.id, limit=2, offset=0)
        assert len(page) == 2
        # Newest first.
        assert page[0].market_date == date(2026, 2, 5)
        rest = list_feature_snapshots(db_session, instrument_id=inst.id, limit=10, offset=2)
        assert len(rest) == 3

    def test_range_filter(self, db_session: Session) -> None:
        """Start/end bounds select the matching market-date slice."""
        inst = _instrument(db_session, "STORE3")
        rows: list[tuple[int, datetime, str, date, dict[str, float | int | None]]] = [
            (
                inst.id,
                datetime(2026, 3, d, 21, 0, tzinfo=UTC),
                "1.0",
                date(2026, 3, d),
                {"fx_return_1d": 0.0},
            )
            for d in range(1, 6)
        ]
        write_feature_snapshots_bulk(db_session, rows)
        in_range = list_feature_snapshots(
            db_session,
            instrument_id=inst.id,
            start=date(2026, 3, 2),
            end=date(2026, 3, 3),
        )
        assert {r.market_date for r in in_range} == {date(2026, 3, 2), date(2026, 3, 3)}

    def test_decode_empty_snapshot(self, db_session: Session) -> None:
        """A null payload decodes to an empty dict, never an exception."""
        inst = _instrument(db_session, "STORE4")
        snapshot = FeatureStore(
            instrument_id=inst.id,
            as_of=datetime(2026, 4, 1, 21, 0, tzinfo=UTC),
            feature_version="1.0",
            market_date=date(2026, 4, 1),
            feature_values_json=None,
        )
        db_session.add(snapshot)
        db_session.commit()
        assert decode_feature_values(snapshot) == {}
