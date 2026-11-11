param(
  [Parameter(Mandatory=$true)][string]$EventId
)
$ErrorActionPreference="Stop"
$compose="docker-compose.demo.yml"
Write-Host "SARCADE Offline First V0.1 recipe" -ForegroundColor Cyan
Write-Host "Event: $EventId"
Write-Host ""
Write-Host "1. Keep SARCADE App open and confirm it is connected."
Write-Host "2. Press ENTER to stop only the API server. PostgreSQL remains running."
Read-Host | Out-Null
docker compose -f $compose stop server
Write-Host "SERVER OFFLINE" -ForegroundColor Yellow
Write-Host "In SARCADE, create a message containing exactly: OFFLINE-V01"
Write-Host "Optionally enable GPS/share position or create other offline actions."
Write-Host "Confirm the app shows pending Outbox operations."
Write-Host "Press ENTER when offline actions are complete."
Read-Host | Out-Null
Write-Host "Restarting server..."
docker compose -f $compose start server
for($i=0;$i -lt 30;$i++){
  try { $h=Invoke-RestMethod "http://localhost:8000/health" -TimeoutSec 2; if($h.status -eq "ok"){break} } catch {}
  Start-Sleep -Seconds 1
}
Write-Host "SERVER ONLINE" -ForegroundColor Green
Write-Host "In SARCADE, click Synchroniser (or wait for automatic retry)."
Write-Host "Wait until the pending counter reaches 0, then press ENTER."
Read-Host | Out-Null
docker compose -f $compose run --rm --entrypoint sh -e SARCADE_EVENT_ID=$EventId -e SARCADE_OFFLINE_MARKER=OFFLINE-V01 simulator -c "pip install -q httpx && python /demo/offline_verify.py"
