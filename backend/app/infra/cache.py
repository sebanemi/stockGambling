"""Redis cache client.

Redis backs both the application cache and the Celery broker. The client is
created lazily and cached per process.
"""

from __future__ import annotations

import json
from typing import Any

import redis

from app.core.config import Settings, get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)

_client: redis.Redis | None = None


def get_redis(settings: Settings | None = None) -> redis.Redis:
    """Return the process-wide synchronous Redis client."""
    global _client
    if _client is None:
        settings = settings or get_settings()
        _client = redis.Redis.from_url(
            settings.redis_url,
            socket_timeout=settings.redis_socket_timeout,
            socket_connect_timeout=settings.redis_socket_timeout,
            decode_responses=True,
        )
        logger.info("cache.client_created", url=settings.redis_url)
    return _client


def ping_redis() -> bool:
    """Return ``True`` when Redis answers a ``PING``."""
    try:
        return bool(get_redis().ping())
    except Exception as exc:  # noqa: BLE001 - health checks must never raise
        logger.warning("cache.ping_failed", error=str(exc))
        return False


def cache_get(key: str) -> Any:
    """Read a JSON value from the cache, or ``None`` when absent/unavailable."""
    try:
        raw = get_redis().get(key)
    except Exception as exc:  # noqa: BLE001 - the cache is never critical
        logger.warning("cache.get_failed", key=key, error=str(exc))
        return None
    if not isinstance(raw, str | bytes | bytearray):
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("cache.decode_failed", key=key)
        return None


def cache_set(key: str, value: Any, ttl_seconds: int = 300) -> bool:
    """Store a JSON value in the cache. Returns ``False`` on any failure."""
    try:
        get_redis().setex(key, ttl_seconds, json.dumps(value, default=str))
    except Exception as exc:  # noqa: BLE001 - the cache is never critical
        logger.warning("cache.set_failed", key=key, error=str(exc))
        return False
    return True


def close_redis() -> None:
    """Close the Redis client. Used by tests and by graceful shutdown."""
    global _client
    if _client is not None:
        _client.close()  # type: ignore[no-untyped-call]
    _client = None
