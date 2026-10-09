/**
 * Typed access to the StockGambling FastAPI backend.
 *
 * Two base URLs exist on purpose:
 *  - `API_INTERNAL_URL` is used by server components and route handlers, which
 *    run inside the Compose network and must reach the API as `http://api:8000`.
 *  - `NEXT_PUBLIC_API_URL` is used by browser code, which can only reach the
 *    API through the published host port.
 *
 * Every fetcher returns an `ApiCallResult`: HTTP errors and unreachable
 * backends surface as honest "unavailable" panels, never as fabricated data.
 */

/** Base URL used from inside the container network. */
export const INTERNAL_API_URL =
  process.env.API_INTERNAL_URL?.replace(/\/$/, "") ?? "http://localhost:8000";

/** Base URL reachable from the user's browser. */
export const PUBLIC_API_URL =
  process.env.NEXT_PUBLIC_API_URL?.replace(/\/$/, "") ?? "http://localhost:8000";

export type HealthStatus = "ok" | "degraded" | "unavailable";

export interface ComponentHealth {
  status: HealthStatus;
  latency_ms: number;
  detail?: string | null;
}

export interface ReadinessReport {
  status: HealthStatus;
  service: string;
  version: string;
  environment: string;
  checks: Record<string, ComponentHealth>;
}

export interface ApiInfo {
  service: string;
  version: string;
  environment: string;
  api_prefix: string;
  docs_url: string | null;
  phase: string;
}

export interface ApiCallResult<T> {
  ok: boolean;
  data: T | null;
  error: string | null;
}

export interface PageEnvelope<T> {
  items: T[];
  total: number;
  limit: number;
  offset: number;
}

export interface CedearSummary {
  symbol: string;
  name: string | null;
  instrument_type: string;
  is_active: boolean;
  underlying_symbol: string | null;
  underlying_name: string | null;
  underlying_market: string | null;
  underlying_market_raw: string | null;
  underlying_isin: string | null;
  isin: string | null;
  custodian: string | null;
  program_status: string;
  current_ratio: string | null;
  current_ratio_formatted: string | null;
  ratio_effective_from: string | null;
  ratio_effective_to: string | null;
  ratio_source: string | null;
  first_seen_at: string;
  last_seen_at: string;
}

export type CedearList = PageEnvelope<CedearSummary>;

export interface RatioPeriod {
  ratio: string;
  ratio_formatted: string;
  effective_from: string;
  effective_to: string | null;
  source: string;
  source_ref: string | null;
}

export interface CedearDetail extends CedearSummary {
  program_status_raw: string | null;
  ratio_history: RatioPeriod[];
  attributes: Record<string, unknown>;
}

export interface PriceBar {
  symbol: string;
  market_date: string;
  timestamp: string;
  open: string | null;
  high: string | null;
  low: string | null;
  close: string | null;
  adjusted_close: string | null;
  volume: number | null;
  traded_value: string | null;
  trades: number | null;
  currency: string;
  source: string;
  source_ref: string | null;
}

export type LocalHistory = PageEnvelope<PriceBar>;
export type UnderlyingHistory = PageEnvelope<PriceBar>;

export interface FxBar {
  pair: string;
  market_date: string;
  timestamp: string;
  open: string | null;
  high: string | null;
  low: string | null;
  close: string | null;
  currency: string;
  source: string;
  source_ref: string | null;
  volume: number | null;
}

export type FxHistory = PageEnvelope<FxBar>;

export interface TheoreticalBar {
  market_date: string;
  theoretical_price: string;
  ratio_used: string;
  ratio_used_formatted: string;
  fx_used: string;
  underlying_price_used: string;
  local_price: string | null;
  premium_discount: string | null;
  underlying_market_date: string;
  fx_market_date: string;
  source: string;
  source_ref: string | null;
  created_at: string;
}

export type TheoreticalHistory = PageEnvelope<TheoreticalBar>;

export interface PredictionModelInfo {
  name: string;
  algorithm: string;
  feature_version: string;
  horizon: string;
}

export interface Prediction {
  symbol: string;
  as_of: string;
  market_date: string;
  horizon: string;
  horizon_detail: string;
  model: PredictionModelInfo;
  feature_version: string;
  feature_version_match: boolean;
  probability_up: number;
  probability_down: number;
  actual_close: string | null;
  current_ratio: string | null;
  current_ratio_formatted: string | null;
}

export interface ModelSummary {
  id: number;
  name: string;
  algorithm: string;
  feature_version: string;
  horizon: string;
  params: Record<string, unknown>;
  artifact_path: string | null;
  is_active: boolean;
  created_at: string;
  updated_at: string;
}

export type ModelList = PageEnvelope<ModelSummary>;

