# Data providers

## Principle

The application never imports a vendor SDK directly. It depends on **interfaces** defined
inside StockGambling, and vendors are selected at runtime through settings.

```
app/providers/
├── base.py            # CedearDataProvider, UnderlyingDataProvider, FXDataProvider,
│                      # FundamentalsProvider, NewsProvider  (Phase 2-3)
├── registry.py        # name -> implementation, resolved from settings
└── implementations/   # one package per vendor (BYMA, Caja de Valores, ...)
```

A provider is chosen with environment variables, never by editing application code:

```bash
CEDEAR_METADATA_PROVIDER=byma
UNDERLYING_PROVIDER=<vendor>
FX_PROVIDER=<vendor>
```

Credentials are read from the environment at call time, are never logged, are never
written to the database, and are never committed.

---

## Interfaces (planned contract)

| Interface                | Responsibility                                                          |
| ------------------------ | ----------------------------------------------------------------------- |
| `CedearDataProvider`     | The CEDEAR universe: symbol, underlying, exchange, ratio, type, issuer, active flag. |
| `UnderlyingDataProvider` | OHLCV for a foreign symbol, in the **underlying's** timezone.           |
| `FXDataProvider`         | An FX series (default USD/ARS) with an explicit market calendar.        |
| `FundamentalsProvider`   | Point-in-time fundamentals keyed by *availability* date (Phase 18).     |
| `NewsProvider`           | Timestamped news (Phase 19).                                            |

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

## CEDEAR metadata sources

Two authorities are relevant and they are not interchangeable:

| Source                                  | Publishes                                                                 |
| --------------------------------------- | ------------------------------------------------------------------------- |
| **BYMA** (Mercado de Valores de Buenos Aires) | Listed CEDEARs, the underlying, and the **conversion ratio**.         |
| **Caja de Valores**                     | The CEDEAR list including the underlying ticker and its ratio.            |

The system prefers BYMA and uses Caja de Valores as a cross-check. **Ratios are never
hardcoded.** They are ingested and stored with an effective date, and a re-ingest that
discovers a different ratio opens a new `instrument_ratio_history` row rather than
overwriting the old one.

BYMA continues to add CEDEARs, so the universe changes over time. The ingestion job is an
**upsert** keyed on the CEDEAR symbol, and `instruments.is_active` is refreshed on every
run so delisted instruments stop appearing in the API without losing their price history.

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

The implementation lives in a single module so there is one place to audit, one place to
test, and one place for a bug to hide.

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
