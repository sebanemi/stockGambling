"""``ingest.cedear_prices`` task body tests.

The task is a thin shell over :func:`app.ingestion.prices.run_market_ingestion`.
These tests pin that shell down without fetching from a real provider, touching
the queue or hitting the database: provider selection, the window, the session
scope and the ingestion entry point are all patched, and the instrument query is
intercepted at the ``scalars`` seam.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date
from typing import Any
from unittest.mock import Mock

import pytest
from pytest import MonkeyPatch

from app.ingestion.prices import JOB_NAME
from app.workers.tasks import market_data as market_data_module
from app.workers.tasks.market_data import TASK_NAME, ingest_cedear_prices

pytestmark = pytest.mark.unit

WINDOW = (date(2026, 7, 13), date(2026, 7, 18))


class _RecordedSession:
    """A fake session that records the SQL of every query it runs.

    SQLAlchemy renders a selector to a string without a live connection, which
    is exactly what the scope assertions need: the default scope is *active
    instruments*, and an explicit list becomes a symbol filter.
    """

    def __init__(self, instruments: list[object]) -> None:
        self._instruments = instruments
        self.statements: list[str] = []

    def scalars(self, statement: Any) -> _Scalars:
        self.statements.append(str(statement))
        return _Scalars(self._instruments)


class _Scalars:
    def __init__(self, instruments: list[object]) -> None:
        self._instruments = instruments

    def all(self) -> list[object]:
        return self._instruments


class _FakeReport:
    def as_dict(self) -> dict[str, object]:
        return {"run_id": 7, "status": "succeeded", "local_bars_inserted": 2}


class Stub:
    """The patched seams and the session the task talked through."""

    def __init__(self, runner: Mock, session: _RecordedSession) -> None:
        """Bind the recording runner and the session the task used."""
        self.runner = runner
        self.session = session


@pytest.fixture
def stub_ingestion(monkeypatch: MonkeyPatch) -> Stub:
    """Reach into the task module; result round-trips come from the report."""
    report = _FakeReport()
    runner = Mock(return_value=report)
    session = _RecordedSession([object(), object()])

    @contextmanager
    def running_session(_settings: object) -> Iterator[_RecordedSession]:
        yield session

    monkeypatch.setattr(market_data_module, "run_market_ingestion", runner)
    monkeypatch.setattr(
        market_data_module, "configured_local_price_provider", lambda _settings: Mock(name="local")
    )
    monkeypatch.setattr(
        market_data_module,
        "configured_underlying_provider",
        lambda _settings: Mock(name="underlying"),
    )
    monkeypatch.setattr(
        market_data_module, "configured_fx_provider", lambda _settings: Mock(name="fx")
    )
    monkeypatch.setattr(market_data_module, "backfill_window", lambda _days: WINDOW)
    monkeypatch.setattr(market_data_module, "session_scope", running_session)
    return Stub(runner=runner, session=session)


def test_ingest_cedear_prices_name_is_the_ingestion_job_name() -> None:
    """The queue name equals the run's job name, so logs and queues align."""
    assert TASK_NAME == JOB_NAME


def test_the_task_runs_the_ingestion_with_window_and_returns_a_dict(
    stub_ingestion: Stub,
) -> None:
    """The shell must pass the computed window and round-trip the report."""
    result = ingest_cedear_prices()
    assert result == {"run_id": 7, "status": "succeeded", "local_bars_inserted": 2}

    _args, kwargs = stub_ingestion.runner.call_args
    assert kwargs["start"] == WINDOW[0]
    assert kwargs["end"] == WINDOW[1]
    assert kwargs["fx_pair"] == "USDARS"


def test_the_task_defaults_to_active_instruments(stub_ingestion: Stub) -> None:
    """A daily run covers active instruments, by SQL not by memory."""
    ingest_cedear_prices()
    assert stub_ingestion.session.statements[0].find("sg_instruments.is_active") != -1


def test_the_task_filters_explicit_symbols(stub_ingestion: Stub) -> None:
    """A backfill can pin a subset instead of forcing the whole universe."""
    ingest_cedear_prices(["AAPL", "GOLD"])
    args, _kwargs = stub_ingestion.runner.call_args
    assert len(args[1]) == 2
    assert stub_ingestion.session.statements[0].find("sg_instruments.symbol IN") != -1


def test_a_failed_ingestion_bubbles_the_exception_for_retry(
    monkeypatch: MonkeyPatch,
    stub_ingestion: Stub,
) -> None:
    """A code path raising inside ingestion must escape the task body."""
    monkeypatch.setattr(
        market_data_module, "run_market_ingestion", Mock(side_effect=RuntimeError("boom"))
    )
    with pytest.raises(RuntimeError, match="boom"):
        ingest_cedear_prices()
