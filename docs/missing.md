# Everything Missing — Project Audit (Oct 2026, updated: Phase 5 closed)

This document lists every gap, stub, approximation, missing endpoint, missing test, and missing module found across the repository after Phase 5 feature-engineering work.

> **2026-10-07 update:** Phase 5 is **Done** (see `docs/phases.md`). Section 1
> below records what the closing commit fixed; sections 2+ (Phases 6-10) are
> unchanged and remain the roadmap.

---

## 1. Phase 5 — Feature Engineering (Done)

### 1.1 Feature Definitions — Complete
All 50 feature definitions are registered (cedear_technical, underlying_technical, fx, relative, market). No missing definitions.

### 1.2 Feature Computation — Complete
- Shared maths live in `app/features/indicators.py`: Wilder's RSI/RMA, MACD signal as the 9-period EMA of the MACD line itself, Wilder's ATR, annualised rolling volatility.
- `relative_features.py` enforces the as-of contract (local `<= D`, underlying strictly `< D` one-session lag, FX `<= D` no lag) and compounds `expected_cedear_return` as `(1 + r_u) * (1 + r_fx) - 1`.
- `market_features.py`: only `merval_return_1d` has DB backing; all other market features return `None` until a provider/table exists. Documented, never fabricated.

### 1.3 Feature Store (`FeatureStore`) — Complete
- ORM model, writer/reader with `write_feature_snapshots_bulk`, pagination (`limit`/`offset`), market-date range filters and `count_feature_snapshots`.
- History indexes via `0005_feature_store_indexes.py`, mirrored in the ORM model.
- `feature_values_json` stays a `String` column (portable across backends); decoding is centralised in `decode_feature_values`.

### 1.4 Feature Registry (`FeatureRegistry`) — Complete
- In-memory `FeatureRegistry` + `get_registered_version()`; the version string is stamped on every snapshot and worker report. No separate version table by design (the registry source is the code).

### 1.5 Feature Tests — Complete
- `tests/test_features.py`: registry checks, no-session stubs, indicator maths (RSI bounds/100/flat/window, MACD signal identity, ATR, volatility).
- `tests/test_feature_store.py`: write/read round-trip, bulk + pagination, range filter, empty-payload decode.
- `tests/test_leakage.py` (`-m leakage`): same-day underlying invisibility, prediction-date bar exclusion, future-FX invisibility, naive-datetime rejection.
- `tests/test_api_features.py`: live snapshot provenance + stored-history paging.

### 1.6 Feature API Endpoints — Complete
- `GET /api/v1/cedears/{symbol}/features` (live, read-only) and `GET /api/v1/cedears/{symbol}/features/snapshots` (stored history).
- Router phase string is `"5-feature-engineering"`.

### 1.7 Feature Worker Tasks — Complete
- `feature.build` Celery task (`app/workers/tasks/features.py`): computes via `compute_all_features`, persists snapshots, per-symbol warnings, skips all-`None` vectors.

---

## 2. Phase 6 — Baseline Models (Not Started)

### 2.1 Model Interface — Missing
- No `app/models/models.py` or `app/domain/models.py` with `PredictionModel` interface (`fit`, `predict`, `predict_proba`, `save`, `load`, `get_metadata`).
- No `models` table (`sg_models`) or `model_runs` table (`sg_model_runs`).
- No `experiment` table (`sg_experiments`).

### 2.2 Baseline Algorithms — Missing
- No `app/models/baselines.py` or `app/features/baselines.py`.
- No majority-class baseline, random baseline, logistic regression, or XGBoost model implementations.
- No model registry module.

### 2.3 Serialization / Round-trip Tests — Missing
- No `tests/test_model_serialization.py`.

---

## 3. Phase 7 — Walk-Forward Evaluation (Not Started)

### 3.1 Chronological Split Utility — Missing
- No `shuffle=True` lint check (would require a custom ruff plugin or pytest plugin). Not implemented.
- No `app/evaluation/splits.py` or similar module for chronological splits.

### 3.2 Walk-Forward Folds — Missing
- No `walk_forward` function or module.
- No `tests/test_walk_forward.py`.

