"""CEDEAR metadata ingestion.

Two modules, one direction of dependency:

* :mod:`app.ingestion.reconcile` is pure. It takes provider snapshots and
  returns a merged universe plus a quarantine list. No database, no I/O, so the
  reconciliation rules are testable without PostgreSQL.
* :mod:`app.ingestion.service` owns the write path: upserting instruments,
  opening and closing ratio periods, deactivating what disappeared, and
  recording the run for audit.
* :mod:`app.ingestion.theoretical` computes and stores theoretical CEDEAR prices.
"""

from app.ingestion.reconcile import (
    MergedInstrument,
    MergedUniverse,
    merge_snapshots,
)
from app.ingestion.service import (
    JOB_NAME,
    IngestionReport,
    fetch_and_ingest,
    open_ratio_period,
    ratio_effective_on,
    run_metadata_ingestion,
)
from app.ingestion.theoretical import (
    JOB_NAME as THEORETICAL_JOB_NAME,
)
from app.ingestion.theoretical import (
    TheoreticalIngestionReport,
    refresh_instrument_theoretical,
    run_theoretical_ingestion,
)

__all__ = [
    "JOB_NAME",
    "THEORETICAL_JOB_NAME",
    "IngestionReport",
    "MergedInstrument",
    "MergedUniverse",
    "TheoreticalIngestionReport",
    "fetch_and_ingest",
    "merge_snapshots",
    "open_ratio_period",
    "ratio_effective_on",
    "refresh_instrument_theoretical",
    "run_metadata_ingestion",
    "run_theoretical_ingestion",
]
