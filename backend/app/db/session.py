"""Engine and session management.

The engine is created lazily and cached per process. A connection-level
``statement_timeout`` is applied so that a misbehaving query can never block
the API health checks.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import Engine, create_engine, event, text
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings, get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)

_engine: Engine | None = None
_sessionmaker: sessionmaker[Session] | None = None


def _engine_kwargs(settings: Settings) -> dict[str, object]:
    """Build engine keyword arguments from settings."""
    return {
        "pool_pre_ping": True,
        "pool_size": settings.db_pool_size,
        "max_overflow": settings.db_max_overflow,
        "pool_timeout": settings.db_pool_timeout,
        "pool_recycle": settings.db_pool_recycle,
        "echo": settings.db_echo,
        "connect_args": {
            "connect_timeout": settings.db_connect_timeout,
            "application_name": settings.app_name,
        },
    }


@event.listens_for(Engine, "connect")
def _set_statement_timeout(dbapi_connection: object, _record: object) -> None:
    """Apply a defensive statement timeout to every new DBAPI connection."""
    raw = dbapi_connection
    cursor = raw.cursor()  # type: ignore[attr-defined]
    try:
        cursor.execute("SET statement_timeout = '30s'")
        cursor.execute("SET timezone = 'UTC'")
    finally:
        cursor.close()


def get_engine(settings: Settings | None = None) -> Engine:
    """Return the process-wide SQLAlchemy engine, creating it on first use."""
    global _engine
    if _engine is None:
        settings = settings or get_settings()
        _engine = create_engine(settings.sqlalchemy_dsn, **_engine_kwargs(settings))
        logger.info("database.engine_created", dsn=_redact(settings.sqlalchemy_dsn))
    return _engine


def get_sessionmaker(settings: Settings | None = None) -> sessionmaker[Session]:
    """Return the process-wide session factory."""
    global _sessionmaker
    if _sessionmaker is None:
        _sessionmaker = sessionmaker(
            bind=get_engine(settings), autoflush=False, expire_on_commit=False, future=True
        )
    return _sessionmaker


def get_session() -> Iterator[Session]:
    """FastAPI dependency yielding a request-scoped :class:`Session`."""
    session = get_sessionmaker()()
    try:
        yield session
    finally:
        session.close()


@contextmanager
def session_scope(settings: Settings | None = None) -> Iterator[Session]:
    """Transactional scope for workers, scripts and notebooks.

    Commits on success, rolls back on any exception.
    """
    session = get_sessionmaker(settings)()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def ping_database() -> bool:
    """Return ``True`` when a trivial round-trip to PostgreSQL succeeds."""
    try:
        with get_engine().connect() as connection:
            connection.execute(text("SELECT 1"))
        return True
    except Exception as exc:  # noqa: BLE001 - a health check must never raise
        logger.warning("database.ping_failed", error=str(exc))
        return False


def dispose_engine() -> None:
    """Dispose of the engine. Used by tests and by graceful shutdown."""
    global _engine, _sessionmaker
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _sessionmaker = None


def _redact(dsn: str) -> str:
    """Remove the password from a DSN before logging it."""
    if "@" not in dsn or "://" not in dsn:
        return dsn
    scheme, _, rest = dsn.partition("://")
    credentials, _, host = rest.rpartition("@")
    if ":" in credentials:
        user, _, _password = credentials.partition(":")
        credentials = f"{user}:***"
    return f"{scheme}://{credentials}@{host}"
