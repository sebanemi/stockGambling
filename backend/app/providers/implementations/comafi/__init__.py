"""COMAFI Custodio Global provider package."""

from app.providers.implementations.comafi.provider import (
    PRODUCTS_URL,
    ComafiCedearProvider,
    parse_description,
)

__all__ = ["PRODUCTS_URL", "ComafiCedearProvider", "parse_description"]
