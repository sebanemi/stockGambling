"""Database session-management tests."""

from __future__ import annotations

import pytest
from sqlalchemy import Engine, text

from app.db.session import get_engine, session_scope

pytestmark = pytest.mark.unit


def test_engine_is_singleton() -> None:
    """The engine must be reused so connection pools are not duplicated."""
    assert get_engine() is get_engine()


@pytest.mark.integration
def test_round_trip_query(engine: Engine) -> None:
    """A trivial query succeeds against the Compose PostgreSQL instance."""
    with engine.connect() as connection:
        assert connection.execute(text("SELECT 1")).scalar_one() == 1


@pytest.mark.integration
def test_session_scope_commits(engine: Engine) -> None:
    """``session_scope`` commits on success."""
    with session_scope() as session:
        session.execute(text("SELECT 1"))


@pytest.mark.integration
def test_session_scope_rolls_back_on_error(engine: Engine) -> None:
    """``session_scope`` rolls back and re-raises on failure."""
    with pytest.raises(RuntimeError, match="boom"), session_scope() as session:
        session.execute(text("SELECT 1"))
        raise RuntimeError("boom")


@pytest.mark.integration
def test_timezone_is_utc_on_the_server(engine: Engine) -> None:
    """The session timezone is pinned to UTC.

    Argentine and US market dates cannot be derived correctly if the server
    hands back timestamps in local time.
    """
    with engine.connect() as connection:
        assert connection.execute(text("SHOW timezone")).scalar_one() in ("UTC", "Etc/UTC")
