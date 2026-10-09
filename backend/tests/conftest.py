"""Shared pytest fixtures.

Schema management deliberately uses **Alembic**, not ``Base.metadata.create_all``.
The tests must exercise the migration that actually ships; a schema created from
the ORM models would pass while the hand-written migration was broken.
"""

from __future__ import annotations

import os
from collections.abc import Iterator

# The environment MUST be configured before any `app.*` module is imported:
# `app.main` builds the FastAPI instance at import time, which reads settings.
os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("LOG_LEVEL", "WARNING")
os.environ.setdefault("DEBUG", "false")

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.db.session import dispose_engine, get_engine
from app.infra.cache import close_redis
from app.main import create_app

BACKEND_ROOT = Path(__file__).resolve().parents[1]

#: Every table the migrations own, in dependency order. Integration tests wipe
#: these between runs so they neither leak state into each other nor depend on
#: execution order.
TRUNCATE_ORDER: tuple[str, ...] = (
    "sg_backtests",
    "sg_model_runs",
    "sg_models",
    "sg_feature_snapshots",
    "sg_theoretical_price_history",
    "sg_ingestion_runs",
    "sg_instrument_ratio_history",
    "sg_local_price_history",
    "sg_underlying_price_history",
    "sg_instruments",
    "sg_fx_history",
)


def _alembic_config(settings: Settings) -> Config:
    """Build an Alembic config pointed at this repository's migrations."""
    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_ROOT / "migrations"))
    # The env module prefers this over the application settings, so tests can
    # target a throwaway database without touching `.env`.
    config.set_main_option("sqlalchemy.url", settings.sqlalchemy_dsn)
    return config


@pytest.fixture(scope="session")
def settings() -> Settings:
    """Return the session-scoped settings object."""
    return get_settings()


@pytest.fixture(scope="session")
def client(settings: Settings) -> Iterator[TestClient]:
    """Return a TestClient bound to a freshly built application."""
    dispose_engine()
    close_redis()
    with TestClient(create_app(settings)) as test_client:
        yield test_client
    dispose_engine()
    close_redis()


@pytest.fixture(scope="session")
def engine() -> Engine:
    """Return the SQLAlchemy engine, skipping when PostgreSQL is unreachable."""
    try:
        candidate = get_engine()
        with candidate.connect() as connection:
            connection.execute(text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001 - skip on any driver error
        pytest.skip(f"PostgreSQL is not reachable: {exc}")
    return candidate


@pytest.fixture(scope="session")
def migrated_database(engine: Engine, settings: Settings) -> None:
    """Bring the schema to head once per session.

    ``alembic upgrade head`` rather than ``create_all``, so the tests run against
    the migration that ships. Idempotent: a database already at head is left
    alone.
    """
    with engine.connect() as connection:
        current = MigrationContext.configure(connection).get_current_revision()
    scripts = ScriptDirectory.from_config(_alembic_config(settings))
    head = scripts.get_current_head()
    if current == head:
        return
    command.upgrade(_alembic_config(settings), "head")


@pytest.fixture
def db_session(engine: Engine, migrated_database: None) -> Iterator[Session]:
    """Yield a session against a database truncated for this test.

    Truncation happens both before and after, so tests are independent of
    execution order and a test that commits cannot leak rows into the next one.
    """
    _truncate(engine)
    with Session(engine) as session:
        try:
            yield session
        finally:
            session.rollback()
            _truncate(engine)


def _truncate(engine: Engine) -> None:
    """Remove every row from the project's tables."""
    statements = "; ".join(
        f"TRUNCATE TABLE {name} RESTART IDENTITY CASCADE" for name in TRUNCATE_ORDER
    )
    with engine.begin() as connection:
        connection.execute(text(statements))
