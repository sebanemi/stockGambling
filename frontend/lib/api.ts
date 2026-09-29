/**
 * Typed access to the StockGambling FastAPI backend.
 *
 * Two base URLs exist on purpose:
 *  - `API_INTERNAL_URL` is used by server components and route handlers, which
 *    run inside the Compose network and must reach the API as `http://api:8000`.
 *  - `NEXT_PUBLIC_API_URL` is used by browser code, which can only reach the
 *    API through the published host port.
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

const REQUEST_TIMEOUT_MS = 5_000;

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
