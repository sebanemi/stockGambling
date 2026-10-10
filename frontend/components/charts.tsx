/**
 * Gráficos SVG honestos para el panel.
 *
 * Dos reglas de la metodología dan forma a cada gráfico:
 *
 * 1. Una sesión de BYMA y una sesión del mercado extranjero son días
 *    distintos. Las series de mercados diferentes se dibujan como paneles
 *    separados, cada uno con su propio eje de fechas - nunca superpuestas en
 *    un eje compartido que sugeriría simultaneidad. Las series que comparten
 *    un calendario (precio real vs teórico del CEDEAR, estrategia vs
 *    buy-and-hold sobre las mismas barras) sí pueden compartir eje, y lo dicen.
 * 2. Las sesiones faltantes son huecos, no ceros: solo se dibujan los puntos
 *    guardados.
 */

export interface ChartPoint {
  /** Market-date label of the stored observation (the market's own day). */
  x: string;
  y: number;
}

export interface ChartSeries {
  label: string;
  detail: string;
  color: string;
  points: ChartPoint[];
}

const WIDTH = 640;
const HEIGHT = 220;
const PAD_LEFT = 56;
const PAD_RIGHT = 12;
const PAD_TOP = 12;
const PAD_BOTTOM = 28;

function extent(series: ChartSeries[]): { min: number; max: number } {
  let min = Number.POSITIVE_INFINITY;
  let max = Number.NEGATIVE_INFINITY;
  for (const line of series) {
    for (const point of line.points) {
      if (point.y < min) min = point.y;
      if (point.y > max) max = point.y;
    }
  }
  if (!Number.isFinite(min) || !Number.isFinite(max)) return { min: 0, max: 1 };
  if (min === max) return { min: min - 1, max: max + 1 };
  const span = max - min;
  return { min: min - span * 0.05, max: max + span * 0.05 };
}

function pathFor(
  points: ChartPoint[],
  min: number,
  max: number,
  plotWidth: number,
  plotHeight: number,
): string {
  const n = points.length;
  if (n === 0) return "";
  const step = n === 1 ? 0 : plotWidth / (n - 1);
  return points
    .map((point, i) => {
      const px = PAD_LEFT + step * i;
      const py = PAD_TOP + plotHeight - ((point.y - min) / (max - min)) * plotHeight;
      return `${i === 0 ? "M" : "L"}${px.toFixed(1)},${py.toFixed(1)}`;
    })
    .join(" ");
}

/**
 * Un gráfico de líneas con su propio eje x de fechas de mercado.
 *
 * `xLabel` nombra el calendario (p. ej. "sesiones BYMA"), para que nadie
 * confunda a qué mercado pertenecen las fechas.
 */
export function LineChart({
  series,
  xLabel,
  yFormat,
}: {
  series: ChartSeries[];
  xLabel: string;
  yFormat: (value: number) => string;
}) {
  const plotWidth = WIDTH - PAD_LEFT - PAD_RIGHT;
  const plotHeight = HEIGHT - PAD_TOP - PAD_BOTTOM;
  const { min, max } = extent(series);
  const firstX = series[0]?.points[0]?.x ?? "—";
  const lastX = series[0]?.points.at(-1)?.x ?? "—";
  const ticks = [min, (min + max) / 2, max];

  return (
    <figure>
      <svg
        viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
        role="img"
        aria-label={`${series.map((line) => line.label).join(", ")} over ${xLabel}`}
        className="w-full"
      >
        {ticks.map((tick) => {
          const py = PAD_TOP + plotHeight - ((tick - min) / (max - min)) * plotHeight;
          return (
            <g key={tick}>
              <line x1={PAD_LEFT} x2={WIDTH - PAD_RIGHT} y1={py} y2={py} stroke="#cbd5e1" strokeWidth="0.5" />
              <text x={PAD_LEFT - 6} y={py + 3.5} textAnchor="end" fontSize="10" fill="#64748b">
                {yFormat(tick)}
              </text>
            </g>
          );
        })}
        {series.map((line) => (
          <path
            key={line.label}
            d={pathFor(line.points, min, max, plotWidth, plotHeight)}
            fill="none"
            stroke={line.color}
            strokeWidth="1.8"
          />
        ))}
        <text x={PAD_LEFT} y={HEIGHT - 12} fontSize="10" fill="#64748b">
          {firstX}
        </text>
        <text x={WIDTH - PAD_RIGHT} y={HEIGHT - 12} fontSize="10" fill="#64748b" textAnchor="end">
          {lastX}
        </text>
        <text
          x={(WIDTH - PAD_LEFT - PAD_RIGHT) / 2 + PAD_LEFT}
          y={HEIGHT - 12}
          fontSize="10"
          fill="#94a3b8"
          textAnchor="middle"
        >
          {xLabel}
        </text>
      </svg>
      <figcaption>
        <ul className="mt-1 space-y-0.5 text-xs text-slate-500">
          {series.map((line) => (
            <li key={line.label} className="flex items-center gap-2">
              <span
                aria-hidden="true"
                className="inline-block h-2 w-4 rounded-sm"
                style={{ backgroundColor: line.color }}
              />
              <span className="font-medium text-slate-600 dark:text-slate-300">{line.label}</span>
              <span className="font-mono">{line.detail}</span>
            </li>
          ))}
        </ul>
      </figcaption>
    </figure>
  );
}

/**
 * Probabilidad de suba/baja dibujada como barra - aclarando que una señal
 * débil apenas supera a una moneda.
 */
export function ProbabilityBar({ up }: { up: number }) {
  const down = 1 - up;
  const verdict =
    up >= 0.65
      ? "Señal fuerte a favor de la suba - igual es una probabilidad, no una orden de compra."
      : up >= 0.55
        ? "Señal débil a favor de la suba - apenas mejor que una moneda."
        : up > 0.45
          ? "Casi un 50/50: no hay señal aprovechable."
          : up > 0.35
            ? "Señal débil a favor de la baja - apenas mejor que una moneda."
            : "Señal fuerte a favor de la baja - igual es una probabilidad, no una orden de venta.";
  return (
    <div>
      <div
        className="flex h-6 w-full overflow-hidden rounded-lg ring-1 ring-slate-300 dark:ring-slate-700"
        role="img"
        aria-label={`Probabilidad de suba ${(up * 100).toFixed(1)} por ciento, de baja ${(down * 100).toFixed(1)} por ciento`}
      >
        <div className="bg-emerald-500/80" style={{ width: `${(up * 100).toFixed(1)}%` }} />
        <div className="bg-rose-500/80" style={{ width: `${(down * 100).toFixed(1)}%` }} />
      </div>
      <div className="mt-1.5 flex justify-between font-mono text-sm">
        <span className="text-emerald-700 dark:text-emerald-300">suba {(up * 100).toFixed(1)}%</span>
        <span className="text-rose-700 dark:text-rose-300">baja {(down * 100).toFixed(1)}%</span>
      </div>
      <p className="mt-1 text-xs text-slate-500">{verdict}</p>
    </div>
  );
}
