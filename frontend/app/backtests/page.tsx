import Link from "next/link";

import { Card, Empty, PageShell, Unavailable } from "@/components/ui";
import { listBacktests } from "@/lib/api";
import { fmtArs, fmtSignedPct } from "@/lib/format";

export const dynamic = "force-dynamic";

export default async function BacktestsPage() {
  const result = await listBacktests();

  return (
    <PageShell>
      <header className="space-y-1">
        <h1 className="text-2xl font-bold tracking-tight">Simulaciones</h1>
        <p className="text-sm text-slate-600 dark:text-slate-400">
          Qué habría pasado operando cada papel con las señales del modelo, en pesos y siempre
          al lado de la comparación honesta: haber comprado y mantenido. Las comisiones
          descuentan de verdad; una estrategia que no las cubre da pérdida.
        </p>
      </header>

      <Card
        title="Simulaciones guardadas"
        hint="Tocá una para ver el gráfico, las métricas y cada operación cerrada."
      >
        {!result.ok || !result.data ? (
          <Unavailable detail={result.error} />
        ) : result.data.items.length === 0 ? (
          <Empty message="Todavía no hay simulaciones guardadas." />
        ) : (
          <ul className="divide-y divide-slate-200 text-sm dark:divide-slate-800">
            {result.data.items.map((run) => (
              <li key={run.id} className="flex flex-wrap items-baseline justify-between gap-2 py-2.5">
                <div>
                  <Link
                    href={`/backtests/${run.id}`}
                    className="font-mono font-semibold text-sky-600 underline-offset-4 hover:underline dark:text-sky-400"
                  >
                    simulación #{run.id} · {run.symbol}
                  </Link>
                  <p className="font-mono text-xs text-slate-500">
                    {run.start_date} → {run.end_date} · {run.n_bars} barras · costos{" "}
                    {fmtArs(run.total_costs)}
                  </p>
                </div>
                <span className="font-mono text-xs">
                  estrategia {fmtSignedPct(run.metrics["total_return"] ?? null)}
                </span>
              </li>
            ))}
          </ul>
        )}
      </Card>
    </PageShell>
  );
}
