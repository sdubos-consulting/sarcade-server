# SARCADE - Diagnostic des prerequis Windows
$ErrorActionPreference="Continue"
Write-Host "=== SARCADE prerequisite check ==="
Write-Host ("Windows: " + [Environment]::OSVersion.VersionString)
Write-Host "--- WSL ---"
wsl.exe --status
Write-Host "--- Git ---"
git --version
Write-Host "--- Docker ---"
docker version
Write-Host "--- Docker Compose ---"
docker compose version
Write-Host "--- SARCADE ports ---"
Get-NetTCPConnection -State Listen -LocalPort 8000,5432 -ErrorAction SilentlyContinue | Format-Table LocalAddress,LocalPort,OwningProcess
Write-Host "=== End ==="
