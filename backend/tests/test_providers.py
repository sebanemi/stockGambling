"""Provider selection and the contracts adapters must honour.

The registry is the seam that lets a vendor be swapped by configuration, and the
in-memory contracts are what make that swap safe. Two things are tested here
that nothing else covers: that an unknown name fails loudly instead of quietly
halving the universe, and that adapters cannot be written to skip the rejection
audit trail.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from app.core.config import Settings
from app.domain.vocabulary import UnderlyingMarket
from app.providers.base import (
    UNDERLYING_BAR_COLUMNS,
    BarSnapshot,
    CedearDataProvider,
    CedearRecord,
    CedearSnapshot,
    DailyBar,
    ProviderError,
    RejectedRecord,
    UnderlyingDataProvider,
)
from app.providers.registry import (
    CEDEAR_PROVIDERS,
    DEFAULT_CEDEAR_PROVIDER_ORDER,
    UnknownProviderError,
    available_cedear_providers,
    cedear_providers,
    configured_cedear_providers,
    resolve_cedear_provider,
)

pytestmark = pytest.mark.unit


class TestDefaultSelection:
    """No single source covers the universe, so the default is the union."""

    def test_unset_selection_returns_every_provider(self) -> None:
        """Defaulting to one source would silently understate the universe.

        COMAFI lists the programs it sponsors and Caja de Valores the ones it
        issues; the foreign-company-sponsored remainder is in neither, so any
        single-source default is a subset that must be declared, not assumed.
        """
        assert {p.name for p in cedear_providers(None)} == set(available_cedear_providers())

    def test_empty_string_also_returns_every_provider(self) -> None:
        """An empty environment variable must mean "unset", not "none"."""
        assert len(cedear_providers("")) == len(available_cedear_providers())

    def test_default_order_puts_comafi_first(self) -> None:
        """Order decides which source's value wins a non-conflicting field.

        COMAFI publishes the program status and Caja de Valores does not, so it
        goes first.
        """
        assert cedear_providers(None)[0].name == DEFAULT_CEDEAR_PROVIDER_ORDER[0]

    def test_available_names_match_the_registry(self) -> None:
        """A registered provider must be discoverable by name."""
        assert set(available_cedear_providers()) == set(CEDEAR_PROVIDERS)


class TestExplicitSelection:
    """Configuration narrows the union when an operator asks for it."""

    def test_single_name(self) -> None:
        """One source is a deliberate choice, recorded in the run."""
        assert [p.name for p in cedear_providers("comafi")] == ["comafi"]

    def test_comma_separated_list(self) -> None:
        """The environment variable is a single comma-separated string."""
        assert [p.name for p in cedear_providers("comafi,cajadevalores")] == [
            "comafi",
            "cajadevalores",
        ]

    def test_surrounding_whitespace_is_ignored(self) -> None:
        """A trailing comma or a stray space in ``.env`` is not an error."""
        assert [p.name for p in cedear_providers(" comafi , ")] == ["comafi"]

    def test_a_repeated_name_is_instantiated_once(self) -> None:
        """Fetching the same source twice would double-count its records."""
        assert [p.name for p in cedear_providers("comafi,comafi")] == ["comafi"]

    def test_explicit_order_is_honoured(self) -> None:
        """An operator overriding the default order changes the tie-break."""
        assert [p.name for p in cedear_providers("cajadevalores,comafi")] == [
            "cajadevalores",
            "comafi",
        ]

    def test_settings_are_read_from_the_environment_variable(self, settings: Settings) -> None:
        """The task must not need to know how the value is spelled."""
        settings.cedear_metadata_provider = "comafi"
        assert [p.name for p in configured_cedear_providers(settings)] == ["comafi"]


class TestUnknownProvider:
    """A typo must be loud, not silent."""

    def test_unknown_name_raises(self) -> None:
        """Falling back to the default would hide the misconfiguration.

        The failure this prevents: a deployment quietly ingesting half the
        universe for months because of one wrong environment variable.
        """
        with pytest.raises(UnknownProviderError, match="comafi2"):
            cedear_providers("comafi2")

    def test_the_error_lists_the_available_names(self) -> None:
        """An operator cannot fix a typo they cannot see."""
        with pytest.raises(UnknownProviderError) as excinfo:
            resolve_cedear_provider("nope")
        assert "cajadevalores" in str(excinfo.value)

    def test_a_valid_name_in_a_mixed_list_also_raises(self) -> None:
        """Partially applying a broken configuration is the worst outcome."""
        with pytest.raises(UnknownProviderError):
            cedear_providers("comafi,typo")

    def test_the_error_is_a_lookup_error(self) -> None:
        """Callers can catch it without importing this module's internals."""
        assert issubclass(UnknownProviderError, LookupError)


