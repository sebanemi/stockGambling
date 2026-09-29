"""Shared pytest fixtures."""

from __future__ import annotations

import os
from collections.abc import Iterator

# The environment MUST be configured before any `app.*` module is imported:
# `app.main` builds the FastAPI instance at import time, which reads settings.
os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("LOG_LEVEL", "WARNING")
os.environ.setdefault("DEBUG", "false")

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text

from app.core.config import Settings, get_settings
from app.db.base import Base
from app.db.session import dispose_engine, get_engine
from app.infra.cache import close_redis
from app.main import create_app


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


@pytest.fixture
def prepared_schema(engine: Engine) -> Iterator[None]:
    """Create the ORM schema for the duration of a test and drop it after.

    Used by the first integration tests. Phase 2 replaces this with real
    Alembic migrations.
    """
    Base.metadata.create_all(bind=engine)
    try:
        yield
    finally:
        Base.metadata.drop_all(bind=engine)
