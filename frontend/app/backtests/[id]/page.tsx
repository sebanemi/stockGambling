import Link from "next/link";

import { LineChart } from "@/components/charts";
import { Card, Empty, PageShell, Row, Unavailable } from "@/components/ui";
import { getBacktest } from "@/lib/api";
import { fmtArs, fmtPct, fmtSignedPct } from "@/lib/format";

export const dynamic = "force-dynamic";

const METRIC_ROWS: {
  key: string;
  label: string;
  hint: string;
  format: (value: number | null) => string;
}[] = [
  {
    key: "total_return",
    label: "Ganancia total",
    hint: "Cuánto creció (o cayó) la plata de punta a punta.",
    format: fmtSignedPct,
  },
  {
    key: "annualised_return",
    label: "Ganancia anualizada",
    hint: "La ganancia total expresada como ritmo anual, para comparar períodos.",
    format: fmtSignedPct,
  },
  {
    key: "volatility",
    label: "Volatilidad (anual)",
    hint: "Cuánto se sacude el valor: más alta, más montaña rusa.",
    format: fmtSignedPct,
  },
  {
    key: "sharpe",
    label: "Sharpe",
    hint: "Ganancia por unidad de riesgo. Más de 1 es bueno, menos de 0 es malo.",
    format: (v) => (v === null ? "—" : v.toFixed(2)),
  },
  {
    key: "max_drawdown",
    label: "Peor caída",
    hint: "La peor pérdida desde un pico hasta recuperarse.",
    format: fmtPct,
  },
  {
    key: "win_rate",
    label: "Operaciones ganadoras",
    hint: "Qué % de las operaciones terminó ganando plata.",
    format: fmtPct,
  },
  {
    key: "trade_count",
    label: "Operaciones cerradas",
    hint: "Cuántas compras con su venta se completaron.",
    format: (v) => (v === null ? "—" : `${v}`),
  },
  {
    key: "average_trade",
    label: "Resultado promedio (ARS)",
    hint: "Cuánto ganó o perdió en promedio cada operación cerrada.",
    format: (v) => (v === null ? "—" : fmtArs(v)),
  },
  {
    key: "profit_factor",
    label: "Factor de ganancia",
    hint: "Cuánto se ganó por cada $1 perdido. Más de 1 = rentable.",
    format: (v) => (v === null ? "—" : v.toFixed(2)),
  },
];

