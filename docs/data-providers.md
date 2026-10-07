# Data providers

## Principle

The application never imports a vendor SDK directly. It depends on **interfaces** defined
inside StockGambling, and vendors are selected at runtime through settings.

```
app/providers/
├── base.py            # CedearDataProvider contract, CedearRecord, CedearSnapshot,
│                      # clean_isin, is_missing_market, is_tradeable_reference
├── http.py            # shared httpx fetch + HTML decoding helpers
├── registry.py        # name -> implementation, resolved from settings
└── implementations/   # one package per vendor (comafi, cajadevalores, ...)
```

A provider is chosen with environment variables, never by editing application code:

```bash
# Comma-separated list, or unset for the default (the union of every provider).
CEDEAR_METADATA_PROVIDER=comafi,cajadevalores
```

Credentials are read from the environment at call time, are never logged, are never
written to the database, and are never committed.

---

## CEDEAR metadata sources

Two authorities are relevant and neither is complete on its own:

| Source            | Publishes                                                        |
| ----------------- | -------------------------------------------------------------- |
| **COMAFI**        | The programs its home-banking BCR page sponsors: symbol, underlying, CEDEAR type (stock / ETF), market, **conversion ratio**, and program status (`active` / `inactive`). |
| **Caja de Valores** | The CEDEARs it issues, with the underlying ticker and ratio, and the **ISIN** both sides. |

The two lists are **disjoint**: COMAFI does not publish every issued CEDEAR and Caja de
Valores does not list every program COMAFI banks. There is no single official source
covering the whole universe, so the **default ingestion runs both providers and unites
their records**. A CEDEAR seen by only one source is still ingested with that source's
data; the fields each source does not publish are left `null` (never fabricated).

**Ratios are never hardcoded.** They are ingested and stored with an effective date, and a
re-ingest that discovers a different ratio opens a new `instrument_ratio_history` period
rather than overwriting the old one. Periods are half-open (`[effective_from, effective_to)`),
so a query for a historical date can be answered from the table instead of assumed to be the
current ratio.

The universe changes over time. Ingestion is an **upsert** keyed on the CEDEAR symbol, and
`instruments.is_active` is refreshed on every run so delisted instruments stop appearing in
the API without losing their price history. Deactivation is conservative: it only happens
when every configured source ran successfully, and never below a `0.5` retention floor.

### Record format

```python
@dataclass(frozen=True)
class CedearRecord:
    symbol
    custodian                # "comafi" | "cajadevalores" | ...
    name
    instrument_type          # STOCK | ETF | OTHER | UNKNOWN
    underlying_symbol
    underlying_market_raw    # exchange as the source prints it
    ratio                    # Decimal | None - None when unparsable
    program_status           # ACTIVE | INACTIVE | UNKNOWN
    source_ref               # the URL the record was read from
    isin / underlying_isin   # when the source publishes them
    multi_local              # raw ratio string, kept in attributes
```

The field names are fixed by the domain, not by the vendor; the adapter translates.

### Adapter quirks (why the code looks the way it does)

* **COMAFI.** The BCR page mixes CEDEARs and other instruments; rows whose underlying is a
  foreign listing are filtered out and only records with established underlying tickers are
  traded. The ratio is printed in the `N:D` form; it is parsed and stored as the numeric
  `N` (CEDEARs per underlying unit). Program status is misspelled on the page
  ("Inhablitado") and is matched by marker, not by equality.
* **Caja de Valores.** The XLSX export is a fixed two-sheet report; the parser validates the
  sheet headers before reading. Records are deduplicated away: the page lists a CEDEAR once
  per issuing plan (`SI` / `SICPQ`), and the second occurrence of the same CEDEAR is dropped.
  ISINs are normalised with `clean_isin` (uppercase, dashes removed).
* HTML is decoded with an explicit UTF-8-then-Latin-1 fallback; the declared charset of an
  Argentine institutional page is not trusted.
* A value that means "missing" on a page - an empty ratio, a placeholder like `"none"` - is
  `null`, and **is not** recorded as a warning. A value that is present but malformed *is* a
  warning. The distinction matters because missing-market programs are the rule, not the
  exception, and a warning per placeholder would drown real problems.

## Market-data interfaces (Phase 3, implemented)

Vendor-neutral vocabulary for OHLCV and FX; one vendor is selected per series and a
provider is swapped by environment variable, never by editing code:

```bash
# One vendor per series; unset falls back to "yahoo".
UNDERLYING_PRICE_PROVIDER=yahoo
LOCAL_PRICE_PROVIDER=yahoo
FX_PROVIDER=yahoo
# Default currency pair, the rate the platform prices everything in (ARS per USD).
FX_DEFAULT_PAIR=USDARS
# Window a backfill job covers, in calendar days.
MARKET_DATA_BACKFILL_DAYS=60
```

