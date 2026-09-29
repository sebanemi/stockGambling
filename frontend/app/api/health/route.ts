import { NextResponse } from "next/server";

import { getReadiness, INTERNAL_API_URL } from "@/lib/api";

export const dynamic = "force-dynamic";

/**
 * Liveness/readiness bridge for the web container.
 *
 * The Docker healthcheck hits this route. It mirrors the backend readiness
 * state so a degraded API surfaces as an unhealthy web container instead of a
 * silently broken dashboard.
 */
export async function GET() {
  const readiness = await getReadiness();

  return NextResponse.json(
    {
      status: readiness.ok ? (readiness.data?.status ?? "degraded") : "unavailable",
      upstream: INTERNAL_API_URL,
      api: readiness.data,
      error: readiness.error,
    },
    {
      status: readiness.ok && readiness.data?.status === "ok" ? 200 : 503,
      headers: { "cache-control": "no-store" },
    },
  );
}
