"""Resolution of duplicate symbols within a single source snapshot.

Duplicates are not hypothetical. The COMAFI catalogue publishes two records for
``WDC`` on the same day: one carrying ``ratio = 92:1`` with the correct
underlying ticker, and a stub with no ratio, no ISIN, and the *wrong* ticker
and sector. A naive "last one wins" or "first one wins" silently corrupts the
instrument depending on dictionary ordering.

The rule applied here is therefore never "pick one arbitrarily":

* exactly one record carries a ratio -> that record wins, because a record
  without a ratio cannot be the truth about a conversion ratio;
* several carry the same ratio -> the most complete record wins;
* several carry *different* ratios -> **all** of them are rejected. Picking one
  would fabricate a fact, and the conflict needs a human.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Sequence

from app.providers.base import CedearRecord, RejectedRecord

#: Fields that make one record more complete than another for the same symbol.
_COMPLETENESS_FIELDS: tuple[str, ...] = (
    "name",
    "underlying_symbol",
    "underlying_market_raw",
    "underlying_isin",
    "isin",
    "program_status_raw",
    "source_ref",
)


def _completeness(record: CedearRecord) -> int:
    """Count how many optional fields a record actually populated."""
    return sum(1 for field_name in _COMPLETENESS_FIELDS if getattr(record, field_name))


def _raw_summary(record: CedearRecord) -> dict[str, object]:
    """A small, JSON-safe excerpt of a record for the audit trail."""
    return {
        "source_ref": record.source_ref,
        "ratio": str(record.ratio) if record.ratio is not None else None,
        "underlying_symbol": record.underlying_symbol,
        "extra": {key: record.extra[key] for key in sorted(record.extra)},
    }


def resolve_duplicates(
    provider: str,
    records: Iterable[CedearRecord],
) -> tuple[list[CedearRecord], list[RejectedRecord]]:
    """Collapse records that share a symbol.

    Args:
        provider: Name of the source, used in the rejection audit entries.
        records: Every record parsed from one snapshot, in source order.

    Returns:
        The surviving records and the rejections, both in source order.
    """
    grouped: dict[str, list[CedearRecord]] = defaultdict(list)
    order: list[str] = []
    for record in records:
        if record.symbol not in grouped:
            order.append(record.symbol)
        grouped[record.symbol].append(record)

    kept: list[CedearRecord] = []
    rejected: list[RejectedRecord] = []

    for symbol in order:
        group = grouped[symbol]
        if len(group) == 1:
            kept.append(group[0])
            continue

        with_ratio = [record for record in group if record.ratio is not None]
        distinct = {record.ratio for record in with_ratio}

        if len(distinct) > 1:
            rejected.extend(
                RejectedRecord(
                    provider=provider,
                    symbol=symbol,
                    reason="conflicting_ratios",
                    detail=(
                        f"{len(group)} records for {symbol} publish "
                        f"{len(distinct)} different conversion ratios; "
                        "none can be trusted without a human decision"
                    ),
                    raw=_raw_summary(group[0]),
                )
                for _ in group
            )
            continue

        if len(with_ratio) == 1:
            winner = with_ratio[0]
            reason = "superseded_by_ratio_bearing_record"
        else:
            winner = max(group, key=_completeness)
            reason = "duplicate_record"

        kept.append(winner)
        for record in group:
            if record is winner:
                continue
            rejected.append(
                RejectedRecord(
                    provider=provider,
                    symbol=symbol,
                    reason=reason,
                    detail=(
                        f"kept {winner.source_ref or 'record without source_ref'} "
                        f"for {symbol}; discarded {record.source_ref or 'record without source_ref'}"
                    ),
                    raw=_raw_summary(record),
                )
            )

    return kept, rejected


def ensure_unique(
    records: Sequence[CedearRecord],
) -> list[str]:
    """Return the symbols that appear more than once, for diagnostics."""
    counts: dict[str, int] = defaultdict(int)
    for record in records:
        counts[record.symbol] += 1
    return sorted(symbol for symbol, count in counts.items() if count > 1)
