import Link from "next/link";

import { LineChart } from "@/components/charts";
import { Card, Empty, PageShell, Row, Unavailable } from "@/components/ui";
import { getBacktest } from "@/lib/api";
import { fmtArs, fmtPct, fmtSignedPct } from "@/lib/format";

export const dynamic = "force-dynamic";

const METRIC_ROWS: { key: string; label: string; format: (value: number | null) => string }[] = [
  { key: "total_return", label: "Total return", format: fmtSignedPct },
  { key: "annualised_return", label: "Annualised return", format: fmtSignedPct },
  { key: "volatility", label: "Volatility (ann.)", format: fmtSignedPct },
  { key: "sharpe", label: "Sharpe (rf = 0)", format: (v) => (v === null ? "—" : v.toFixed(2)) },
  { key: "max_drawdown", label: "Max drawdown", format: fmtPct },
  { key: "win_rate", label: "Win rate", format: fmtPct },
  { key: "trade_count", label: "Closed trades", format: (v) => (v === null ? "—" : `${v}`) },
  {
    key: "average_trade",
    label: "Average trade (ARS)",
    format: (v) => (v === null ? "—" : fmtArs(v)),
  },
  {
    key: "profit_factor",
    label: "Profit factor",
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
        <Card title="Backtest">
          <Empty message="Invalid run id." />
        </Card>
      </PageShell>
    );
  }

  const result = await getBacktest(runId);
  if (!result.ok || !result.data) {
    return (
      <PageShell>
        <Card title={`Backtest #${id}`}>
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
            Backtests
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
          {run.n_bars} stored bars · {run.holding_period}-session epochs · initial{" "}
          {fmtArs(Number(run.params["initial_capital"] ?? Number.NaN))} · position fraction{" "}
          {String(run.params["position_fraction"] ?? "—")} · commission{" "}
          {fmtPct(Number(run.params["commission_rate"] ?? Number.NaN))} · slippage{" "}
          {fmtPct(Number(run.params["slippage_rate"] ?? Number.NaN))}
        </p>
      </header>

      <Card title="Equity vs buy-and-hold (ARS, same bars)">
        {n < 2 ? (
          <Empty message="Fewer than two equity points stored." />
        ) : (
          <LineChart
            xLabel={`Stored bars · ${run.start_date} → ${run.end_date} (${n} points)`}
            yFormat={(value) => fmtArs(value)}
            series={[
              { label: "Strategy", detail: "long/flat, net of costs", color: "#0284c7", points: equityPoints },
              {
                label: "Buy-and-hold CEDEAR",
                detail: "same window, same entry costs",
                color: "#94a3b8",
                points: benchPoints,
              },
            ]}
          />
        )}
        <p className="mt-2 text-xs text-slate-500">
          Both curves are marked on the same stored bars, so the shared axis is honest. Costs
          paid: {fmtArs(run.total_costs)}.
        </p>
      </Card>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card title="Strategy metrics">
          <dl className="space-y-1.5 text-sm">
            {METRIC_ROWS.map((row) => (
              <Row
                key={row.key}
                label={row.label}
                value={row.format(run.metrics[row.key] ?? null)}
              />
            ))}
          </dl>
        </Card>
        <Card title="Buy-and-hold benchmark">
          <dl className="space-y-1.5 text-sm">
            {METRIC_ROWS.map((row) => (
              <Row
                key={row.key}
                label={row.label}
                value={row.format(run.benchmark_metrics[row.key] ?? null)}
              />
            ))}
          </dl>
        </Card>
      </div>

      <Card title={`Closed round-trip trades (${run.trades.length})`}>
        {run.trades.length === 0 ? (
          <Empty message="No closed round trips: the run held or stayed flat. An open tail position stays marked in the curve, never invented as a trade." />
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full font-mono text-xs">
              <thead>
                <tr className="text-left text-slate-500">
                  <th className="py-1 pr-3">Entry bar</th>
                  <th className="py-1 pr-3">Exit bar</th>
                  <th className="py-1 pr-3 text-right">Entry</th>
                  <th className="py-1 pr-3 text-right">Exit</th>
                  <th className="py-1 pr-3 text-right">P&amp;L (ARS)</th>
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