### 3.3 Classification Metrics — Missing
- No `app/evaluation/metrics.py` or `app/metrics/`.
- No accuracy, balanced accuracy, precision, recall, F1, ROC-AUC, log loss, Brier score implementations.
- No `tests/test_metrics.py`.

### 3.4 Leakage Tests — Missing
- No `tests/test_leakage.py` (zero tests use `-m leakage`).
- No leakage detection module (`app/leakage/` or `app/evaluation/leakage.py`).
- No tests for future underlying prices, future FX, future volume, future news, revised fundamentals, future conversion ratios, timezone errors, dataset-wide normalisation, or shuffled splits.

---

## 4. Phase 8 — CEDEAR Backtesting (Not Started)

### 4.1 Portfolio Simulation — Missing
- No `app/backtesting/portfolio.py` or similar.
- No `sg_backtests` table or model.
- No `backtest` endpoint (`POST /api/v1/backtests`, `GET /api/v1/backtests/{backtest_id}`).

### 4.2 Transaction Costs / Slippage — Missing
- No `app/backtesting/costs.py` or configurable cost model.
- Default costs are not set to non-zero anywhere.

### 4.3 Benchmarks — Missing
- No buy-and-hold CEDEAR benchmark implementation.
- No cash / no-trade benchmark.
- No underlying-equivalent benchmark.

### 4.4 Backtest Metrics — Missing
- No `total_return`, `annualised_return`, `volatility`, `sharpe`, `max_drawdown`, `win_rate`, `trade_count`, `average_trade`, `profit_factor`, `total_costs` calculations.
- No separate ARS and USD-adjusted return reporting module.

---

## 5. Phase 9 — Prediction API (Not Started)

### 5.1 Prediction Endpoint — Missing
- `GET /api/v1/cedears/{symbol}/prediction` does not exist.
- No probability output (`probability_up`, `probability_down`).
- No response envelope (`probability_up + probability_down == 1` constraint not enforced).

### 5.2 Model / Experiment Endpoints — Missing
- `GET /api/v1/models` and `GET /api/v1/models/{model_id}` not implemented.
- `GET /api/v1/experiments` not implemented.
- `POST /api/v1/backtests` and `GET /api/v1/backtests/{backtest_id}` not implemented.
- No Pydantic models for these responses.

### 5.3 Feature Endpoint — Missing (Phase 5 dependency)
- `GET /api/v1/cedears/{symbol}/features` not implemented.
- No feature history endpoint (`history`, `underlying`, `fx`, `theoretical-price` also missing from endpoint list, though `cedears.py` may handle some).

---

## 6. Phase 10 — Frontend (Not Started)

### 6.1 Dashboard Pages — Partial
- `frontend/app/page.tsx` exists (infrastructure status dashboard).
- `frontend/app/api/health/` exists (health bridge).
- No `frontend/app/cedears/` page for CEDEAR search.
- No `frontend/app/instrument/[symbol]/` panel.
- No `frontend/app/features/` or `frontend/app/predictions/` pages.
- No `frontend/app/backtests/` or `frontend/app/experiments/` pages.

### 6.2 Charts / Visualisations — Missing
- No multi-market chart component (`local / underlying / USD/ARS`).
- No premium/discount chart.
- No prediction probability panel.
- No benchmark comparison chart.

---

## 7. General / Cross-Phase Gaps

### 7.1 Documentation Gaps (largely closed with Phase 5)
- `docs/phases.md`: Phase 5 marked **Done** with deliverables and exit criteria.
- `README.md`: status table shows Phase 5 **Done** with the feature/API/task scope.
- `docs/methodology.md`: still does not document feature engineering methodology (as-of rule for features, leakage prevention, feature version registry) — carried into Phase 6 prep.
- `docs/architecture.md`: references feature store (now exists); prediction API, models, backtests remain future phases.

### 7.2 Migration / Schema Gaps
- `0004_feature_store.py`: No index on `(feature_version, market_date)` for fast historical queries.
- `0004_feature_store.py`: Uses `String` for JSON instead of `JSONB` (as noted in model docstring: "JSON feature values would be stored in production; for Phase 5, the structure is established.").
- No migration for `sg_feature_versions` (feature version registry persistence).
- No migration for `sg_models` or `sg_model_runs`.
- No migration for `sg_experiments`.
- No migration for `sg_backtests`.

