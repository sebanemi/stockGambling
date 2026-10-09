/**
 * Number and probability formatting for the dashboard.
 *
 * Formatting is display-only: raw API values are never rounded before they
 * are compared or plotted, only when they are rendered as text.
 */

const arsFormat = new Intl.NumberFormat("es-AR", {
  style: "currency",
  currency: "ARS",
  maximumFractionDigits: 2,
});

const compactFormat = new Intl.NumberFormat("en-US", {
  maximumFractionDigits: 4,
});

const percentFormat = new Intl.NumberFormat("en-US", {
  style: "percent",
  maximumFractionDigits: 1,
});

/** Format an ARS amount, or an em dash when unknown. */
export function fmtArs(value: string | number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  const numeric = typeof value === "string" ? Number(value) : value;
  if (!Number.isFinite(numeric)) return "—";
  return arsFormat.format(numeric);
}

/** Format a ratio string (e.g. "0.083333") compactly, or an em dash. */
export function fmtCompact(value: string | number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  const numeric = typeof value === "string" ? Number(value) : value;
  if (!Number.isFinite(numeric)) return "—";
  return compactFormat.format(numeric);
}

/** Format a fraction (0.53) as a percentage ("53.0%"), or an em dash. */
export function fmtPct(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return "—";
  return percentFormat.format(value);
}

/** Format a signed fraction as a percentage ("+2.1%" / "-0.4%"). */
export function fmtSignedPct(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return "—";
  const sign = value > 0 ? "+" : "";
  return `${sign}${percentFormat.format(value)}`;
}
