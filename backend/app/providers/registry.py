"""Provider registry: a stable name resolves to a concrete implementation.

Selection is configuration, not code. ``CEDEAR_METADATA_PROVIDER=comafi`` in the
environment changes which adapter runs; adding a new vendor adds a line here
and nothing anywhere else. This is the guarantee ``docs/data-providers.md``
makes: no change to the schema, the ingestion service, the API or the models is
required to swap a data source.

**Defaulting.** When ``CEDEAR_METADATA_PROVIDER`` is unset the registry returns
*every* registered CEDEAR provider, because no single official source covers
the whole universe. COMAFI lists the programs it sponsors, Caja de Valores the
ones it issues, and the remainder are foreign-company-sponsored programs that
appear in neither. Defaulting to one of them would silently understate the
universe, so the default is the union and the ingestion service records which
sources contributed.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import TypeVar

from app.core.config import Settings
from app.core.logging import get_logger
from app.providers.base import (
    CedearDataProvider,
    FXDataProvider,
    LocalPriceDataProvider,
    UnderlyingDataProvider,
)
from app.providers.implementations.cajadevalores import CajaDeValoresCedearProvider
from app.providers.implementations.comafi import ComafiCedearProvider
from app.providers.implementations.yahoo import (
    YahooFxProvider,
    YahooLocalPriceProvider,
    YahooUnderlyingProvider,
)

logger = get_logger(__name__)

#: name -> zero-argument factory. Factories rather than classes so a provider
#: that needs an API key or a custom fetcher can be constructed on demand.
CEDEAR_PROVIDERS: dict[str, Callable[[], CedearDataProvider]] = {
    ComafiCedearProvider.name: ComafiCedearProvider,
    CajaDeValoresCedearProvider.name: CajaDeValoresCedearProvider,
}

#: Price series come from one vendor each. The defaults answer ``None`` in
#: settings, so unset means "the default implementation", never "run them all".
UNDERLYING_PROVIDERS: dict[str, Callable[[], UnderlyingDataProvider]] = {
    YahooUnderlyingProvider.name: YahooUnderlyingProvider,
}
LOCAL_PRICE_PROVIDERS: dict[str, Callable[[], LocalPriceDataProvider]] = {
    YahooLocalPriceProvider.name: YahooLocalPriceProvider,
}
FX_PROVIDERS: dict[str, Callable[[], FXDataProvider]] = {
    YahooFxProvider.name: YahooFxProvider,
}

DEFAULT_PRICE_PROVIDER = "yahoo"

#: Order used when the union of all providers is taken. Earlier entries win a
#: field-level conflict, so a source that publishes the program status is
#: preferred over one that does not. Neither list is a superset of the other,
#: so this is a tie-break, not a hierarchy of truth.
DEFAULT_CEDEAR_PROVIDER_ORDER: tuple[str, ...] = ("comafi", "cajadevalores")


class UnknownProviderError(LookupError):
    """A configured provider name has no registered implementation.

    Raised instead of silently falling back: a typo in an environment variable
    that quietly disables half the universe is the exact failure mode this
    registry exists to prevent.
    """


def available_cedear_providers() -> tuple[str, ...]:
    """Names that :func:`cedear_providers` accepts, in the default order."""
    registered = set(CEDEAR_PROVIDERS)
    return tuple(name for name in DEFAULT_CEDEAR_PROVIDER_ORDER if name in registered) + tuple(
        sorted(registered - set(DEFAULT_CEDEAR_PROVIDER_ORDER))
    )


def resolve_cedear_provider(name: str) -> CedearDataProvider:
    """Instantiate one provider by name.

    Raises:
        UnknownProviderError: when ``name`` is not registered.
    """
    try:
        factory = CEDEAR_PROVIDERS[name]
    except KeyError:
        raise UnknownProviderError(
            f"unknown CEDEAR metadata provider {name!r}; "
            f"available: {', '.join(available_cedear_providers())}"
        ) from None
    return factory()


def cedear_providers(names: str | Sequence[str] | None) -> list[CedearDataProvider]:
    """Instantiate the configured providers, in the given order.

    Args:
        names: A comma-separated string or a sequence of provider names, as
            produced by ``CEDEAR_METADATA_PROVIDER``. ``None`` or empty selects
            every registered provider.

    Raises:
        UnknownProviderError: if any name is not registered.
    """
    if names is None:
        selected = available_cedear_providers()
    elif isinstance(names, str):
        selected = tuple(name.strip() for name in names.split(",") if name.strip())
    else:
        selected = tuple(name.strip() for name in names if name.strip())

    if not selected:
        selected = available_cedear_providers()

    seen: set[str] = set()
    providers: list[CedearDataProvider] = []
    for name in selected:
        if name in seen:
            continue
        seen.add(name)
        providers.append(resolve_cedear_provider(name))

    logger.info(
        "providers.resolved",
        requested=selected or None,
        resolved=[provider.name for provider in providers],
    )
    return providers


def configured_cedear_providers(settings: Settings) -> list[CedearDataProvider]:
    """Instantiate the providers named by :class:`~app.core.config.Settings`.

    Accepts the settings object rather than a string so that the ingestion task
    does not have to know how the value is spelled in the environment.
    """
    return cedear_providers(settings.cedear_metadata_provider)


PriceProviderT = TypeVar("PriceProviderT")


def _single(
    names: dict[str, Callable[[], PriceProviderT]], configured: str | None, kind: str
) -> PriceProviderT:
    """Instantiate one provider of ``kind`` by its configured name.

    ``None`` means "the default implementation" - for price series there is no
    union, so a single vendor is always selected.
    """
    name = (configured or DEFAULT_PRICE_PROVIDER).strip()
    try:
        factory = names[name]
    except KeyError:
        raise UnknownProviderError(
            f"unknown {kind} provider {name!r}; " f"available: {', '.join(sorted(names))}"
        ) from None
    return factory()


def configured_underlying_provider(settings: Settings) -> UnderlyingDataProvider:
    """The underlying OHLCV provider, defaulting to Yahoo."""
    return _single(UNDERLYING_PROVIDERS, settings.underlying_provider, "underlying")


def configured_local_price_provider(settings: Settings) -> LocalPriceDataProvider:
    """The local CEDEAR OHLCV provider, defaulting to Yahoo."""
    return _single(LOCAL_PRICE_PROVIDERS, settings.local_price_provider, "local price")


def configured_fx_provider(settings: Settings) -> FXDataProvider:
    """The FX provider, defaulting to Yahoo."""
    return _single(FX_PROVIDERS, settings.fx_provider, "fx")


__all__ = [
    "CEDEAR_PROVIDERS",
    "DEFAULT_CEDEAR_PROVIDER_ORDER",
    "DEFAULT_PRICE_PROVIDER",
    "FX_PROVIDERS",
    "LOCAL_PRICE_PROVIDERS",
    "UNDERLYING_PROVIDERS",
    "UnknownProviderError",
    "available_cedear_providers",
    "cedear_providers",
    "configured_cedear_providers",
    "configured_fx_provider",
    "configured_local_price_provider",
    "configured_underlying_provider",
    "resolve_cedear_provider",
]
