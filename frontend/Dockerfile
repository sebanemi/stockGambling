# syntax=docker/dockerfile:1

# ===========================================================================
# StockGambling frontend image (Next.js dashboard)
# ===========================================================================
# Build produces `.next/standalone`, so the runtime layer only needs Node and
# the generated server bundle - no source, no node_modules tree to prune.
# ===========================================================================

# ---------------------------------------------------------------------------
# deps: install node_modules from the lockfile
# ---------------------------------------------------------------------------
FROM node:22-alpine AS deps
WORKDIR /app
RUN apk add --no-cache libc6-compat
COPY package.json package-lock.json ./
RUN npm ci

# ---------------------------------------------------------------------------
# builder: type-check and produce the standalone server bundle
# ---------------------------------------------------------------------------
FROM node:22-alpine AS builder
WORKDIR /app
ENV NEXT_TELEMETRY_DISABLED=1
ENV NODE_ENV=production
COPY --from=deps /app/node_modules ./node_modules
COPY . .
RUN npm run build

# ---------------------------------------------------------------------------
# runner: minimal runtime
# ---------------------------------------------------------------------------
FROM node:22-alpine AS runner
WORKDIR /app

ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    PORT=3000 \
    HOSTNAME=0.0.0.0

RUN addgroup --system --gid 1001 nodejs \
    && adduser --system --uid 1001 nextjs

COPY --from=builder --chown=nextjs:nodejs /app/public ./public
COPY --from=builder --chown=nextjs:nodejs /app/.next/standalone ./
COPY --from=builder --chown=nextjs:nodejs /app/.next/static ./.next/static

USER nextjs
EXPOSE 3000

# `wget` ships with busybox, so no extra package is required.
HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=3 \
    CMD wget --spider -q http://127.0.0.1:3000/api/health || exit 1

CMD ["node", "server.js"]
