# StockGambling web

Next.js dashboard for the StockGambling CEDEAR research platform.

## What it does today (Phase 1)

* Infrastructure status page: API service/version/environment, plus live readiness for
  PostgreSQL and Redis, with per-dependency latency.
* The canonical pipeline with its phase numbers, so the current scope is always visible.
* `/api/health` route handler that mirrors upstream readiness and returns `503` when the
  API is degraded. This is also the container healthcheck target.

No predictions, charts or CEDEAR data are rendered yet - those arrive in Phase 10.

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