export interface ModelRun {
  id: number;
  train_start: string;
  train_end: string;
  n_train: number;
  n_features: number;
  metrics: Record<string, number | null>;
  status: string;
  notes: string | null;
  created_at: string;
}

export interface ModelDetail extends ModelSummary {
  runs: ModelRun[];
}

export interface BacktestTrade {
  entry_index: number;
  exit_index: number;
  entry_price: number;
  exit_price: number;
  shares: number;
  costs: number;
  pnl: number;
}

export interface BacktestSummary {
  id: number;
  symbol: string;
  start_date: string;
  end_date: string;
  n_bars: number;
  holding_period: number;
  total_costs: string;
  metrics: Record<string, number | null>;
  created_at: string;
}

export type BacktestList = PageEnvelope<BacktestSummary>;

export interface BacktestDetail extends BacktestSummary {
  params: Record<string, unknown>;
  signals: number[];
  trades: BacktestTrade[];
  equity_curve: number[];
  benchmark_buy_hold: number[];
  benchmark_metrics: Record<string, number | null>;
  status: string;
  notes: string | null;
}

const REQUEST_TIMEOUT_MS = 8_000;

async function request<T>(path: string): Promise<ApiCallResult<T>> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
  try {
    const response = await fetch(`${INTERNAL_API_URL}${path}`, {
      signal: controller.signal,
      cache: "no-store",
      headers: { accept: "application/json" },
    });
    if (!response.ok) {
      return { ok: false, data: null, error: `HTTP ${response.status}` };
    }
    return { ok: true, data: (await response.json()) as T, error: null };
  } catch (error) {
    return {
      ok: false,
      data: null,
      error: error instanceof Error ? error.message : "unknown error",
    };
  } finally {
    clearTimeout(timer);
  }
}

/** Fetch the API discovery document. */
export function getApiInfo(): Promise<ApiCallResult<ApiInfo>> {
  return request<ApiInfo>("/api/v1");
}

/** Fetch the readiness report (PostgreSQL + Redis). */
export function getReadiness(): Promise<ApiCallResult<ReadinessReport>> {
  return request<ReadinessReport>("/health/ready");
}

/** Search the stored CEDEAR universe. */
export function searchCedears(query: string): Promise<ApiCallResult<CedearList>> {
  const params = new URLSearchParams({ q: query, limit: "50" });
  return request<CedearList>(`/api/v1/cedears?${params.toString()}`);
}

/** Browse the stored CEDEAR universe (first active instruments). */
export function browseCedears(): Promise<ApiCallResult<CedearList>> {
  return request<CedearList>("/api/v1/cedears?limit=20");
}

/** Fetch one instrument with its full ratio history. */
export function getCedear(symbol: string): Promise<ApiCallResult<CedearDetail>> {
  return request<CedearDetail>(`/api/v1/cedears/${encodeURIComponent(symbol)}`);
}

/** Fetch stored BYMA bars, newest first. */
export function getLocalHistory(symbol: string): Promise<ApiCallResult<LocalHistory>> {
  return request<LocalHistory>(
    `/api/v1/cedears/${encodeURIComponent(symbol)}/history?limit=200`,
  );
}

/** Fetch stored underlying bars, newest first. */
export function getUnderlyingHistory(
  symbol: string,
): Promise<ApiCallResult<UnderlyingHistory>> {
  return request<UnderlyingHistory>(
    `/api/v1/cedears/${encodeURIComponent(symbol)}/underlying?limit=200`,
  );
}

/** Fetch the stored FX reference series, newest first. */
export function getFxHistory(symbol: string): Promise<ApiCallResult<FxHistory>> {
  return request<FxHistory>(
    `/api/v1/cedears/${encodeURIComponent(symbol)}/fx?limit=200`,
  );
}

/** Fetch stored theoretical prices with provenance, newest first. */
export function getTheoreticalHistory(
  symbol: string,
): Promise<ApiCallResult<TheoreticalHistory>> {
  return request<TheoreticalHistory>(
    `/api/v1/cedears/${encodeURIComponent(symbol)}/theoretical-price?limit=200`,
  );
}

/** List registered models. */
export function listModels(): Promise<ApiCallResult<ModelList>> {
  return request<ModelList>("/api/v1/models?limit=200");
}

/** Fetch one model with its training runs. */
export function getModel(id: number): Promise<ApiCallResult<ModelDetail>> {
  return request<ModelDetail>(`/api/v1/models/${id}`);
}

/** List persisted backtests, newest first. */
export function listBacktests(): Promise<ApiCallResult<BacktestList>> {
  return request<BacktestList>("/api/v1/backtests?limit=100");
}

/** Fetch one persisted backtest with its full vectors. */
export function getBacktest(id: number): Promise<ApiCallResult<BacktestDetail>> {
  return request<BacktestDetail>(`/api/v1/backtests/${id}`);
}
