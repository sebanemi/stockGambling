"""Application configuration.

All runtime configuration is environment driven. The settings object is
composed from small, focused sub-settings so that each concern (database,
cache, queue) can be validated and overridden independently.

Secrets are *never* read from files committed to the repository: only
``.env`` (git-ignored) and real environment variables are used.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Annotated, Any, Literal

from pydantic import Field, computed_field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

EnvLiteral = Literal["development", "staging", "production", "test"]

#: Comma-separated list from an environment variable, e.g.
#: ``CORS_ORIGINS=http://localhost:3000,https://stockgambling.dev``.
#: JSON arrays are accepted too. ``NoDecode`` is required so pydantic-settings
#: hands the raw string to the validator instead of failing on JSON parsing.
CsvList = Annotated[list[str], NoDecode]


def _split_csv(value: Any) -> Any:
    """Normalise a comma-separated string into a list of strings."""
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    return value


class AppSettings(BaseSettings):
    """Web-application level settings."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    app_name: str = "stockgambling-api"
    app_env: EnvLiteral = "development"
    app_version: str = "0.1.0"
    debug: bool = True
    docs_enabled: bool = True
    api_prefix: str = "/api/v1"

    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    log_format: Literal["console", "json"] = "console"

    cors_origins: CsvList = Field(
        default_factory=lambda: ["http://localhost:3000"],
        description="Comma-separated list of allowed browser origins.",
    )

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _parse_cors_origins(cls, value: Any) -> Any:
        """Accept both ``a,b`` and ``["a","b"]`` from the environment."""
        return _split_csv(value)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def is_production(self) -> bool:
        """Whether the process runs with production semantics."""
        return self.app_env == "production"

    @computed_field  # type: ignore[prop-decorator]
    @property
    def cors_origin_list(self) -> list[str]:
        """Normalised CORS origins (strips trailing slashes and blanks)."""
        return [origin.strip().rstrip("/") for origin in self.cors_origins if origin.strip()]


class PostgresSettings(BaseSettings):
    """PostgreSQL connection settings.

    Components are the source of truth. ``database_url`` is an optional
    override for managed instances that hand out a single DSN.
    """

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore", case_sensitive=False
    )

    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_db: str = "stockgambling"
    postgres_user: str = "stockgambling"
    # Local-only placeholder; never a real credential. Compose overrides it and
    # deployments are expected to inject a strong value from the environment.
    postgres_password: str = "change-me-local-only"  # noqa: S105

    db_pool_size: int = 5
    db_max_overflow: int = 10
    db_pool_timeout: int = 30
    db_pool_recycle: int = 1800
    db_connect_timeout: int = 5
    db_echo: bool = False

    database_url: str | None = None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def sqlalchemy_dsn(self) -> str:
        """SQLAlchemy DSN using the psycopg (v3) driver."""
        if self.database_url:
            return self.database_url
        return (
            f"postgresql+psycopg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def async_sqlalchemy_dsn(self) -> str:
        """DSN for the psycopg async driver (used by long-running jobs)."""
        return self.sqlalchemy_dsn.replace("postgresql+psycopg://", "postgresql+psycopg_async://")


class RedisSettings(BaseSettings):
    """Redis connection settings for caching, Celery broker and results."""

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore", case_sensitive=False
    )

    redis_host: str = "localhost"
    redis_port: int = 6379
    redis_db: int = 0
    redis_password: str | None = None
    redis_socket_timeout: int = 5
    celery_result_backend_db: int = 1

    @computed_field  # type: ignore[prop-decorator]
    @property
    def redis_url(self) -> str:
        """Redis URL for the application cache."""
        return self._url(self.redis_db)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def celery_broker_url(self) -> str:
        """Redis URL used as the Celery broker."""
        return self._url(self.redis_db)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def celery_result_backend_url(self) -> str:
        """Redis URL used to store Celery task results."""
        return self._url(self.celery_result_backend_db)

    def _url(self, db: int) -> str:
        auth = f":{self.redis_password}@" if self.redis_password else ""
        return f"redis://{auth}{self.redis_host}:{self.redis_port}/{db}"


class ProviderSettings(BaseSettings):
    """Market-data provider selection and credentials.

    Providers are pluggable (see ``app.providers``). Credentials are supplied
    at runtime through the environment and are never persisted.
    """

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore", case_sensitive=False
    )

    cedear_metadata_provider: str | None = None
    underlying_provider: str | None = None
    underlying_provider_api_key: str | None = None
    fx_provider: str | None = None
    fx_provider_api_key: str | None = None


class CelerySettings(BaseSettings):
    """Celery worker tuning."""

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore", case_sensitive=False
    )

    celery_worker_concurrency: int = 2
    celery_task_always_eager: bool = False
    celery_task_default_queue: str = "stockgambling"
    celery_task_time_limit: int = 3600
    celery_task_soft_time_limit: int = 3300


class Settings(AppSettings, PostgresSettings, RedisSettings, ProviderSettings, CelerySettings):
    """Aggregated application settings."""

    @computed_field  # type: ignore[prop-decorator]
    @property
    def is_test(self) -> bool:
        """Whether the process is running the test suite."""
        return self.app_env == "test"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the cached settings singleton.

    Cached so that connection pools are created once per process.
    """
    return Settings()
