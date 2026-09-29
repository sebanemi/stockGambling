"""Celery worker wiring tests.

Verifies the broker/result URLs are wired from settings and that task
registration is namespaced, without requiring a running Redis.
"""

from __future__ import annotations

import pytest

from app.core.config import Settings

# Imported for its side effect: importing the task module registers every task
# on the Celery app (the `include=` entry is resolved lazily).
from app.workers import tasks as _tasks
from app.workers.celery_app import celery_app

pytestmark = pytest.mark.unit


def test_broker_comes_from_settings(settings: Settings) -> None:
    """The broker URL is derived from the Redis settings, not hardcoded."""
    assert celery_app.conf.broker_url == settings.celery_broker_url
    assert celery_app.conf.result_backend == settings.celery_result_backend_url


def test_task_serialisation_is_json() -> None:
    """Only JSON payloads are accepted; pickle is never enabled."""
    assert celery_app.conf.task_serializer == "json"
    assert celery_app.conf.accept_content == ["json"]


def test_timezone_is_utc() -> None:
    """Celery must schedule in UTC; market timezones are applied in code."""
    assert celery_app.conf.timezone == "UTC"
    assert celery_app.conf.enable_utc is True


def test_tasks_are_namespaced() -> None:
    """Every StockGambling task lives under the ``system.`` namespace."""
    names = {name for name in celery_app.tasks if not name.startswith("celery.")}
    assert "system.ping" in names
    assert "system.describe" in names
    assert all(name.startswith("system.") for name in names)


@pytest.mark.integration
def test_task_round_trip_in_eager_mode(settings: Settings) -> None:
    """The task body runs and its JSON result is readable end to end."""
    celery_app.conf.task_always_eager = True
    try:
        result = _tasks.describe.apply().get()
    finally:
        celery_app.conf.task_always_eager = settings.celery_task_always_eager
    assert "system.ping" in result["tasks"]
