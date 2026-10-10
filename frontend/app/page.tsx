import Link from "next/link";
import { getApiInfo, getReadiness, PUBLIC_API_URL } from "@/lib/api";
import { Card } from "@/components/ui";

export const dynamic = "force-dynamic";

const PIPELINE = [
  { step: "Datos del CEDEAR", detail: "Qué papeles existen y cuántas acciones representa cada uno (ratio de conversión)." },
  { step: "Precios", detail: "Cotización del CEDEAR en BYMA, del activo original en EE.UU. y del dólar oficial." },
  { step: "Valor teórico", detail: "Cuánto debería valer el CEDEAR según activo × dólar ÷ ratio." },
  { step: "Indicadores", detail: "RSI, MACD, volatilidad y otras señales calculadas de los precios." },
  { step: "Modelos", detail: "Algoritmos que estiman la probabilidad de que el precio suba." },
  { step: "Validación", detail: "Se prueban en el pasado sin haber visto el futuro, para no autoengañarse." },
  { step: "Simulaciones", detail: "Qué habría pasado operando con esas señales, con comisiones reales." },
] as const;

type Badge = "ok" | "degraded" | "unavailable";

function StatusBadge({ status }: { status: Badge }) {
  const styles: Record<Badge, string> = {
    ok: "bg-emerald-500/15 text-emerald-700 dark:text-emerald-300 ring-emerald-500/30",
    degraded: "bg-amber-500/15 text-amber-700 dark:text-amber-300 ring-amber-500/30",
    unavailable: "bg-red-500/15 text-red-700 dark:text-red-300 ring-red-500/30",
  };
  const label =
    status === "ok" ? "funcionando" : status === "degraded" ? "degradado" : "no disponible";
  return (
    <span
      className={`inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium ring-1 ${styles[status]}`}
    >
      {label}
    </span>
  );
}

function QuickLink({ href, title, body }: { href: string; title: string; body: string }) {
  return (
    <Link
      href={href}
      className="block rounded-xl border border-slate-200 bg-white/70 p-5 shadow-sm hover:border-sky-400 dark:border-slate-800 dark:bg-slate-900/60 dark:hover:border-sky-600"
    >
      <span className="font-semibold text-sky-600 dark:text-sky-400">{title}</span>
      <span className="mt-1 block text-sm text-slate-600 dark:text-slate-400">{body}</span>
    </Link>
  );
}

