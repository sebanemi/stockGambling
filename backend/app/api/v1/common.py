"""Shared helpers for the versioned API routers.

Every router resolves instruments, paginates and reports missing resources
the same way, so the response envelope and error shapes stay consistent as
new endpoints (models, backtests, experiments, predictions) are added.
"""

from __future__ import annotations

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.instrument import Instrument

#: Upper bound for ``limit`` on every paginated endpoint.
MAX_PAGE_SIZE = 200


def resolve_instrument(session: Session, symbol: str) -> Instrument:
    """Load an instrument by BYMA ticker or raise a 404.

    Raises:
        HTTPException: ``404`` with an ``instrument_not_found`` error payload
            when the symbol is not in the stored CEDEAR universe.
    """
    normalised = symbol.strip().upper()
    instrument = session.scalar(select(Instrument).where(Instrument.symbol == normalised))
    if instrument is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error": "instrument_not_found",
                "symbol": normalised,
                "message": f"{normalised} is not in the stored CEDEAR universe.",
            },
        )
    return instrument


__all__ = ["MAX_PAGE_SIZE", "resolve_instrument"]
