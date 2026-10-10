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
    label: `${usable.length} sesiones`,
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
        <Card title="Papel">
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
              Sigue a <span className="font-mono">{inst.underlying_symbol}</span>
              {inst.underlying_market ? ` en ${inst.underlying_market}` : ""} ·{" "}
            </>
          ) : null}
          Cotiza en pesos en BYMA: es un instrumento distinto de la acción original.
        </p>
      </header>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card
          title="Ficha del papel"
          hint="Datos básicos del instrumento y el ratio de conversión vigente (cuántas acciones representa cada CEDEAR)."
        >
          <dl className="space-y-1.5 text-sm">
            <Row label="Acción original" value={inst.underlying_symbol ?? "—"} />
            <Row label="Mercado de la acción" value={inst.underlying_market ?? "—"} />
            <Row
              label="Ratio actual"
              value={inst.current_ratio_formatted ?? "desconocido (nulo, nunca inventado)"}
            />
            <Row label="Fuente del ratio" value={inst.ratio_source ?? "—"} />
            <Row label="Estado del programa" value={inst.program_status} />
            <Row label="Activo" value={inst.is_active ? "sí" : "no"} />
          </dl>
        </Card>

        <Card
          title="¿Va a subir? (probabilidad)"
          hint="Elegí horizonte y modelo. La barra muestra la chance estimada y el texto te dice cómo leerla: cerca de 50/50 no hay señal."
        >
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

      <Card
        title="Precio real vs valor teórico (sesiones BYMA, ARS)"
        hint="El valor teórico es acción × dólar ÷ ratio: lo que debería valer. Si el precio real está por encima, el mercado está pagando de más."
      >
        {actualPoints.length < 2 || sharedDates.length < 2 ? (
          <Empty message="Hay menos de dos sesiones con precio real y valor teórico a la vez; no se inventa nada para rellenar." />
        ) : (
          <LineChart
            xLabel={`Sesiones BYMA · ${sharedDates.length} sesiones con ambos valores`}
            yFormat={(value) => fmtArs(value)}
            series={[
              {
                label: "Cierre real del CEDEAR",
                detail: `${actualPoints[0].x} → ${actualPoints[actualPoints.length - 1].x} · ARS`,
                color: "#0284c7",
                points: actualPoints.filter((point) => sharedDates.includes(point.x)),
              },
              {
                label: "Valor teórico",
                detail: "acción × dólar ÷ ratio · ARS",
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

      <Card
        title="Prima / descuento vs teórico"
        hint="Cuánto paga el mercado por encima (+) o por debajo (−) del valor teórico. Una prima alta y sostenida es entusiasmo local."
      >
        {premiumPoints.length < 2 ? (
          <Empty message="Sin historia de prima: falta la serie teórica o los cierres reales." />
        ) : (
          <>
            <LineChart
              xLabel={`Sesiones BYMA · ${premiumPoints.length} sesiones`}
              yFormat={(value) => fmtSignedPct(value)}
              series={[
                {
                  label: "Prima (+) / descuento (−)",
                  detail: "(real − teórico) / teórico",
                  color: "#7c3aed",
                  points: premiumPoints,
                },
              ]}
            />
            <p className="mt-2 text-xs text-slate-500">
              Último: {fmtSignedPct(premiumPoints[premiumPoints.length - 1].y)} el{" "}
              {premiumPoints[premiumPoints.length - 1].x}. Lo que sobra después de explicar el
              precio con acción + dólar es el humor local: esa es la señal, no ruido a esconder.
            </p>
          </>
        )}
      </Card>

      <Card
        title="Tres mercados, tres calendarios"
        hint="Cada serie usa las fechas de su propio mercado. Una sesión de BYMA y una de EE.UU. son días distintos: por eso van en paneles separados."
      >
        <div className="grid gap-5 lg:grid-cols-3">
          <div>
            <h3 className="mb-1 font-mono text-xs font-semibold">CEDEAR · BYMA · ARS</h3>
            {cedearPrices ? (
              <LineChart
                xLabel={`BYMA · ${cedearPrices.detail}`}
                yFormat={(value) => fmtArs(value)}
                series={[{ ...cedearPrices.series[0], label: "Cierre del CEDEAR", detail: "ARS" }]}
              />
            ) : (
              <Empty message="No hay cierres del CEDEAR guardados." />
            )}
          </div>
          <div>
            <h3 className="mb-1 font-mono text-xs font-semibold">
              Acción original · {inst.underlying_market ?? "mercado extranjero"}
            </h3>
            {underlyingPrices ? (
              <LineChart
                xLabel={`Sesiones del activo · ${underlyingPrices.detail}`}
                yFormat={(value) => `$${value.toFixed(2)}`}
                series={[
                  { ...underlyingPrices.series[0], label: "Cierre del activo", detail: "" },
                ]}
              />
            ) : (
              <Empty message="No hay cotizaciones del activo guardadas." />
            )}
          </div>
          <div>
            <h3 className="mb-1 font-mono text-xs font-semibold">Dólar de referencia</h3>
            {fxPrices ? (
              <LineChart
                xLabel={`Observaciones del dólar · ${fxPrices.detail}`}
                yFormat={(value) => fmtArs(value)}
                series={[{ ...fxPrices.series[0], label: "Pesos por dólar", detail: "" }]}
              />
            ) : (
              <Empty message="No hay observaciones del dólar guardadas." />
            )}
          </div>
        </div>
      </Card>

      <Card
        title="Modelos registrados y cómo rindieron"
        hint="Cada modelo se probó con datos que no había visto. 'Acierto en prueba' es el % de aciertos corrigiendo por desbalance: 50% es lo mismo que tirar una moneda."
      >
        {withRuns.length === 0 ? (
          <Empty message="No hay modelos registrados." />
        ) : (
          <ul className="space-y-3 text-sm">
            {withRuns.map(({ model, detail: full }) => {
              const latest = full.data?.runs.at(-1);
              const upRate = latest?.metrics["test_up_rate"] ?? null;
              const degenerate = upRate === 0 || upRate === 1;
              return (
                <li
                  key={model.id}
                  className="rounded-lg bg-slate-100/70 px-3 py-2.5 dark:bg-slate-800/60"
                >
                  <p className="font-mono font-medium">
                    {model.name}{" "}
                    <span className="text-xs text-slate-500">
                      {model.algorithm} · horizonte {model.horizon} · indicadores{" "}
                      {model.feature_version}
                      {model.artifact_path ? "" : " · sin modelo guardado"}
                    </span>
                  </p>
                  <p className="mt-1 font-mono text-xs text-slate-500">
                    {latest
                      ? `Entrenado con ${latest.train_start} → ${latest.train_end} (${latest.n_train} sesiones) · acierto en prueba ${fmtPct(latest.metrics["balanced_accuracy"] ?? null)} · acierto en validación progresiva ${fmtPct(latest.metrics["wf_balanced_accuracy"] ?? null)}`
                      : "Nunca entrenado"}
                  </p>
                  {degenerate ? (
                    <p className="mt-1 text-xs text-amber-700 dark:text-amber-300">
                      Ojo: en la prueba todo fue para el mismo lado (todo subas o todo bajas).
                      Ese puntaje refleja la tendencia del período, no habilidad del modelo.
                    </p>
                  ) : null}
                </li>
              );
            })}
          </ul>
        )}
      </Card>

      <Card
        title={`Simulaciones de ${inst.symbol}`}
        hint="Qué habría pasado operando este papel con las señales del modelo, con comisiones incluidas. Comparalo siempre contra comprar y mantener."
      >
        {symbolRunDetails.length === 0 ? (
          <Empty message="Todavía no hay simulaciones guardadas para este papel." />
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
                    simulación #{fetched.data.id}
                  </Link>
                  <span className="font-mono text-xs text-slate-500">
                    {fetched.data.start_date} → {fetched.data.end_date} · {fetched.data.n_bars}{" "}
                    barras · estrategia{" "}
                    {fmtSignedPct(fetched.data.metrics["total_return"] ?? null)} vs comprar y
                    mantener{" "}
                    {fmtSignedPct(fetched.data.benchmark_metrics["total_return"] ?? null)}
                  </span>
                </li>
              ),
            )}
          </ul>
        )}
        <p className="mt-2 text-xs">
          <Link href="/backtests" className="text-sky-600 underline underline-offset-4 dark:text-sky-400">
            Ver todas las simulaciones
          </Link>
        </p>
      </Card>
    </PageShell>
  );
}
