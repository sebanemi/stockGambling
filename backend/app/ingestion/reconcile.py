"""Cross-provider reconciliation of the CEDEAR universe.

Two official sources describe the same instrument and neither is a superset of
the other: COMAFI covers the programs it sponsors, Caja de Valores the ones it
issues, and the foreign-company-sponsored programs appear in neither. The
ingestion service therefore has to *reconcile* two snapshots before touching
the database, and the reconciliation rules are the interesting part of Phase 2.

**The rules, and why each one exists.**

1. *Union, not intersection.* A symbol present in only one snapshot is kept.
   Dropping it would make the universe smaller than any source says it is.
2. *A field is filled from the first source that publishes it.* Fields are
   merged independently, so a program whose status only COMAFI states and whose
   ratio only Caja de Valores states ends up with both, rather than with
   whichever record happened to sort first.
3. *A conflict on the ratio rejects the ratio, never picks a side.* If two
   sources publish different ratios for the same symbol, one of them is wrong
   and the platform cannot tell which. The instrument is still ingested - the
   symbol, the underlying and the venue are not in doubt - but the ratio is
   left unstored and the conflict is quarantined for a human. Guessing here
   would corrupt every theoretical price computed from it.
4. *A conflict on the underlying symbol rejects the underlying.* Same argument:
   two different underlyings for one CEDEAR means the mapping is not known, and
   a CEDEAR with a wrong underlying is worse than one with no underlying.
5. *Nothing is filled in from a symbol's own name.* ``BPA11`` and ``BPAC11``
   look related and are not interchangeable; ``SI`` wraps a delisted
   ``SICPQ``. The mapping comes from the source or not at all.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Final, TypeVar, cast

from app.core.logging import get_logger
from app.providers.base import CedearRecord, CedearSnapshot, RejectedRecord

logger = get_logger(__name__)

_T = TypeVar("_T")

#: Sentinel distinguishing "no source published this field" from a published
#: ``None``.
_MISSING: Final[object] = object()

#: Sentinel for a field the sources disagreed about, which is left unset.
_CLEARED: Final[object] = object()

#: Fields merged field-by-field, in provider order. ``symbol``, ``custodian``
#: and ``source_ref`` are *not* here: the symbol is the merge key, the
#: custodian records the winning source, and ``source_ref`` must point at the
#: record the stored row actually came from.
_MERGED_FIELDS: tuple[str, ...] = (
    "name",
    "instrument_type",
    "underlying_symbol",
    "underlying_name",
    "underlying_market_raw",
    "underlying_isin",
    "isin",
    "ratio",
    "program_status",
    "program_status_raw",
)

#: Fields where disagreement between sources is treated as a data-quality
#: incident rather than resolved by precedence.
_CONFLICTING_FIELDS: frozenset[str] = frozenset({"ratio", "underlying_symbol"})

#: Defaults that carry no information. Treating them as "absent" is what lets a
#: partial record contribute its good fields without overwriting good data from
#: another source with nothing.
_UNSET_VALUES: dict[str, object] = {
    "name": None,
    "instrument_type": "UNKNOWN",
    "underlying_symbol": None,
    "underlying_name": None,
    "underlying_market_raw": None,
    "underlying_isin": None,
    "isin": None,
    "ratio": None,
    "program_status": "UNKNOWN",
    "program_status_raw": None,
}


def _is_unset(field_name: str, value: object) -> bool:
    """Whether ``value`` carries no information for ``field_name``."""
    if value is None:
        return True
    expected = _UNSET_VALUES[field_name]
    if isinstance(expected, str) and isinstance(value, str):
        return value == expected
    return False


@dataclass(frozen=True, slots=True)
class MergedInstrument:
    """One instrument after reconciling every source that lists it."""

    record: CedearRecord
    #: Provider names that listed this symbol, in reconciliation order.
    sources: tuple[str, ...]
    #: Provider whose record supplied the stored ``source_ref``.
    primary_source: str


@dataclass(frozen=True, slots=True)
class MergedUniverse:
    """The reconciled universe plus everything that could not be trusted."""

    instruments: tuple[MergedInstrument, ...]
    rejected: tuple[RejectedRecord, ...]
    warnings: tuple[str, ...]

    def __len__(self) -> int:
        """Number of instruments in the merged universe."""
        return len(self.instruments)

    def by_symbol(self) -> dict[str, MergedInstrument]:
        """Index the merged instruments by CEDEAR symbol."""
        return {item.record.symbol: item for item in self.instruments}


def _conflict_rejection(
    symbol: str,
    field_name: str,
    values: Sequence[tuple[str, object]],
) -> RejectedRecord:
    """Build the quarantine entry for a field the sources disagree about."""
    rendered = ", ".join(f"{source}={value}" for source, value in values)
    return RejectedRecord(
        provider="merge",
        symbol=symbol,
        reason=f"conflicting_{field_name}",
        detail=(
            f"sources disagree on {field_name} for {symbol}: {rendered}. "
            f"The field is left unset rather than resolved by precedence, because a "
            f"wrong {field_name} silently corrupts every downstream price."
        ),
        raw={source: str(value) for source, value in values},
    )


def merge_snapshots(snapshots: Iterable[CedearSnapshot]) -> MergedUniverse:
    """Reconcile provider snapshots into a single universe.

    Args:
        snapshots: The snapshots to reconcile. Order matters only for
            provenance: the first source that publishes a field wins it, and the
            ``source_ref`` recorded on the row is the winning record's.

    Returns:
        The merged instruments, the records that were quarantined, and the
        non-fatal warnings. A snapshot from a provider that failed outright is
        simply absent, which is why the caller must refuse to deactivate
        anything when not every provider succeeded.
    """
    by_symbol: dict[str, list[tuple[str, CedearRecord]]] = defaultdict(list)
    order: list[str] = []
    rejected: list[RejectedRecord] = []
    warnings: list[str] = []

    materialised = list(snapshots)
    for snapshot in materialised:
        warnings.extend(f"{snapshot.provider}: {warning}" for warning in snapshot.warnings)
        rejected.extend(snapshot.rejected)
        for record in snapshot.records:
            if record.symbol not in by_symbol:
                order.append(record.symbol)
            by_symbol[record.symbol].append((snapshot.provider, record))

    instruments: list[MergedInstrument] = []
    for symbol in order:
        entries = by_symbol[symbol]
        merged, entry_rejections = _merge_symbol(symbol, entries)
        rejected.extend(entry_rejections)
        instruments.append(merged)

    logger.info(
        "merge.completed",
        inputs=len(materialised),
        instruments=len(instruments),
        rejected=len(rejected),
    )

    return MergedUniverse(
        instruments=tuple(instruments),
        rejected=tuple(rejected),
        warnings=tuple(warnings),
    )


def _merge_symbol(
    symbol: str, entries: list[tuple[str, CedearRecord]]
) -> tuple[MergedInstrument, list[RejectedRecord]]:
    """Reconcile every record published for one symbol.

    Each field is resolved independently: the first source that publishes a
    value wins it, and a value nobody publishes keeps the primary record's. The
    two fields listed in ``_CONFLICTING_FIELDS`` are the exception - see the
    module docstring.
    """
    primary_source, primary = entries[0]
    sources: list[str] = [primary_source]
    rejected: list[RejectedRecord] = []

    #: field name -> (first value seen, [source, value] pairs)
    winners: dict[str, tuple[object, list[tuple[str, object]]]] = {}

    for source, record in entries:
        if source not in sources:
            sources.append(source)
        for field_name in _MERGED_FIELDS:
            value = getattr(record, field_name)
            if _is_unset(field_name, value):
                continue
            seen = winners.setdefault(field_name, (value, []))
            seen[1].append((source, value))

    resolved: dict[str, object] = {}
    for field_name, (first, published) in winners.items():
        if len({value for _source, value in published}) > 1 and field_name in _CONFLICTING_FIELDS:
            rejected.append(_conflict_rejection(symbol, field_name, published))
            # Cleared, not merely absent. Falling back to the primary record here
            # would smuggle one side of the dispute back in as if it had been
            # agreed, which is exactly the guess the rule forbids.
            resolved[field_name] = _CLEARED
            continue
        resolved[field_name] = first

    extras: dict[str, Any] = {}
    for source, record in entries:
        for key, value in record.extra.items():
            extras.setdefault(f"{source}.{key}", value)

    merged = CedearRecord(
        symbol=symbol,
        custodian=primary_source,
        source_ref=primary.source_ref,
        name=_pick(resolved, "name", primary.name),
        instrument_type=_pick(resolved, "instrument_type", primary.instrument_type),
        underlying_symbol=_pick(resolved, "underlying_symbol", primary.underlying_symbol),
        underlying_name=_pick(resolved, "underlying_name", primary.underlying_name),
        underlying_market_raw=_pick(
            resolved, "underlying_market_raw", primary.underlying_market_raw
        ),
        underlying_isin=_pick(resolved, "underlying_isin", primary.underlying_isin),
        isin=_pick(resolved, "isin", primary.isin),
        ratio=_pick(resolved, "ratio", primary.ratio),
        program_status=_pick(resolved, "program_status", primary.program_status),
        program_status_raw=_pick(resolved, "program_status_raw", primary.program_status_raw),
        extra=extras,
    )

    return (
        MergedInstrument(record=merged, sources=tuple(sources), primary_source=primary_source),
        rejected,
    )


def _pick(resolved: dict[str, object], field_name: str, fallback: _T) -> _T:
    """Return the reconciled value for a field.

    Falls back to the primary record's value only when *no* source published the
    field at all. A field cleared because the sources disagreed resolves to
    ``None``: the primary record's value is a side of the dispute, not a default.
    Typed per-field rather than through ``dataclasses.replace`` so the compiler
    still checks each constructor argument against :class:`CedearRecord`.
    """
    value = resolved.get(field_name, _MISSING)
    if value is _MISSING:
        return fallback
    if value is _CLEARED:
        return cast(_T, None)
    return cast(_T, value)


def ratio_is_usable(record: CedearRecord) -> bool:
    """Whether a record's ratio can be written to the ratio history.

    Re-exported here so the ingestion service has a single import for the
    question "is this ratio trustworthy enough to open a period with?".
    """
    return isinstance(record.ratio, Decimal) and record.ratio > 0


__all__ = [
    "MergedInstrument",
    "MergedUniverse",
    "merge_snapshots",
    "ratio_is_usable",
]
