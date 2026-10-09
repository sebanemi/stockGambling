/**
 * Honest SVG charts for the dashboard.
 *
 * Two rules from the methodology shape every chart here:
 *
 * 1. A BYMA session and a foreign-market session are different days. Series
 *    from different markets are rendered as small multiples with their own
 *    market-date axes - never overlaid on a shared x-axis that would imply
 *    simultaneity. Series that share one market calendar (actual vs
 *    theoretical CEDEAR, strategy vs buy-and-hold over the same bars) may
 *    share an axis, and say so.
 * 2. Missing sessions are gaps, not zeroes: only stored points are plotted.
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
 * One line chart with its own x-axis of market dates.
 *
 * `xLabel` names the calendar (e.g. "BYMA sessions"), so a reader can never
 * mistake which market the dates belong to.
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
 * Up/down probability rendered as a bar - with the honest framing that a
 * weak signal is barely better than a coin.
 */
export function ProbabilityBar({ up }: { up: number }) {
  const down = 1 - up;
  const verdict =
    up >= 0.6
      ? "Leaning up - still a probability, not a call."
      : up <= 0.4
        ? "Leaning down - still a probability, not a call."
        : "Close to a coin flip: no meaningful edge.";
  return (
    <div>
      <div
        className="flex h-6 w-full overflow-hidden rounded-lg ring-1 ring-slate-300 dark:ring-slate-700"
        role="img"
        aria-label={`Probability up ${(up * 100).toFixed(1)} percent, down ${(down * 100).toFixed(1)} percent`}
      >
        <div className="bg-emerald-500/80" style={{ width: `${(up * 100).toFixed(1)}%` }} />
        <div className="bg-rose-500/80" style={{ width: `${(down * 100).toFixed(1)}%` }} />
      </div>
      <div className="mt-1.5 flex justify-between font-mono text-sm">
        <span className="text-emerald-700 dark:text-emerald-300">up {(up * 100).toFixed(1)}%</span>
        <span className="text-rose-700 dark:text-rose-300">down {(down * 100).toFixed(1)}%</span>
      </div>
      <p className="mt-1 text-xs text-slate-500">{verdict}</p>
    </div>
  );
}