export default async function BacktestDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  const runId = Number(id);
  if (!Number.isInteger(runId)) {
    return (
      <PageShell>
        <Card title="Simulación">
          <Empty message="Ese número de simulación no es válido." />
        </Card>
      </PageShell>
    );
  }

  const result = await getBacktest(runId);
  if (!result.ok || !result.data) {
    return (
      <PageShell>
        <Card title={`Simulación #${id}`}>
          <Unavailable detail={result.error} />
        </Card>
      </PageShell>
    );
  }

  const run = result.data;
  const n = run.equity_curve.length;
  const equityPoints = run.equity_curve.map((value, i) => ({ x: `${i}`, y: value }));
  const benchPoints = run.benchmark_buy_hold.map((value, i) => ({ x: `${i}`, y: value }));

  return (
    <PageShell>
      <header className="space-y-1">
        <p className="font-mono text-xs text-slate-500">
          <Link href="/backtests" className="underline underline-offset-4 hover:text-slate-800">
            Simulaciones
          </Link>{" "}
          / #{run.id}
        </p>
        <h1 className="text-2xl font-bold tracking-tight">
          <span className="font-mono">{run.symbol}</span>{" "}
          <span className="text-lg font-medium text-slate-500">
            {run.start_date} → {run.end_date}
          </span>
        </h1>
        <p className="text-sm text-slate-600 dark:text-slate-400">
          {run.n_bars} barras guardadas · se mantiene la posición {run.holding_period}{" "}
          sesiones · capital inicial{" "}
          {fmtArs(Number(run.params["initial_capital"] ?? Number.NaN))} · fracción por
          posición {String(run.params["position_fraction"] ?? "—")} · comisión{" "}
          {fmtPct(Number(run.params["commission_rate"] ?? Number.NaN))} · deslizamiento{" "}
          {fmtPct(Number(run.params["slippage_rate"] ?? Number.NaN))}
        </p>
      </header>

      <Card
        title="Tu estrategia vs comprar y mantener (ARS, mismas barras)"
        hint="Las dos curvas usan las mismas barras, así que compararlas es honesto. Si la azul no le gana a la gris, la señal no agrega nada."
      >
        {n < 2 ? (
          <Empty message="Hay menos de dos puntos guardados para dibujar." />
        ) : (
          <LineChart
            xLabel={`Barras guardadas · ${run.start_date} → ${run.end_date} (${n} puntos)`}
            yFormat={(value) => fmtArs(value)}
            series={[
              { label: "Estrategia", detail: "compra/espera, con costos", color: "#0284c7", points: equityPoints },
              {
                label: "Comprar y mantener el CEDEAR",
                detail: "mismo período, mismos costos de entrada",
                color: "#94a3b8",
                points: benchPoints,
              },
            ]}
          />
        )}
        <p className="mt-2 text-xs text-slate-500">
          Costos pagados en total: {fmtArs(run.total_costs)}.
        </p>
      </Card>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card
          title="Métricas de la estrategia"
          hint="Pasá el ojo por la letra chica de cada métrica: ahí dice qué significa."
        >
          <dl className="space-y-1.5 text-sm">
            {METRIC_ROWS.map((row) => (
              <div key={row.key}>
                <Row label={row.label} value={row.format(run.metrics[row.key] ?? null)} />
                <p className="text-right text-xs text-slate-400">{row.hint}</p>
              </div>
            ))}
          </dl>
        </Card>
        <Card
          title="Métricas de comprar y mantener"
          hint="La vara a superar: ¿valió la pena operar, o convenía no tocar nada?"
        >
          <dl className="space-y-1.5 text-sm">
            {METRIC_ROWS.map((row) => (
              <div key={row.key}>
                <Row
                  label={row.label}
                  value={row.format(run.benchmark_metrics[row.key] ?? null)}
                />
                <p className="text-right text-xs text-slate-400">{row.hint}</p>
              </div>
            ))}
          </dl>
        </Card>
      </div>

      <Card
        title={`Operaciones cerradas (${run.trades.length})`}
        hint="Cada fila es una compra con su venta. La última posición puede quedar abierta: se ve en la curva pero no se inventa como operación."
      >
        {run.trades.length === 0 ? (
          <Empty message="Sin operaciones cerradas: la simulación compró y mantuvo, o se quedó quieta." />
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full font-mono text-xs">
              <thead>
                <tr className="text-left text-slate-500">
                  <th className="py-1 pr-3">Entrada (barra)</th>
                  <th className="py-1 pr-3">Salida (barra)</th>
                  <th className="py-1 pr-3 text-right">Precio de entrada</th>
                  <th className="py-1 pr-3 text-right">Precio de salida</th>
                  <th className="py-1 pr-3 text-right">Resultado (ARS)</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-200 dark:divide-slate-800">
                {run.trades.map((trade, i) => (
                  <tr key={i}>
                    <td className="py-1 pr-3">#{trade.entry_index}</td>
                    <td className="py-1 pr-3">#{trade.exit_index}</td>
                    <td className="py-1 pr-3 text-right">{fmtArs(trade.entry_price)}</td>
                    <td className="py-1 pr-3 text-right">{fmtArs(trade.exit_price)}</td>
                    <td
                      className={`py-1 pr-3 text-right ${trade.pnl >= 0 ? "text-emerald-700 dark:text-emerald-300" : "text-rose-700 dark:text-rose-300"}`}
                    >
                      {fmtArs(trade.pnl)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </PageShell>
  );
}
