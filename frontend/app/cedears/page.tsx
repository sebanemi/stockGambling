import Link from "next/link";

import { Card, Empty, PageShell, Unavailable } from "@/components/ui";
import { browseCedears, searchCedears } from "@/lib/api";

export const dynamic = "force-dynamic";

export default async function CedearsPage({
  searchParams,
}: {
  searchParams: Promise<{ q?: string }>;
}) {
  const { q } = await searchParams;
  const query = (q ?? "").trim();
  const result = query ? await searchCedears(query) : await browseCedears();

  return (
    <PageShell>
      <header className="space-y-1">
        <h1 className="text-2xl font-bold tracking-tight">CEDEARs</h1>
        <p className="text-sm text-slate-600 dark:text-slate-400">
          Buscá en el universo de CEDEARs guardado. Acá aparecen solo los papeles que el
          sistema ya importó; si sale uno nuevo a cotizar, aparece solo sin tocar código.
        </p>
      </header>

      <form method="get" action="/cedears" className="flex gap-2">
        <input
          name="q"
          defaultValue={query}
          placeholder="Buscá por símbolo, nombre o activo original (p. ej. AAPL)"
          className="w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm dark:border-slate-700 dark:bg-slate-900"
        />
        <button
          type="submit"
          className="rounded-lg bg-sky-600 px-4 py-2 text-sm font-medium text-white hover:bg-sky-500"
        >
          Buscar
        </button>
      </form>

      <Card
        title={query ? `Resultados para "${query}"` : "Explorar el universo"}
        hint="Tocá un símbolo para abrir su ficha: precios, probabilidad del modelo y simulaciones."
      >
        {!result.ok || !result.data ? (
          <Unavailable detail={result.error} />
        ) : result.data.items.length === 0 ? (
          <Empty message="Nada coincide. El universo es lo que se haya importado hasta ahora." />
        ) : (
          <div>
            <p className="mb-2 text-xs text-slate-500">
              {result.data.total} coincidencia{result.data.total === 1 ? "" : "s"}
            </p>
            <ul className="divide-y divide-slate-200 dark:divide-slate-800">
              {result.data.items.map((item) => (
                <li key={item.symbol} className="flex items-center justify-between gap-3 py-2.5">
                  <div>
                    <Link
                      href={`/cedears/${encodeURIComponent(item.symbol)}`}
                      className="font-mono font-semibold text-sky-600 underline-offset-4 hover:underline dark:text-sky-400"
                    >
                      {item.symbol}
                    </Link>
                    <p className="text-xs text-slate-500">
                      {[item.name, item.underlying_symbol ? `→ ${item.underlying_symbol}` : null]
                        .filter(Boolean)
                        .join(" · ") || "—"}
                    </p>
                  </div>
                  <span className="font-mono text-xs text-slate-500">
                    {item.current_ratio_formatted ?? "ratio desconocido"}
                  </span>
                </li>
              ))}
            </ul>
          </div>
        )}
      </Card>
    </PageShell>
  );
}
