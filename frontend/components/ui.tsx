import Link from "next/link";
import type { ReactNode } from "react";

export function Card({
  title,
  hint,
  children,
}: {
  title: string;
  hint?: string;
  children: ReactNode;
}) {
  return (
    <section className="rounded-xl border border-slate-200 bg-white/70 p-5 shadow-sm dark:border-slate-800 dark:bg-slate-900/60">
      <h2 className="mb-1 text-sm font-semibold tracking-wide text-slate-500 uppercase dark:text-slate-400">
        {title}
      </h2>
      {hint ? <p className="mb-3 text-xs text-slate-500 dark:text-slate-400">{hint}</p> : null}
      {children}
    </section>
  );
}

export function TopNav() {
  const link =
    "rounded-lg px-3 py-1.5 text-sm font-medium text-slate-600 hover:bg-slate-100 hover:text-slate-900 dark:text-slate-300 dark:hover:bg-slate-800 dark:hover:text-white";
  return (
    <nav className="flex flex-wrap items-center gap-1 border-b border-slate-200 pb-4 dark:border-slate-800">
      <Link href="/" className="mr-2 text-lg font-bold tracking-tight">
        StockGambling
      </Link>
      <Link href="/cedears" className={link}>
        CEDEARs
      </Link>
      <Link href="/backtests" className={link}>
        Simulaciones
      </Link>
    </nav>
  );
}

export function PageShell({ children }: { children: ReactNode }) {
  return (
    <main className="mx-auto flex min-h-screen w-full max-w-5xl flex-col gap-6 px-6 py-8">
      <TopNav />
      {children}
      <footer className="mt-auto border-t border-slate-200 pt-4 text-xs text-slate-500 dark:border-slate-800">
        Software educativo de investigación. No es asesoramiento de inversión. Los modelos
        devuelven probabilidades, nunca certezas.
      </footer>
    </main>
  );
}

export function Unavailable({ detail }: { detail: string | null }) {
  return (
    <div className="space-y-2">
      <span className="inline-flex items-center rounded-full bg-red-500/15 px-2.5 py-0.5 text-xs font-medium text-red-700 ring-1 ring-red-500/30 dark:text-red-300">
        unavailable
      </span>
      <p className="font-mono text-xs break-all text-slate-500">{detail ?? "sin detalle"}</p>
      <p className="text-sm text-slate-500">
        El backend no respondió. Levantalo con <code>docker compose up</code> e intentá de
        nuevo; este panel prefiere no mostrar nada antes que inventar un dato.
      </p>
    </div>
  );
}

export function Empty({ message }: { message: string }) {
  return <p className="text-sm text-slate-500 dark:text-slate-400">{message}</p>
}

export function Row({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div className="flex items-baseline justify-between gap-4">
      <dt className="text-slate-500 dark:text-slate-400">{label}</dt>
      <dd className="text-right font-mono text-slate-800 dark:text-slate-200">{value}</dd>
    </div>
  );
}
