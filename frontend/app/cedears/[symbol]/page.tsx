import Link from "next/link";

import { LineChart, type ChartSeries } from "@/components/charts";
import { PredictionPanel } from "@/components/PredictionPanel";
import { Card, Empty, PageShell, Row, Unavailable } from "@/components/ui";
import {
  getBacktest,
  getCedear,
  getFxHistory,
  getLocalHistory,
  getModel,
  getTheoreticalHistory,
  getUnderlyingHistory,
  listBacktests,
  listModels,
} from "@/lib/api";
import { fmtArs, fmtPct, fmtSignedPct } from "@/lib/format";

export const dynamic = "force-dynamic";

function prices(points: { market_date: string; close: string | null }[]): {
  label: string;
  detail: string;
  series: ChartSeries[];
} | null {
  const usable = points.filter(
    (point): point is { market_date: string; close: string } => point.close !== null,
  );
  if (usable.length < 2) return null;
  const oldest = [...usable].reverse();
  return {
    label: `${usable.length} sessions`,
    detail: `${oldest[0].market_date} → ${oldest[oldest.length - 1].market_date}`,
    series: [
      {
        label: "",
        detail: "",
        color: "",
        points: oldest.map((point) => ({ x: point.market_date, y: Number(point.close) })),
      },
    ],
  };
}

