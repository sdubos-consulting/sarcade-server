# Cartes hors connexion fabriquées sur l'i9 (carte « Cartes hors connexion
# sur le téléphone », validée le 9 novembre 2026, choix 1 et 2).
#
#   powershell -NoProfile -ExecutionPolicy Bypass -File C:\dev\sarcade\sarcade-server\tool\tiles\build-offline-basemaps.ps1
#
# 1. extrait OpenStreetMap de l'Île-de-France (Geofabrik, ODbL), empreinte
#    MD5 vérifiée ;
# 2. tuiles vectorielles du département par Planetiler (conteneur), avec les
#    données Natural Earth et lacs gardées dans C:\dev\sarcade\cartes\sources
#    (eau de mer : fichier vide, pas de côte dans le département) ;
# 3. images PNG du zoom 6 au zoom 16 dessinées par TileServer GL
#    (conteneur, style « basic-preview »), rangées dans un MBTiles par
#    tool\tiles\render_mbtiles.py ;
# 4. résultat dans C:\dev\sarcade\cartes\paquets\osm.mbtiles, repris par
#    tool\nas\publish-registry.ps1 dans l'image du serveur (NAS et serveur
#    mobile). Jamais le serveur de tuiles public d'OpenStreetMap.
# Lancé une fois par mois par la tâche planifiée « SARCADE - cartes hors
# connexion ». Relançable ; l'ancien paquet reste en place en cas d'échec.
param(
  [string]$Bounds = '1.44,48.43,2.24,49.09',   # Yvelines (ouest,sud,est,nord)
  [int]$MinZoom = 6,
  [int]$MaxZoom = 16
)
$ErrorActionPreference = 'Continue'
$env:MSYS_NO_PATHCONV = '1'
$root = 'C:\dev\sarcade\cartes'
$src = "$root\sources"; $work = "$root\work"; $out = "$root\paquets"
$srv = 'C:\dev\sarcade\sarcade-server'
foreach ($d in $src, $work, $out) { New-Item -ItemType Directory -Force $d | Out-Null }
$log = "$work\construction-$(Get-Date -Format 'yyyyMMdd-HHmmss').log"
Start-Transcript -Path $log | Out-Null
function Fail([string]$m) { Write-Host "ÉCHEC : $m" -ForegroundColor Red; docker rm -f sarcade-tileserver 2>$null | Out-Null; Stop-Transcript | Out-Null; exit 1 }

try {
  Write-Host '== 1. Extrait OpenStreetMap (Geofabrik)'
  $pbf = "$src\ile-de-france-latest.osm.pbf"
  $base = 'https://download.geofabrik.de/europe/france/ile-de-france-latest.osm.pbf'
  curl.exe -sSL -o "$pbf.part" $base
  curl.exe -sSL -o "$pbf.md5" "$base.md5"
  $want = ((Get-Content "$pbf.md5" -Raw) -split '\s+')[0].ToLower()
  $got = (Get-FileHash -Algorithm MD5 "$pbf.part").Hash.ToLower()
  if ($want -ne $got) { Fail "empreinte MD5 différente ($got au lieu de $want)" }
  Move-Item -Force "$pbf.part" $pbf

  Write-Host '== 2. Tuiles vectorielles (Planetiler)'
  foreach ($f in 'natural_earth_vector.sqlite.zip', 'lake_centerline.shp.zip', 'water-polygons-split-3857.zip') {
    if (-not (Test-Path "$src\$f")) { Fail "source absente : $src\$f (voir la note de la carte)" }
  }
  docker run --rm -e JAVA_TOOL_OPTIONS=-Xmx8g -v "${root}:/data" ghcr.io/onthegomap/planetiler:latest `
    --osm-path=/data/sources/ile-de-france-latest.osm.pbf --water-polygons-path=/data/sources/water-polygons-split-3857.zip `
    --natural-earth-path=/data/sources/natural_earth_vector.sqlite.zip --lake-centerlines-path=/data/sources/lake_centerline.shp.zip `
    --output=/data/work/vector.mbtiles "--bounds=$Bounds" --force *> "$work\planetiler.log"
  if ($LASTEXITCODE -ne 0 -or -not (Test-Path "$work\vector.mbtiles")) { Fail "Planetiler (journal : $work\planetiler.log)" }

  Write-Host '== 3. Images (TileServer GL)'
  docker rm -f sarcade-tileserver 2>$null | Out-Null
  docker run --rm -d --name sarcade-tileserver -p 127.0.0.1:8080:8080 -v "${work}:/data" maptiler/tileserver-gl:latest --file /data/vector.mbtiles | Out-Null
  $ready = $false
  for ($i = 0; $i -lt 60 -and -not $ready; $i++) { try { Invoke-WebRequest -UseBasicParsing -TimeoutSec 3 http://127.0.0.1:8080/styles.json | Out-Null; $ready = $true } catch { Start-Sleep 2 } }
  if (-not $ready) { Fail 'TileServer GL ne répond pas' }
  python -I "$srv\tool\tiles\render_mbtiles.py" --url 'http://127.0.0.1:8080/styles/basic-preview/256/{z}/{x}/{y}.png' `
    --bounds $Bounds --minzoom $MinZoom --maxzoom $MaxZoom --out "$work\osm.mbtiles" --name 'OpenStreetMap Yvelines' --workers 12
  $code = $LASTEXITCODE
  docker rm -f sarcade-tileserver 2>$null | Out-Null
  if ($code -ne 0 -or -not (Test-Path "$work\osm.mbtiles")) { Fail 'rendu des images' }

  Write-Host '== 4. Paquet'
  Move-Item -Force "$work\osm.mbtiles" "$out\osm.mbtiles"
  Remove-Item -Force "$work\vector.mbtiles" -ErrorAction SilentlyContinue
  $mb = [math]::Round((Get-Item "$out\osm.mbtiles").Length / 1MB)
  Write-Host "Paquet : $out\osm.mbtiles ($mb Mo) ; repris à la prochaine publication du NAS." -ForegroundColor Green
}
finally { Stop-Transcript | Out-Null }
