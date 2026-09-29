"""Declarative base and naming conventions for all StockGambling tables.

Design constraints enforced here:

* Every timestamp column is ``TIMESTAMPTZ`` (UTC). Market *dates* are stored
  separately as ``DATE`` so that a BYMA session and an underlying session are
  never conflated.
* Every table is prefixed ``sg_`` to keep the shared schema unambiguous.
* Constraint names are generated deterministically so that Alembic
  autogeneration produces stable, reviewable migrations.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any, ClassVar

from sqlalchemy import DateTime, MetaData, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

NAMING_CONVENTION: Mapping[str, str] = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    """Base class for every ORM model."""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)

    type_annotation_map: ClassVar[dict[Any, Any]] = {
        dict[str, Any]: JSONB,
        list[str]: JSONB,
    }


class TimestampMixin:
    """Adds ``created_at`` / ``updated_at`` bookkeeping columns."""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
