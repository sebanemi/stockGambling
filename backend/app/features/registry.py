"""Feature version registry.

Every feature computation references a version so that feature sets are
reproducible and comparable across model runs.
"""

from __future__ import annotations

from datetime import datetime

from app.features.core import get_feature_registry


def get_registered_version() -> str:
    """The current feature registry version."""
    return get_feature_registry().version


def register_feature_version(timestamp: datetime | None = None) -> str:
    """Register the current feature set version.

    Returns the version string that should be stored with any model
    or experiment that uses these features.
    """
    registry = get_feature_registry()
    version = registry.version
    return version
