"""Provider interfaces and adapters.

Importing this package gives the application everything it needs to talk about
CEDEAR metadata without importing a vendor module directly.
"""

from app.providers.base import (
    UNDERLYING_BAR_COLUMNS,
    CedearDataProvider,
    CedearRecord,
    CedearSnapshot,
    ProviderError,
    RejectedRecord,
    UnderlyingDataProvider,
)
from app.providers.registry import (
    CEDEAR_PROVIDERS,
    UnknownProviderError,
    available_cedear_providers,
    cedear_providers,
    configured_cedear_providers,
    resolve_cedear_provider,
)

__all__ = [
    "CEDEAR_PROVIDERS",
    "UNDERLYING_BAR_COLUMNS",
    "CedearDataProvider",
    "CedearRecord",
    "CedearSnapshot",
    "ProviderError",
    "RejectedRecord",
    "UnderlyingDataProvider",
    "UnknownProviderError",
    "available_cedear_providers",
    "cedear_providers",
    "configured_cedear_providers",
    "resolve_cedear_provider",
]
