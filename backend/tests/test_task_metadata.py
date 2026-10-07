"""``ingest.cedear_metadata`` task body tests.

The task is a thin shell over :func:`app.ingestion.fetch_and_ingest`; these tests
pin that shell down without fetching from a real provider or touching the queue.
The three seams are patched at module level: provider selection, the database
session scope and the ingestion entry point.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date
from typing import Any, NamedTuple
from unittest.mock import Mock

import pytest
from pytest import MonkeyPatch

from app.ingestion import JOB_NAME
from app.workers.tasks import metadata as metadata_module
from app.workers.tasks.metadata import TASK_NAME, ingest_cedear_metadata

pytestmark = pytest.mark.unit


class _FakeSession:
    """A placeholder session the test never visits."""


class _FakeReport:
    """A report whose dict form the task must pass through untouched."""

    def as_dict(self) -> dict[str, object]:
        return {"ingested": 3, "warnings": ["a provider was down"]}


class Stub(NamedTuple):
    """The patched seams, so a test can inspect what the task called."""

    fetcher: Mock
    report: _FakeReport


@contextmanager
def _noop_scope(_settings: object) -> Iterator[Any]:
    """Impersonate ``session_scope`` without touching the database."""
    yield _FakeSession()


@pytest.fixture
def stub_ingestion(monkeypatch: MonkeyPatch) -> Stub:
    """Reach into the task module; result round-trips come from the report."""
    report = _FakeReport()
    fetcher = Mock(return_value=report)
    monkeypatch.setattr(metadata_module, "fetch_and_ingest", fetcher)
    monkeypatch.setattr(metadata_module, "configured_cedear_providers", lambda _settings: [])
    monkeypatch.setattr(metadata_module, "session_scope", _noop_scope)
    return Stub(fetcher=fetcher, report=report)


def test_the_task_name_is_the_ingestion_job_name() -> None:
    """The queue name equals the run's job name, so logs and queues align."""
    assert TASK_NAME == JOB_NAME


def test_the_task_passes_the_parsed_effective_date_down(
    stub_ingestion: Stub,
) -> None:
    """``2026-03-10`` must reach ingestion as a date, not as a string."""
    ingest_cedear_metadata("2026-03-10")
    assert stub_ingestion.fetcher.call_count == 1
    _args, kwargs = stub_ingestion.fetcher.call_args
    assert kwargs["effective_date"] == date(2026, 3, 10)


def test_the_task_defaults_the_effective_date_to_none(stub_ingestion: Stub) -> None:
    """Ingestion decides "today" itself; the task must not bake in a date."""
    ingest_cedear_metadata()
    _args, kwargs = stub_ingestion.fetcher.call_args
    assert kwargs["effective_date"] is None


def test_the_task_passes_the_configured_providers(stub_ingestion: Stub) -> None:
    """Provider selection happens per run from current settings."""
    ingest_cedear_metadata()
    args, _kwargs = stub_ingestion.fetcher.call_args
    assert args[1] == []


def test_the_task_returns_the_report_as_a_plain_dict(stub_ingestion: Stub) -> None:
    """The result backend stores JSON; the report must round-trip as a dict."""
    result = ingest_cedear_metadata()
    assert result == {"ingested": 3, "warnings": ["a provider was down"]}


def test_a_failed_fetch_bubbles_the_exception_for_retry(
    monkeypatch: MonkeyPatch, stub_ingestion: Stub
) -> None:
    """A code path raising inside ingestion must escape the task body."""
    monkeypatch.setattr(metadata_module, "fetch_and_ingest", Mock(side_effect=RuntimeError("boom")))
    with pytest.raises(RuntimeError, match="boom"):
        ingest_cedear_metadata()