### 7.3 API / Router Gaps
- `app/api/v1/router.py`: Phase string is `"3-market-data"`. Should be updated.
- No feature router included (`cedears.py` exists but does not export feature routes).
- No `models.py`, `experiments.py`, `backtests.py`, `predictions.py` routers.

### 7.4 Core / Settings Gaps
- `app/core/config.py`: `market_data_backfill_days` exists but no feature-computation-specific settings (feature version, feature store retention, feature computation timeout).
- `app/core/time.py`: `MARKET_TIMEZONES` exists but no feature-computation time helpers (e.g., feature `as_of` validation).

### 7.5 Alignment / Ingestion Gaps
- `app/alignment/`: `asof.py` exists but no feature-alignment helper (e.g., aligning feature snapshots to prediction instants).
- `app/ingestion/`: `service.py`, `reconcile.py`, `prices.py`, `theoretical.py` exist. No `features.py` ingestion service or feature-computation ingestion pipeline.

### 7.6 Provider / Data Source Gaps
- `app/providers/`: Concrete implementations exist (`yahoo`, `comafi`, `cajadevalores`). No market-data provider for S&P 500, NASDAQ, Dow, Russell 2000, VIX, or sector ETF.
- `app/features/market_features.py`: Only `merval` has DB backing. Other market series have no provider or DB table.

### 7.7 Quality / Testing Gaps
- `ruff`: Feature import blocks were unformatted (fixed by `ruff --fix`).
- `mypy`: Feature module passes but `FeatureStore` uses `str | None` for JSON (should be `JSONB` ideally).
- `pytest -m leakage`: Zero tests collected (no `test_leakage.py`).
- `pytest -m integration`: Feature store integration test (`test_feature_store.py`) is skipped when PostgreSQL is unreachable.
- No test verifies that `compute_cedear_technical_features` never reads data from `market_date >= as_of`.
- No test verifies that `compute_relative_features` uses historical ratio, historical underlying price, and historical FX rate.

### 7.8 Security / Secrets Gaps
- `tests/test_secrets_hygiene.py`: Exists and passes. No missing security tests.
- `.env`: Exists with `POSTGRES_HOST`, `REDIS_HOST`, etc. No feature-specific secrets.

### 7.9 Docker / Infrastructure Gaps
- `docker-compose.yml`: Includes `postgres`, `redis`, `api`, `web`, `worker`. No additional feature-computation service needed.
- `Dockerfile`: Builds API image. Feature dependencies (`numpy`, `pandas`) are installed via `pyproject.toml`. No issues.

### 7.10 Frontend / Dashboard Gaps
- `frontend/app/page.tsx`: Only infrastructure status page.
- `frontend/lib/api.ts`: Only health check client. No feature API client, no model client, no prediction client.
- `frontend/app/api/health/`: Only health bridge.

---

## 8. Summary of What's Complete vs Missing

| Area | Status | Key Missing Items |
|---|---|---|
| Phase 5 Features (definitions) | Complete | None |
| Phase 5 Features (computation) | Complete | Market features beyond Merval await a provider/table (by design) |
| Phase 5 Feature Store | Complete | None |
| Phase 5 Feature Registry | Complete | None |
| Phase 5 Feature Tests | Complete | DB-backed runs need PostgreSQL (skip cleanly without it) |
| Phase 5 Feature API | Complete | None |
| Phase 5 Feature Worker | Complete | None |
| Phase 6 Baselines | Missing | Model interface, algorithms, registry |
| Phase 7 Walk-Forward | Missing | Split utility, folds, metrics, leakage tests |
| Phase 8 Backtesting | Missing | Portfolio sim, costs, benchmarks, metrics, endpoint |
| Phase 9 Prediction API | Missing | Prediction endpoint, probability envelope, model/experiment endpoints |
| Phase 10 Frontend | Missing | All feature/model/backtest UI pages |
| General | Partial | `docs/phases.md` updated; `README.md` phase string outdated; `docs/architecture.md` references missing modules |
