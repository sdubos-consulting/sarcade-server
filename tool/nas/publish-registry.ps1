# Publication automatique du serveur du NAS, lancée par Claude après chaque
# fusion (serveur ou application) poussée vers gitlab. Prérequis : une fois,
# tool\nas\registre-installer.ps1 (registre sur le NAS, Watchtower, connexion).
#
#   powershell -NoProfile -ExecutionPolicy Bypass -File tool\nas\publish-registry.ps1 [-Force]
#
# 1. refuse si un événement est en cours sur le serveur du NAS (sauf -Force) ;
# 2. client web de gitlab/main de l'application (construit avant si absent),
#    et fichiers de la page /telechargements : installeur Windows, APK,
#    sources (tool\build-downloads.ps1 de l'application, construits si absents) ;
# 3. image du serveur de gitlab/main construite SUR L'I9, client web intégré,
#    commit renseigné ; publiée dans le registre du NAS (:main et :<commit>) ;
# 4. Watchtower la met en service (au plus 5 minutes) ; le script attend que
#    /version et sarcade_build.json donnent les bons commits (10 minutes au plus).
param([switch]$Force)
$ErrorActionPreference = 'Continue'
$nasUrl = 'http://192.168.0.110:8000'
$registry = '192.168.0.110:5000'
$app = 'C:\dev\sarcade\sarcade-app'
$srv = 'C:\dev\sarcade\sarcade-server'
$wt = "C:\dev\sarcade\wt\nas-image-$(Get-Date -Format 'yyyyMMdd-HHmmss')"
function Fail([string]$m) { Write-Host "ÉCHEC : $m" -ForegroundColor Red; exit 1 }

Write-Host '== 1. Événement en cours ?' -ForegroundColor Cyan
# Jeton d'administrateur (tool\nas\jeton-admin-installer.ps1), jamais affiché.
$headers = @{}
$tokFile = 'C:\dev\secrets\nas\admin-token.txt'
if (Test-Path $tokFile) { $headers['Authorization'] = 'Bearer ' + [IO.File]::ReadAllText($tokFile).Trim() }
try { $plan = Invoke-RestMethod -TimeoutSec 10 -Headers $headers "$nasUrl/api/v0.1/admin/updates/plan" } catch { Fail "serveur du NAS injoignable ($($_.Exception.Message))" }
# Les événements en brouillon (essais, « Test NAS 1 ») ne bloquent pas
# (décision du porteur, 8 novembre 2026) : seuls les événements ouverts pour
# de vrai (exercice, opération) arrêtent la publication.
$drafts = @()
try { $drafts = @((Invoke-RestMethod -TimeoutSec 10 "$nasUrl/api/v0.1/events") | Where-Object { $_.status -eq 'draft' } | ForEach-Object { $_.id }) } catch {}
$active = @($plan.active_events | Where-Object { $drafts -notcontains $_ })
$ignored = @($plan.active_events | Where-Object { $drafts -contains $_ })
if ($ignored.Count -gt 0) { Write-Host "en brouillon, ignorés : $($ignored -join ', ')" }
if ($active.Count -gt 0 -and -not $Force) { Fail "événement en cours : $($active -join ', ') ; rien n'est publié (relancer avec -Force pour passer outre)" }
if ($active.Count -gt 0) { Write-Host "événement en cours ($($active -join ', ')), publication forcée" -ForegroundColor Yellow } else { Write-Host 'aucun' }

Write-Host '== 2. Client web' -ForegroundColor Cyan
git -C $app fetch -q gitlab; git -C $srv fetch -q gitlab
$appSha = (git -C $app rev-parse --short gitlab/main).Trim()
$srvSha = (git -C $srv rev-parse --short gitlab/main).Trim()
$srvFull = (git -C $srv rev-parse gitlab/main).Trim()
$tar = "C:\dev\sarcade\publications\web\sarcade-web-$appSha.tar.gz"
if (-not (Test-Path $tar)) { powershell -NoProfile -ExecutionPolicy Bypass -File "$app\tool\build-web.ps1" }
if (-not (Test-Path $tar)) { Fail "client web $appSha non construit" }
$dl = "C:\dev\sarcade\publications\downloads\$appSha"
if (-not (Test-Path "$dl\app-files.json")) { powershell -NoProfile -ExecutionPolicy Bypass -File "$app\tool\build-downloads.ps1" }
if (-not (Test-Path "$dl\app-files.json")) { Fail "fichiers de téléchargement $appSha non construits" }
Write-Host "application main@$appSha ; serveur main@$srvSha"

