"""Configuration tests.

Settings are the single place where environment variables turn into runtime
behaviour, so they are tested for precedence, derived values and secret
hygiene.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.core.config import PostgresSettings, RedisSettings, Settings, get_settings

pytestmark = pytest.mark.unit


def test_get_settings_is_cached() -> None:
    """Settings are a singleton so pools are created once per process."""
    assert get_settings() is get_settings()


def test_dsn_built_from_components() -> None:
    """The DSN is derived from host/port/db/user/password when no override."""
    pg = PostgresSettings(
        postgres_host="db",
        postgres_port=5433,
        postgres_db="sg",
        postgres_user="u",
        postgres_password="p",
        database_url=None,
    )
    assert pg.sqlalchemy_dsn == "postgresql+psycopg://u:p@db:5433/sg"


def test_explicit_dsn_wins() -> None:
    """A managed-instance DSN overrides the individual components."""
    pg = PostgresSettings(database_url="postgresql+psycopg://x:y@host:5432/other")
    assert pg.sqlalchemy_dsn == "postgresql+psycopg://x:y@host:5432/other"
    assert pg.async_sqlalchemy_dsn == "postgresql+psycopg_async://x:y@host:5432/other"


def test_async_dsn_uses_async_driver() -> None:
    """The async DSN must not silently reuse the sync driver."""
    pg = PostgresSettings(
        postgres_host="localhost", postgres_user="u", postgres_password="p", postgres_db="d"
    )
    assert pg.async_sqlalchemy_dsn.startswith("postgresql+psycopg_async://")


def test_redis_urls_are_separated() -> None:
    """Broker, cache and result backend must not collide by accident."""
    redis_settings = RedisSettings(
        redis_host="cache", redis_port=6380, redis_db=0, celery_result_backend_db=1
    )
    assert redis_settings.redis_url == "redis://cache:6380/0"
    assert redis_settings.celery_broker_url == "redis://cache:6380/0"
    assert redis_settings.celery_result_backend_url == "redis://cache:6380/1"


def test_redis_password_is_embedded_when_set() -> None:
    """An authenticated Redis instance must produce an authenticated URL."""
    redis_settings = RedisSettings(redis_password="s3cr3t", redis_host="h", redis_port=6379)
    assert redis_settings.redis_url == "redis://:s3cr3t@h:6379/0"


def test_cors_origins_are_normalised() -> None:
    """Trailing slashes and blanks are stripped from CORS origins."""
    settings = Settings(
        cors_origins=["http://localhost:3000/", "  ", "https://stockgambling.dev"],
    )
    assert settings.cors_origin_list == ["http://localhost:3000", "https://stockgambling.dev"]


def test_invalid_environment_is_rejected() -> None:
    """An unknown environment must fail fast instead of silently defaulting."""
    with pytest.raises(ValidationError):
        Settings(app_env="productionn")


def test_log_format_is_restricted() -> None:
    """Only the two supported renderers are accepted."""
    with pytest.raises(ValidationError):
        Settings(log_format="xml")


def test_test_env_flag(settings: Settings) -> None:
    """``is_test`` reflects the environment used by the suite."""
    assert settings.is_test is True
    assert settings.is_production is False
