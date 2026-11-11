$ErrorActionPreference="Stop"
Set-Location $PSScriptRoot
Write-Host "Starting SARCADE Demo..."
docker compose -f docker-compose.demo.yml up -d --build postgres server

Write-Host "Waiting for SARCADE API..."
$ready=$false
for($i=1;$i -le 60;$i++){
  try {
    $r=Invoke-RestMethod -Uri "http://localhost:8000/health" -TimeoutSec 2
    if($r.status -eq "ok"){ $ready=$true; break }
  } catch {}
  Start-Sleep -Seconds 2
}
if(-not $ready){
  Write-Host "SARCADE Server did not become ready." -ForegroundColor Red
  docker compose -f docker-compose.demo.yml ps -a
  docker compose -f docker-compose.demo.yml logs server --tail=80
  exit 1
}
Write-Host "SARCADE Server is ready." -ForegroundColor Green
Write-Host "API: http://localhost:8000/docs"
Write-Host "To simulate 4 operators:"
Write-Host "docker compose -f docker-compose.demo.yml --profile simulate run --rm simulator"
Write-Host "To verify idempotence:"
Write-Host "docker compose -f docker-compose.demo.yml run --rm simulator python verify.py"