| Interface                | Responsibility                                                          |
| ------------------------ | ----------------------------------------------------------------------- |
| `UnderlyingDataProvider` | OHLCV for a foreign symbol, in the **underlying's** timezone.            |
| `LocalPriceDataProvider` | OHLCV for a CEDEAR as quoted on BYMA, in ARS.                            |
| `FXDataProvider`         | An FX series (default USD/ARS) with an explicit market calendar.         |
| `FundamentalsProvider`   | Point-in-time fundamentals keyed by *availability* date (Phase 18).     |
| `NewsProvider`           | Timestamped news (Phase 19).                                            |

Every method must return a provider-neutral frame. The vocabulary is fixed by the domain,
not by the vendor:

```
symbol, market_date, timestamp (UTC, aware), open, high, low, close,
adjusted_close, volume, traded_value, trades, currency, source
```

### Yahoo Finance (the Phase 3 vendor)

One chart endpoint (`https://query1.finance.yahoo.com/v8/finance/chart`) serves all three
modes with no API key. The wire ticker is derived from the platform's own vocabulary in
`vendor_symbol` (US venues carry no suffix; `VALE3.SA`, `SIE.DE`, `ULVR.L`, a CEDEAR
local quote is `AAPL.BA`, a pair is `USDARS=X`). Yahoo does **not** publish traded value
or trade counts, so those fields are stored `null` - the platform must not invent a
turnover it did not receive. A calendar slot with no trade is rejected
(`reason: no_trade_slot`) into the run's audit trail, never stored as `0.0`.

### Quiet days and layout changes

* A `null`/`NaN` bar field is a missing observation, never zero.
* Misaligned timestamp/quote arrays abort with `ProviderError`: the payload shape changed
  and zipping silently would corrupt history.
* A `chart.error` (e.g. a delisted symbol) is a `ProviderError`: routing a different ticker
  would be a guess.

Every method must return a provider-neutral frame. The vocabulary is fixed by the domain,
not by the vendor:

```
symbol, market_date, timestamp (UTC, aware), open, high, low, close,
adjusted_close, volume, traded_value, trades, currency, source
```

### Rules every implementation must honour

1. **Timestamps are timezone-aware UTC.** A provider that returns naive local times must
   be rejected in its adapter, not silently reinterpreted.
2. **Market dates are the provider's own local dates**, preserved, not derived from UTC.
3. **Adjustments are explicit.** `adjusted_close` must be adjusted close, not close. If
   the vendor does not provide it, the field is `null` and downstream code must not
   substitute `close`.
4. **`source` is always populated**, so a later vendor change never rewrites history
   ambiguously.
5. **No silent forward-fill.** Gaps stay gaps. Alignment happens in one place, under
   test, using the as-of rule below.

---

## As-of rule (the anti-leakage contract)

Joining a BYMA series to a US series is the single most dangerous operation in this
project. The rule is:

> For a prediction made at instant `T` in the Argentine market, use the most recent
> underlying observation whose underlying-market timestamp is **strictly before the
> last completed underlying session that had ended before `T`**.

Concretely: a BYMA close at 18:00 ART on day *D* happens **before** the US close on day
*D*. So the same-day US close is *future* information and must not be used. The underlying
series is therefore lagged by one completed session for a `1d` horizon, and the leakage
tests in Phase 7 assert exactly that.

The same rule applies to FX: only FX observations published before `T` are eligible.

The implementation lives in `app/alignment/asof.py`, one module, so there is one place to
audit, one place to test, and one place for a bug to hide. Its exact semantics:

* `select_underlying_bar(bars, T, market_tz, lag_sessions=1)` — a session on market date
  `m` is eligible only when `m` is **strictly before** the prediction's local date in the
  underlying market's timezone, and the bar's own instant is strictly before `T`. The
  default `lag_sessions=1` is the `1d` horizon value: the most recent completed session.
  Returns `None` (never the nearest past bar) when no eligible session exists.
* `select_fx_observation(obs, T)` — the most recent observation whose timestamp is
  **strictly before** `T`; no lag. An observation stamped exactly `T` is ineligible, so
  the cutoff is testable to the instant.
* Both refuse naive datetimes via `app/core/time.ensure_utc`.

---

## Adding a new provider

1. Implement the relevant interface in `app/providers/implementations/<vendor>/`.
2. Translate the vendor payload into the provider-neutral vocabulary. Adapters are where
   timezone and column-name mistakes get introduced, so adapters carry their own tests.
3. Reject naive timestamps explicitly.
4. Register the implementation in `app/providers/registry.py`.
5. Set the corresponding `*_PROVIDER` environment variable.
6. Add a fixture-based test asserting the adapter's output vocabulary and that a known
   record round-trips unchanged.

No change to the database schema, the feature builders, the models or the API is required.
That is the point of the abstraction.
