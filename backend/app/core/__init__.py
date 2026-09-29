"""Cross-cutting concerns: configuration, logging, time handling."""

from app.core.config import Settings, get_settings

__all__ = ["Settings", "get_settings"]
