import Link from "next/link";
import { getApiInfo, getReadiness, PUBLIC_API_URL } from "@/lib/api";
import { Card } from "@/components/ui";

export const dynamic = "force-dynamic";

const PIPELINE = [
  { step: "CEDEAR metadata", phase: 2 },
  { step: "Local CEDEAR prices", phase: 3 },
  { step: "Underlying prices", phase: 3 },
  { step: "USD/ARS FX", phase: 3 },
  { step: "Theoretical CEDEAR value", phase: 4 },
  { step: "Feature engineering", phase: 5 },
  { step: "Baseline models", phase: 6 },
  { step: "Walk-forward validation", phase: 7 },
  { step: "CEDEAR backtesting", phase: 8 },
  { step: "Prediction API", phase: 9 },
  { step: "Dashboard", phase: 10 },
] as const;

type Badge = "ok" | "degraded" | "unavailable";

function StatusBadge({ status }: { status: Badge }) {
  const styles: Record<Badge, string> = {
    ok: "bg-emerald-500/15 text-emerald-700 dark:text-emerald-300 ring-emerald-500/30",
    degraded: "bg-amber-500/15 text-amber-700 dark:text-amber-300 ring-amber-500/30",
    unavailable: "bg-red-500/15 text-red-700 dark:text-red-300 ring-red-500/30",
  };
  return (
    <span
      className={`inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium ring-1 ${styles[status]}`}
    >
      {status}
    </span>
  );
}

function QuickLink({ href, title, body }: { href: string; title: string; body: string }) {
  return (
    <Link
      href={href}
      className="block rounded-xl border border-slate-200 bg-white/70 p-5 shadow-sm hover:border-sky-400 dark:border-slate-800 dark:bg-slate-900/60 dark:hover:border-sky-600"
    >
      <span className="font-semibold text-sky-600 dark:text-sky-400">{title}</span>
      <span className="mt-1 block text-sm text-slate-600 dark:text-slate-400">{body}</span>
    </Link>
  );
}

export default async function HomePage() {
  const [info, readiness] = await Promise.all([getApiInfo(), getReadiness()]);

  return (
    <main className="mx-auto flex min-h-screen w-full max-w-5xl flex-col gap-8 px-6 py-14">
      <header className="space-y-3">
        <h1 className="text-3xl font-bold tracking-tight">StockGambling</h1>
        <p className="max-w-3xl text-sm leading-relaxed text-slate-600 dark:text-slate-400">
          Research platform for predicting the <strong>next-day direction</strong> of Argentine
          CEDEARs traded on BYMA. The target is the CEDEAR quoted in ARS &mdash; a distinct
          instrument from its underlying foreign security, driven by the underlying price, the
          USD/ARS rate, the conversion ratio and local market behaviour.
        </p>
        <p className="text-sm text-slate-500 dark:text-slate-500">
          Research dashboard: search the CEDEAR universe, inspect actual vs theoretical prices,
          read next-session probabilities from registered models, and compare backtests against
          their benchmarks.
        </p>
      </header>

      <div className="grid gap-4 sm:grid-cols-2">
        <QuickLink
          href="/cedears"
          title="Explore CEDEARs →"
          body="Search instruments, see ratio history, multi-market panels and prediction probabilities."
        />
        <QuickLink
          href="/backtests"
          title="Backtests →"
          body="Strategy equity vs buy-and-hold, metrics and closed trades for persisted runs."
        />
      </div>

      <div className="grid gap-4 sm:grid-cols-2">
        <Card title="API service">
          {info.ok && info.data ? (
            <dl className="space-y-2 text-sm">
              <Row label="Service" value={info.data.service} />
              <Row label="Version" value={info.data.version} />
              <Row label="Environment" value={info.data.environment} />
              <Row label="API prefix" value={info.data.api_prefix} />
              <div className="pt-1">
                <Link
                  href={`${PUBLIC_API_URL}/docs`}
                  className="text-sm font-medium text-sky-600 underline underline-offset-4 hover:text-sky-500 dark:text-sky-400"
                >
                  Open interactive API docs
                </Link>
              </div>
            </dl>
          ) : (
            <Unavailable detail={info.error} />
          )}
        </Card>

        <Card title="Dependencies">
          {readiness.ok && readiness.data ? (
            <ul className="space-y-2 text-sm">
              <DependencyRow
                name={readiness.data.service}
                status={readiness.data.status as Badge}
                detail={`v${readiness.data.version} · ${readiness.data.environment}`}
              />
              {Object.entries(readiness.data.checks).map(([name, check]) => (
                <DependencyRow
                  key={name}
                  name={name}
                  status={check.status}
                  detail={`${check.latency_ms} ms`}
                />
              ))}
            </ul>
          ) : (
            <Unavailable detail={readiness.error} />
          )}
        </Card>
      </div>

      <Card title="Canonical pipeline">
        <ol className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
          {PIPELINE.map((item) => (
            <li
              key={item.step}
              className="flex items-center gap-2 rounded-lg bg-slate-100/70 px-3 py-2 text-sm dark:bg-slate-800/60"
            >
              <span className="font-mono text-xs text-slate-400">P{item.phase}</span>
              <span className="text-slate-700 dark:text-slate-300">{item.step}</span>
            </li>
          ))}
        </ol>
      </Card>

      <footer className="mt-auto border-t border-slate-200 pt-4 text-xs text-slate-500 dark:border-slate-800">
        Educational research software. Not investment advice. Model outputs are probabilities, never
        certainties.
      </footer>
    </main>
  );
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-baseline justify-between gap-4">
      <dt className="text-slate-500 dark:text-slate-400">{label}</dt>
      <dd className="font-mono text-slate-800 dark:text-slate-200">{value}</dd>
    </div>
  );
}

function DependencyRow({
  name,
  status,
  detail,
}: {
  name: string;
  status: Badge;
  detail: string;
}) {
  return (
    <li className="flex items-center justify-between gap-3">
      <span className="text-slate-700 dark:text-slate-300">{name}</span>
      <span className="flex items-center gap-2">
        <span className="font-mono text-xs text-slate-500">{detail}</span>
        <StatusBadge status={status} />
      </span>
    </li>
  );
}

function Unavailable({ detail }: { detail: string | null }) {
  return (
    <div className="space-y-2">
      <StatusBadge status="unavailable" />
      <p className="font-mono text-xs break-all text-slate-500">{detail ?? "no detail"}</p>
    </div>
  );
}