class TestProviderContract:
    """The abstract base class must actually be abstract."""

    def test_incomplete_implementation_cannot_be_instantiated(self) -> None:
        """A half-written adapter must fail at construction, not at 3am.

        Without the abstract methods a provider missing ``fetch`` would be
        accepted by the registry and only fail when the task called it.
        """
        with pytest.raises(TypeError):
            CedearDataProvider()  # type: ignore[abstract]

    def test_name_is_the_stable_identifier(self) -> None:
        """The name is persisted on ratio rows and in the run audit."""
        for name, factory in CEDEAR_PROVIDERS.items():
            assert factory().name == name

    def test_source_url_is_exposed(self) -> None:
        """Every stored row must point back at the document it came from."""
        for name in CEDEAR_PROVIDERS:
            assert resolve_cedear_provider(name).source_url.startswith("https://")


class TestRecordContracts:
    """Properties the rest of the platform relies on."""

    def test_market_is_derived_from_the_raw_text(self) -> None:
        """The normalised value must follow from what the source published."""
        record = CedearRecord(symbol="SPY", custodian="comafi", underlying_market_raw="NYSE ARCA")
        assert record.underlying_market is UnderlyingMarket.NYSE_ARCA

    def test_unmapped_market_does_not_raise(self) -> None:
        """An unrecognised venue is data, not a crash."""
        record = CedearRecord(symbol="X", custodian="comafi", underlying_market_raw="Idustrial")
        assert record.underlying_market is UnderlyingMarket.UNKNOWN

    def test_tradeable_reference_requires_an_underlying(self) -> None:
        """Without an underlying there is no price series to link to.

        Treating a missing underlying as a same-named local listing would attach
        a CEDEAR to an unrelated series.
        """
        assert CedearRecord(symbol="X", custodian="comafi").is_tradeable_reference is False
        assert (
            CedearRecord(symbol="X", custodian="comafi", underlying_symbol="Y")
        ).is_tradeable_reference
        # An empty string is not a reference either.
        assert (
            CedearRecord(
                symbol="X", custodian="comafi", underlying_symbol=""
            ).is_tradeable_reference
            is False
        )

    def test_ratio_is_a_decimal_not_a_float(self) -> None:
        """``1:5`` is ``0.2`` exactly in decimal, and not in binary float."""
        record = CedearRecord(symbol="X", custodian="comafi", ratio=Decimal("0.2"))
        assert isinstance(record.ratio, Decimal)
        assert str(record.ratio) == "0.2"

    def test_rejection_is_part_of_the_snapshot_interface(self) -> None:
        """A snapshot that cannot report rejections is not a valid snapshot."""
        rejection = RejectedRecord(provider="comafi", reason="bad", detail="why")
        assert rejection.as_dict() == {
            "provider": "comafi",
            "symbol": None,
            "reason": "bad",
            "detail": "why",
        }

    def test_snapshot_indexing(self) -> None:
        """Callers reconcile by symbol, so the index must be total."""
        snapshot = CedearSnapshot(
            provider="comafi",
            source_url="https://example.test",
            fetched_at=datetime(2026, 3, 1, tzinfo=UTC),
            records=(
                CedearRecord(symbol="AAPL", custodian="comafi"),
                CedearRecord(symbol="VALE3", custodian="comafi"),
            ),
        )
        assert set(snapshot.by_symbol()) == {"AAPL", "VALE3"}


class TestProviderErrorMeaning:
    """:class:`ProviderError` gates deactivation, so it must stay distinct."""

    def test_it_is_not_a_value_error(self) -> None:
        """A per-record problem is a rejection; a fetch problem is an error."""
        assert not issubclass(ProviderError, ValueError)

    def test_it_is_a_runtime_error(self) -> None:
        """Adapters raise it without importing anything else from the app."""
        assert issubclass(ProviderError, RuntimeError)


class TestUnderlyingProviderInterface:
    """Phase 3 implements this; Phase 2 only fixes the shape."""

    def test_column_contract_is_declared(self) -> None:
        """Every price source must produce the same columns, in this order."""
        assert UNDERLYING_BAR_COLUMNS[:4] == ("symbol", "market_date", "timestamp", "open")
        assert "adjusted_close" in UNDERLYING_BAR_COLUMNS
        assert "traded_value" in UNDERLYING_BAR_COLUMNS

    def test_incomplete_implementation_cannot_be_instantiated(self) -> None:
        """The interface must be enforced before a vendor is chosen."""
        with pytest.raises(TypeError):
            UnderlyingDataProvider()  # type: ignore[abstract]

    def test_a_conforming_implementation_is_accepted(self) -> None:
        """The signature must be implementable, or Phase 3 starts with a fight."""

        class Stub(UnderlyingDataProvider):
            name = "stub"

            def fetch_daily_bars(
                self, symbol: str, market: UnderlyingMarket, start: date, end: date
            ) -> BarSnapshot:
                bar = DailyBar(
                    symbol=symbol,
                    market_date=start,
                    timestamp=datetime.combine(start, datetime.min.time(), tzinfo=UTC),
                )
                return BarSnapshot(
                    provider=self.name,
                    source_url="https://stub.test",
                    fetched_at=datetime.now(UTC),
                    bars=(bar,),
                )

        assert Stub().name == "stub"
