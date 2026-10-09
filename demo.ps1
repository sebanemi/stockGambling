# StockGambling one-command demo (PowerShell).
#
#   .\demo.ps1                 full stack + real data + trained models
#   .\demo.ps1 -SkipTrain      stack + data only (faster)
#
# Steps: build + start everything, wait for a healthy API, migrate the
# database, bootstrap the real CEDEAR universe with market data, and train
# one model per symbol and horizon. Daily use afterwards is
# `docker compose stop` / `docker compose start` (keeps containers, database
# and artifacts); `docker compose down -v` deletes everything.

param([switch]$SkipTrain)

$ErrorActionPreference = "Stop"

function Wait-Healthy {
  for ($i = 0; $i -lt 60; $i++) {
    try {
      $status = (Invoke-RestMethod http://localhost:8000/health/ready -TimeoutSec 5).status
      if ($status -eq "ok") { return }
    } catch { Start-Sleep -Seconds 5 }
  }
  throw "API did not become healthy in time (docker compose logs api)"
}

docker compose up -d --build
Wait-Healthy
docker compose exec api alembic upgrade head
docker compose exec api python -m app.bootstrap
if (-not $SkipTrain) {
  docker compose exec api python -m app.train
  docker compose exec api python -m app.demo_backtests
}
Write-Host "Demo ready: http://localhost:3000"