Write-Host '== 3. Image du serveur (i9)' -ForegroundColor Cyan
Get-ChildItem 'C:\dev\sarcade\wt' -Directory -Filter 'nas-image-*' -ErrorAction SilentlyContinue | ForEach-Object { git -C $srv worktree remove --force $_.FullName 2>$null }
git -C $srv worktree prune
git -C $srv worktree add -q --detach $wt gitlab/main
if ($LASTEXITCODE -ne 0) { Fail "worktree $wt" }
try {
  Copy-Item -Force $tar "$wt\sarcade-web.tar.gz"
  New-Item -ItemType Directory -Force "$wt\downloads" | Out-Null
  Get-ChildItem $dl -File | Where-Object { $_.Name -ne 'app-files.json' } | Copy-Item -Destination "$wt\downloads" -Force
  git -C $srv archive --format=tar.gz --prefix=sarcade-server/ -o "$wt\downloads\sarcade-server-sources-$srvSha.tar.gz" gitlab/main
  python -I "$wt\tool\nas\downloads_manifest.py" "$wt\downloads" "$dl\app-files.json" "$wt\downloads\sarcade-server-sources-$srvSha.tar.gz" "main@$srvSha"
  if ($LASTEXITCODE -ne 0) { Fail 'manifeste des téléchargements' }
  # Cartes hors connexion (tool\tiles\build-offline-basemaps.ps1, une fois par mois).
  New-Item -ItemType Directory -Force "$wt\tiles" | Out-Null
  Get-ChildItem 'C:\dev\sarcade\cartes\paquets' -Filter '*.mbtiles' -ErrorAction SilentlyContinue | Copy-Item -Destination "$wt\tiles" -Force
  # --pull : image de base (python:3.15-slim, Debian) reprise à jour à chaque publication, avec les correctifs de
  # sécurité de Debian (carte « Veille des dépendances », choix 2, 11/11/2026).
  docker build -q --pull --build-arg SARCADE_WEB=local --build-arg SARCADE_DOWNLOADS=local --build-arg SARCADE_TILES=local --build-arg "SARCADE_COMMIT=$srvFull" -t "${registry}/sarcade-server:main" -t "${registry}/sarcade-server:$srvSha" $wt
  if ($LASTEXITCODE -ne 0) { Fail 'construction de l''image' }
  docker push -q "${registry}/sarcade-server:$srvSha" | Out-Null
  docker push -q "${registry}/sarcade-server:main"
  if ($LASTEXITCODE -ne 0) { Fail 'envoi au registre du NAS (connexion faite par registre-installer.ps1 ?)' }
} finally {
  Set-Location $srv
  git -C $srv worktree remove --force $wt 2>$null
}

Write-Host '== 4. Mise en service par Watchtower (5 minutes au plus)' -ForegroundColor Cyan
$deadline = (Get-Date).AddMinutes(10)
do {
  Start-Sleep 20
  try { $v = Invoke-RestMethod -TimeoutSec 5 "$nasUrl/version"; $b = Invoke-RestMethod -TimeoutSec 5 "$nasUrl/sarcade_build.json" } catch { $v = $null; $b = $null }
  $ok = $v -and $v.commit_short -eq $srvSha -and $b -and $b.build -eq "main@$appSha"
} until ($ok -or (Get-Date) -gt $deadline)
if (-not $ok) { Fail "le NAS n'est pas passé sur main@$srvSha / main@$appSha en 10 minutes (voir les journaux de Watchtower)" }
Write-Host "NAS à jour : serveur main@$srvSha, client web main@$appSha ($($b.version))." -ForegroundColor Green
try { $d = Invoke-RestMethod -TimeoutSec 5 "$nasUrl/api/v0.1/downloads"; Write-Host "Téléchargements : $(@($d.files).Count) fichiers sur $nasUrl/telechargements" -ForegroundColor Green } catch { Write-Host 'Téléchargements : page absente (serveur plus ancien ?)' -ForegroundColor Yellow }
