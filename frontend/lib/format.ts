/**
 * Formato de números y probabilidades para el panel.
 *
 * El formato es solo visual: los valores crudos de la API nunca se redondean
 * antes de compararse o graficarse, solo al mostrarse como texto.
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

/** Formatea un monto en ARS, o una raya si se desconoce. */
export function fmtArs(value: string | number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  const numeric = typeof value === "string" ? Number(value) : value;
  if (!Number.isFinite(numeric)) return "—";
  return arsFormat.format(numeric);
}

/** Formatea un ratio (p. ej. "0.083333") en forma compacta, o una raya. */
export function fmtCompact(value: string | number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  const numeric = typeof value === "string" ? Number(value) : value;
  if (!Number.isFinite(numeric)) return "—";
  return compactFormat.format(numeric);
}

/** Formatea una fracción (0.53) como porcentaje ("53,0 %"), o una raya. */
export function fmtPct(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return "—";
  return percentFormat.format(value);
}

/** Formatea una fracción con signo como porcentaje ("+2,1 %" / "-0,4 %"). */
export function fmtSignedPct(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return "—";
  const sign = value > 0 ? "+" : "";
  return `${sign}${percentFormat.format(value)}`;
}