export default async function InstrumentPage({
  params,
}: {
  params: Promise<{ symbol: string }>;
}) {
  const { symbol } = await params;
  const [detail, local, underlying, fx, theoretical, models, backtests] = await Promise.all([
    getCedear(symbol),
    getLocalHistory(symbol),
    getUnderlyingHistory(symbol),
    getFxHistory(symbol),
    getTheoreticalHistory(symbol),
    listModels(),
    listBacktests(),
  ]);

  if (!detail.ok || !detail.data) {
    return (
      <PageShell>
        <h1 className="font-mono text-2xl font-bold">{decodeURIComponent(symbol)}</h1>
        <Card title="Instrument">
          <Unavailable detail={detail.error} />
        </Card>
      </PageShell>
    );
  }

  const inst = detail.data;
  const localBars = [...(local.data?.items ?? [])].reverse();
  const theoBars = [...(theoretical.data?.items ?? [])].reverse();

  const actualPoints = localBars
    .filter((bar) => bar.close !== null)
    .map((bar) => ({ x: bar.market_date, y: Number(bar.close) }));
  const theoByDate = new Map(theoBars.map((bar) => [bar.market_date, bar]));
  const sharedDates = localBars
    .map((bar) => bar.market_date)
    .filter((date) => theoByDate.has(date));
  const premiumPoints = sharedDates.flatMap((date) => {
    const bar = theoByDate.get(date);
    const premium =
      bar?.premium_discount !== null && bar?.premium_discount !== undefined
        ? Number(bar.premium_discount)
        : null;
    return premium === null || !Number.isFinite(premium) ? [] : [{ x: date, y: premium }];
  });

  const cedearPrices = prices(local.data?.items ?? []);
  const underlyingPrices = prices(underlying.data?.items ?? []);
  const fxPrices = prices(fx.data?.items ?? []);

  const registered = (models.data?.items ?? []).filter((model) => model.is_active);
  const withRuns = await Promise.all(
    registered.map(async (model) => ({ model, detail: await getModel(model.id) })),
  );
  const symbolRuns = (backtests.data?.items ?? []).filter(
    (run) => run.symbol.toUpperCase() === inst.symbol.toUpperCase(),
  );
  const symbolRunDetails = await Promise.all(
    symbolRuns.slice(0, 5).map(async (run) => getBacktest(run.id)),
  );

  return (
    <PageShell>
      <header className="space-y-1">
        <p className="font-mono text-xs text-slate-500">
          <Link href="/cedears" className="underline underline-offset-4 hover:text-slate-800">
            CEDEARs
          </Link>{" "}
          / {inst.symbol}
        </p>
        <h1 className="text-2xl font-bold tracking-tight">
          <span className="font-mono">{inst.symbol}</span>{" "}
          {inst.name ? (
            <span className="text-lg font-medium text-slate-500">{inst.name}</span>
          ) : null}
        </h1>
        <p className="text-sm text-slate-600 dark:text-slate-400">
          {inst.underlying_symbol ? (
            <>
              Tracks <span className="font-mono">{inst.underlying_symbol}</span>
              {inst.underlying_market ? ` on ${inst.underlying_market}` : ""} ·{" "}
            </>
          ) : null}
          Quoted in ARS on BYMA - a distinct instrument from its underlying.
        </p>
      </header>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card title="Instrument">
          <dl className="space-y-1.5 text-sm">
            <Row label="Underlying" value={inst.underlying_symbol ?? "—"} />
            <Row label="Underlying market" value={inst.underlying_market ?? "—"} />
            <Row
              label="Current ratio"
              value={inst.current_ratio_formatted ?? "unknown (null, never defaulted)"}
            />
            <Row label="Ratio source" value={inst.ratio_source ?? "—"} />
            <Row label="Program status" value={inst.program_status} />
            <Row label="Active" value={inst.is_active ? "yes" : "no"} />
          </dl>
        </Card>

        <Card title="Next-session direction (probability)">
          <PredictionPanel
            symbol={inst.symbol}
            models={registered
              .filter((model) => model.artifact_path)
              .map((model) => ({
                name: model.name,
                algorithm: model.algorithm,
                horizon: model.horizon,
              }))}
          />
        </Card>
      </div>

      <Card title="Actual vs theoretical price (BYMA sessions, ARS)">
        {actualPoints.length < 2 || sharedDates.length < 2 ? (
          <Empty message="Fewer than two sessions carry both an actual close and a theoretical value; nothing is interpolated." />
        ) : (
          <LineChart
            xLabel={`BYMA sessions · ${sharedDates.length} sessions with both values`}
            yFormat={(value) => fmtArs(value)}
            series={[
              {
                label: "Actual CEDEAR close",
                detail: `${actualPoints[0].x} → ${actualPoints[actualPoints.length - 1].x} · ARS`,
                color: "#0284c7",
                points: actualPoints.filter((point) => sharedDates.includes(point.x)),
              },
              {
                label: "Theoretical value",
                detail: "underlying × FX / ratio · ARS",
                color: "#94a3b8",
                points: sharedDates.map((date) => ({
                  x: date,
                  y: Number(theoByDate.get(date)?.theoretical_price ?? Number.NaN),
                })),
              },
            ]}
          />
        )}
      </Card>

      <Card title="Premium / discount vs theoretical">
        {premiumPoints.length < 2 ? (
          <Empty message="No premium history: either the theoretical series or the actual closes are missing." />
        ) : (
          <>
            <LineChart
              xLabel={`BYMA sessions · ${premiumPoints.length} sessions`}
              yFormat={(value) => fmtSignedPct(value)}
              series={[
                {
                  label: "Premium (+) / discount (−)",
                  detail: "(actual − theoretical) / theoretical",
                  color: "#7c3aed",
                  points: premiumPoints,
                },
              ]}
            />
            <p className="mt-2 text-xs text-slate-500">
              Latest: {fmtSignedPct(premiumPoints[premiumPoints.length - 1].y)} on{" "}
              {premiumPoints[premiumPoints.length - 1].x}. The residual carries local
              sentiment, frictions and liquidity - that is the signal, not noise to hide.
            </p>
          </>
        )}
      </Card>

      <Card title="Three markets, three calendars">
        <p className="mb-3 text-xs text-slate-500">
          Each series is plotted against its own market dates. A BYMA session and a US session
          are different days; the panels below never overlay them on a shared axis.
        </p>
        <div className="grid gap-5 lg:grid-cols-3">
          <div>
            <h3 className="mb-1 font-mono text-xs font-semibold">CEDEAR · BYMA · ARS</h3>
            {cedearPrices ? (
              <LineChart
                xLabel={`BYMA · ${cedearPrices.detail}`}
                yFormat={(value) => fmtArs(value)}
                series={[{ ...cedearPrices.series[0], label: "CEDEAR close", detail: "ARS" }]}
              />
            ) : (
              <Empty message="No CEDEAR closes stored." />
            )}
          </div>
          <div>
            <h3 className="mb-1 font-mono text-xs font-semibold">
              Underlying · {inst.underlying_market ?? "foreign market"}
            </h3>
            {underlyingPrices ? (
              <LineChart
                xLabel={`Underlying sessions · ${underlyingPrices.detail}`}
                yFormat={(value) => `$${value.toFixed(2)}`}
                series={[
                  { ...underlyingPrices.series[0], label: "Underlying close", detail: "" },
                ]}
              />
            ) : (
              <Empty message="No underlying bars stored." />
            )}
          </div>
          <div>
            <h3 className="mb-1 font-mono text-xs font-semibold">USD/ARS reference</h3>
            {fxPrices ? (
              <LineChart
                xLabel={`FX observations · ${fxPrices.detail}`}
                yFormat={(value) => fmtArs(value)}
                series={[{ ...fxPrices.series[0], label: "ARS per USD", detail: "" }]}
              />
            ) : (
              <Empty message="No FX observations stored." />
            )}
          </div>
        </div>
      </Card>

      <Card title="Registered models & recent performance">
        {withRuns.length === 0 ? (
          <Empty message="No registered models." />
        ) : (
          <ul className="divide-y divide-slate-200 text-sm dark:divide-slate-800">
            {withRuns.map(({ model, detail: full }) => {
              const latest = full.data?.runs.at(-1);
              const upRate = latest?.metrics["test_up_rate"] ?? null;
              const degenerate = upRate === 0 || upRate === 1;
              return (
                <li key={model.id} className="flex flex-wrap items-baseline justify-between gap-2 py-2">
                  <span className="font-mono font-medium">
                    {model.name}{" "}
                    <span className="text-xs text-slate-500">
                      {model.algorithm} · {model.horizon} · features {model.feature_version}
                      {model.artifact_path ? "" : " · no artifact"}
                    </span>
                  </span>
                  <span className="font-mono text-xs text-slate-500">
                    {latest
                      ? `train ${latest.train_start}→${latest.train_end} · n=${latest.n_train} · wf bal-acc ${fmtPct(latest.metrics["wf_balanced_accuracy"] ?? null)} · test bal-acc ${fmtPct(latest.metrics["balanced_accuracy"] ?? null)}${degenerate ? " · test 100% one-sided — trend, not skill" : ""}`
                      : "never trained"}
                  </span>
                </li>
              );
            })}
          </ul>
        )}
      </Card>

      <Card title={`Backtests for ${inst.symbol}`}>
        {symbolRunDetails.length === 0 ? (
          <Empty message="No persisted backtests for this symbol yet." />
        ) : (
          <ul className="divide-y divide-slate-200 text-sm dark:divide-slate-800">
            {symbolRunDetails.map((fetched) =>
              !fetched.ok || !fetched.data ? null : (
                <li
                  key={fetched.data.id}
                  className="flex flex-wrap items-baseline justify-between gap-2 py-2"
                >
                  <Link
                    href={`/backtests/${fetched.data.id}`}
                    className="font-mono font-medium text-sky-600 underline-offset-4 hover:underline dark:text-sky-400"
                  >
                    run #{fetched.data.id}
                  </Link>
                  <span className="font-mono text-xs text-slate-500">
                    {fetched.data.start_date}→{fetched.data.end_date} · {fetched.data.n_bars}{" "}
                    bars · strategy {fmtSignedPct(fetched.data.metrics["total_return"] ?? null)}{" "}
                    vs buy-and-hold{" "}
                    {fmtSignedPct(fetched.data.benchmark_metrics["total_return"] ?? null)}
                  </span>
                </li>
              ),
            )}
          </ul>
        )}
        <p className="mt-2 text-xs">
          <Link href="/backtests" className="text-sky-600 underline underline-offset-4 dark:text-sky-400">
            All backtests
          </Link>
        </p>
      </Card>
    </PageShell>
  );
}
