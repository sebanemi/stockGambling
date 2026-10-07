"""Provider implementations, one package per source.

The set is deliberately plural: no single official source covers the whole
CEDEAR universe, and pretending otherwise would silently understate it.
"""

from app.providers.implementations.comafi import ComafiCedearProvider

__all__ = ["ComafiCedearProvider"]
