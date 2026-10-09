# StockGambling web

Next.js dashboard for the StockGambling CEDEAR research platform.

## What it does (Phase 10)

* Infrastructure status plus dashboard entry points on `/`.
* `/cedears`: search of the stored CEDEAR universe (empty query browses it).
* `/cedears/[symbol]`: instrument panel (underlying, ratio, program status),
  actual-vs-theoretical chart with premium/discount, three-market small multiples
  (each series on its own market calendar - never overlaid), a prediction panel
  with a horizon selector (1 day to 2 years) served from the registered
  `(name, horizon)` artifact, registered models with walk-forward aggregates and
  one-sided-window flags, and the symbol's backtests vs buy-and-hold.
* `/backtests` and `/backtests/[id]`: persisted runs with equity-vs-benchmark
  charts, metric tables and closed round-trip trades.
* `/api/health` route handler that mirrors upstream readiness and returns `503`
  when the API is degraded. This is also the container healthcheck target.

Charts are dependency-free SVG. Predictions are shown as probabilities with the
weak-signal framing, never as certainties.

## Environment

| Variable              | Used by        | Default                 | Meaning                                        |
| --------------------- | -------------- | ----------------------- | ---------------------------------------------- |
| `API_INTERNAL_URL`    | server         | `http://localhost:8000` | Base URL reachable **from inside** the network. |
| `NEXT_PUBLIC_API_URL` | browser        | `http://localhost:8000` | Base URL reachable **from the user's browser**. |
| `PORT`                | server         | `3000`                  | Listen port.                                   |

Both URLs exist deliberately. Server components and route handlers talk to
`http://api:8000` on the Compose network; browser code must go through the published host
port. Conflating them is the classic container networking bug.

## Commands

```bash
npm install
npm run dev        # dev server on :3000
npm run lint       # eslint (flat config, native next configs)
npm run typecheck  # tsc --noEmit
npm run build      # production build (output: standalone)
npm run start      # serve the production build
```

## Docker

`next build` emits `.next/standalone`, so the runtime image contains only the generated
server bundle plus `public/` and `.next/static/`. The container runs as the unprivileged
`nextjs` user and is healthchecked through `/api/health`.

## Conventions

* App Router, `src/`-less layout, `@/*` path alias.
* Server components by default. Add `"use client"` only for genuine interactivity.
* Tailwind CSS v4 (CSS-first config in `app/globals.css`).
* No web font is fetched at build time, so the image builds without outbound network.
* API calls go through `lib/api.ts`; components never build URLs by hand.
* Every API call returns `{ ok, data, error }` so a degraded backend renders a clear
  message instead of throwing.