export default async function HomePage() {
  const [info, readiness] = await Promise.all([getApiInfo(), getReadiness()]);

  return (
    <main className="mx-auto flex min-h-screen w-full max-w-5xl flex-col gap-8 px-6 py-14">
      <header className="space-y-3">
        <h1 className="text-3xl font-bold tracking-tight">StockGambling</h1>
        <p className="max-w-3xl text-sm leading-relaxed text-slate-600 dark:text-slate-400">
          Plataforma de investigación para estimar hacia dónde va a moverse el precio de los
          CEDEARs argentinos (BYMA). Ojo: el objetivo es el CEDEAR cotizado en pesos — un
          instrumento distinto de la acción extranjera original, movido por el precio de esa
          acción, el dólar, el ratio de conversión y el humor del mercado local.
        </p>
      </header>

      <Card
        title="Qué podés hacer acá"
        hint="El recorrido habitual, en orden: primero mirás un papel, después qué dice el modelo, después si esa señal habría ganado plata."
      >
        <ol className="grid gap-2 sm:grid-cols-3">
          {[
            {
              n: "1",
              title: "Buscá un CEDEAR",
              body: "Entrá a CEDEARs, buscá por símbolo (p. ej. AAPL) y abrí su ficha.",
            },
            {
              n: "2",
              title: "Pedí una probabilidad",
              body: "En la ficha elegí horizonte y modelo, y mirá la chance de suba. Cerca de 50/50 = no hay señal.",
            },
            {
              n: "3",
              title: "Fijate si habría funcionado",
              body: "En Simulaciones compará la estrategia contra simplemente haber comprado y mantenido.",
            },
          ].map((item) => (
            <li
              key={item.n}
              className="rounded-lg bg-slate-100/70 px-3 py-2.5 text-sm dark:bg-slate-800/60"
            >
              <span className="font-mono text-xs text-slate-400">Paso {item.n}</span>
              <span className="block font-semibold text-slate-800 dark:text-slate-100">
                {item.title}
              </span>
              <span className="mt-0.5 block text-slate-600 dark:text-slate-300">{item.body}</span>
            </li>
          ))}
        </ol>
      </Card>

      <div className="grid gap-4 sm:grid-cols-2">
        <QuickLink
          href="/cedears"
          title="Explorar CEDEARs →"
          body="Buscá papeles, mirá precio real vs teórico, probabilidades por modelo y simulaciones."
        />
        <QuickLink
          href="/backtests"
          title="Ver simulaciones →"
          body="Qué habría ganado cada estrategia contra comprar y mantener, con costos incluidos."
        />
      </div>

      <Card
        title="Cómo se cocina cada número"
        hint="Cada probabilidad que ves pasó por estas etapas, en este orden."
      >
        <ol className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
          {PIPELINE.map((item, i) => (
            <li
              key={item.step}
              className="rounded-lg bg-slate-100/70 px-3 py-2 text-sm dark:bg-slate-800/60"
            >
              <span className="font-mono text-xs text-slate-400">Etapa {i + 1}</span>
              <span className="block font-semibold text-slate-800 dark:text-slate-100">
                {item.step}
              </span>
              <span className="mt-0.5 block text-xs text-slate-600 dark:text-slate-300">
                {item.detail}
              </span>
            </li>
          ))}
        </ol>
      </Card>

      <div className="grid gap-4 sm:grid-cols-2">
        <Card title="Servicio API">
          {info.ok && info.data ? (
            <dl className="space-y-2 text-sm">
              <Row label="Servicio" value={info.data.service} />
              <Row label="Versión" value={info.data.version} />
              <Row label="Entorno" value={info.data.environment} />
              <Row label="Prefijo API" value={info.data.api_prefix} />
              <div className="pt-1">
                <Link
                  href={`${PUBLIC_API_URL}/docs`}
                  className="text-sm font-medium text-sky-600 underline underline-offset-4 hover:text-sky-500 dark:text-sky-400"
                >
                  Abrir documentación interactiva de la API
                </Link>
              </div>
            </dl>
          ) : (
            <Unavailable detail={info.error} />
          )}
        </Card>

        <Card title="Dependencias">
          {readiness.ok && readiness.data ? (
            <ul className="space-y-2 text-sm">
              <DependencyRow
                name={readiness.data.service}
                status={readiness.data.status as Badge}
                detail={`v${readiness.data.version} · ${readiness.data.environment}`}
              />
              {Object.entries(readiness.data.checks).map(([name, check]) => (
                <DependencyRow
                  key={name}
                  name={name === "postgres" ? "Base de datos" : name}
                  status={check.status}
                  detail={`${check.latency_ms} ms`}
                />
              ))}
            </ul>
          ) : (
            <Unavailable detail={readiness.error} />
          )}
        </Card>
      </div>

      <footer className="mt-auto border-t border-slate-200 pt-4 text-xs text-slate-500 dark:border-slate-800">
        Software educativo de investigación. No es asesoramiento de inversión. Los modelos
        devuelven probabilidades, nunca certezas.
      </footer>
    </main>
  );
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-baseline justify-between gap-4">
      <dt className="text-slate-500 dark:text-slate-400">{label}</dt>
      <dd className="font-mono text-slate-800 dark:text-slate-200">{value}</dd>
    </div>
  );
}

function DependencyRow({
  name,
  status,
  detail,
}: {
  name: string;
  status: Badge;
  detail: string;
}) {
  return (
    <li className="flex items-center justify-between gap-3">
      <span className="text-slate-700 dark:text-slate-300">{name}</span>
      <span className="flex items-center gap-2">
        <span className="font-mono text-xs text-slate-500">{detail}</span>
        <StatusBadge status={status} />
      </span>
    </li>
  );
}

function Unavailable({ detail }: { detail: string | null }) {
  return (
    <div className="space-y-2">
      <StatusBadge status="unavailable" />
      <p className="font-mono text-xs break-all text-slate-500">{detail ?? "sin detalle"}</p>
    </div>
  );
}
