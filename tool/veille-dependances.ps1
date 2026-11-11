# Veille des dépendances sur l'i9 (carte « GitHub de nouveau actif
# (10 novembre 2026) », choix 4b à 4g) : remplace Dependabot et le contrôle de
# nuit de GitHub. Examine l'application et le serveur (branche main, pas la
# copie de travail). Windows PowerShell 5.1, sans droits d'administrateur.
#
#   powershell -NoProfile -ExecutionPolicy Bypass -File tool\veille-dependances.ps1 -Failles   [-Essai] [-Notifier]
#   powershell -NoProfile -ExecutionPolicy Bypass -File tool\veille-dependances.ps1 -Versions  [-Essai]
#
# -Failles (chaque jour à 6 h, tool\veille-installer.ps1) : OSV-Scanner sur
#   pubspec.lock, ios/Podfile.lock (s'il existe), l'inventaire Android (s'il
#   existe dans C:\dev\sarcade\publications\sbom\), uv.lock et l'inventaire de
#   l'image servie par le NAS ; registre des failles écartées de chaque dépôt
#   (osv-scanner.toml : raison et date de réexamen obligatoires) ;
#   notification Windows s'il y a une faille.
# -Versions (chaque lundi à 7 h) : flutter pub outdated, paquets Python (uv),
#   nouvelle version stable de Flutter, images de base des Dockerfile.
# Résultat : UNE carte « Veille des dépendances » en tête de « À valider » du
# Kanban, mise à jour sans doublon, détail dans sa fiche ; rien si tout est à
# jour. Rapport complet : C:\dev\sarcade\publications\veille\.
# -Essai : Kanban et fiche écrits dans C:\dev\sarcade\publications\veille\essai\
#   (copie du Kanban), jamais dans le coffre ; pas de notification sauf -Notifier.
# Aucune mise à jour n'est appliquée : après validation, branche, CI, fusion.
param([switch]$Failles, [switch]$Versions, [switch]$Essai, [switch]$Notifier, [string]$Ref = 'main')
$ErrorActionPreference = 'Continue'
if ($Failles -eq $Versions) { Write-Host 'Préciser -Failles ou -Versions (un seul).' -ForegroundColor Red; exit 2 }
$mode = if ($Failles) { 'failles' } else { 'versions' }
[Console]::OutputEncoding = New-Object Text.UTF8Encoding($false)
$wingetLinks = "$env:LOCALAPPDATA\Microsoft\WinGet\Links"
if (($env:Path -split ';') -notcontains $wingetLinks) { $env:Path += ";$wingetLinks" }

$app = 'C:\dev\sarcade\sarcade-app'
$srv = 'C:\dev\sarcade\sarcade-server'
$vault = 'C:\dev\sarcade\sarcade-vault\SARCADE\08 - Backlog'
$out = 'C:\dev\sarcade\publications\veille'
$nasUrl = 'http://192.168.0.110:8000'
$helper = Join-Path $PSScriptRoot 'veille_dependances.py'
$now = Get-Date
$stamp = $now.ToString('yyyyMMdd-HHmmss')
$work = Join-Path $out "travail\$mode-$stamp"
$journal = New-Object System.Collections.Generic.List[string]
function Say([string]$m) { $journal.Add($m); Write-Host $m }
function Text($lines) { (@($lines) | ForEach-Object { "$_" }) -join "`n" }
function WriteUtf8([string]$path, [string]$text) { [IO.File]::WriteAllText($path, $text, (New-Object Text.UTF8Encoding($false))) }

if ($Essai) {
  $base = Join-Path $out 'essai'
  New-Item -ItemType Directory -Force "$base\fiches" | Out-Null
  $kanban = Join-Path $base 'SARCADE - Kanban.md'
  if (-not (Test-Path $kanban)) { Copy-Item "$vault\SARCADE - Kanban.md" $kanban }
  $fiches = Join-Path $base 'fiches'
  $rapports = Join-Path $base 'rapports'
  Say "ESSAI : Kanban et fiche dans $base (le coffre n'est pas touché)"
} else {
  $kanban = Join-Path $vault 'SARCADE - Kanban.md'
  $fiches = Join-Path $vault 'fiches'
  $rapports = $out
}
New-Item -ItemType Directory -Force $work | Out-Null
Say "== Veille des dépendances : $mode, $Ref, $($now.ToString('yyyy-MM-dd HH:mm'))"

# Fichiers de la branche suivie (jamais la copie de travail).
function Extract([string]$repo, [string]$dest, [string[]]$paths) {
  New-Item -ItemType Directory -Force $dest | Out-Null
  $present = @($paths | Where-Object { git -C $repo cat-file -e "${Ref}:$_" 2>$null; $LASTEXITCODE -eq 0 })
  if ($present.Count -eq 0) { return @() }
  $tar = Join-Path $work ("{0}.tar" -f (Split-Path $dest -Leaf))
  git -C $repo archive -o $tar $Ref -- @present
  if ($LASTEXITCODE -ne 0) { Say "git archive $repo : échec"; return @() }
  tar -xf $tar -C $dest
  Remove-Item $tar -Force
  return $present
}
$appFiles = Extract $app "$work\app" @('pubspec.yaml', 'pubspec.lock', 'ios/Podfile.lock', 'osv-scanner.toml', 'tool/osv_nightly.py')
$srvFiles = Extract $srv "$work\srv" @('pyproject.toml', 'uv.lock', 'osv-scanner.toml', 'Dockerfile', 'docker/caddy/Dockerfile', 'tool/security/osv_input.py')
Say ("application {0} : {1}" -f (git -C $app rev-parse --short $Ref), ($appFiles -join ', '))
Say ("serveur     {0} : {1}" -f (git -C $srv rev-parse --short $Ref), ($srvFiles -join ', '))

$data = [ordered]@{ mode = $mode; date = $now.ToString('yyyy-MM-dd'); heure = $now.ToString('HH:mm') }

if ($mode -eq 'failles') {
  $osv = (Get-Command osv-scanner -ErrorAction SilentlyContinue).Source
  if (-not $osv) { Say 'OSV-Scanner absent : winget install --id Google.OSVScanner -e --scope user' }
  $scans = New-Object System.Collections.Generic.List[object]
  function Scan([string]$depot, [string]$fichier, [string]$path, [string]$config) {
    $report = Join-Path $work ("osv-{0}-{1}.json" -f $depot, ($fichier -replace '[^A-Za-z0-9.]', '-'))
    $entry = [ordered]@{ depot = $depot; fichier = $fichier; osv = $null; erreur = $null }
    if (-not $osv) { $entry.erreur = 'OSV-Scanner absent'; $scans.Add($entry); return }
    $cfgArgs = @(); if ($config -and (Test-Path $config)) { $cfgArgs = @('--config', $config) }
    $err = & $osv scan source -L $path @cfgArgs --format json --output-file $report 2>&1 | ForEach-Object { "$_" }
    $code = $LASTEXITCODE
    if (($code -eq 0 -or $code -eq 1) -and (Test-Path $report)) {
      $entry.osv = $report
      Say ("OSV {0}/{1} : {2}" -f $depot, $fichier, $(if ($code -eq 1) { 'failles trouvées' } else { 'rien' }))
    } else {
      $entry.erreur = "code $code : " + (@($err) | Select-Object -Last 1)
      Say ("OSV {0}/{1} : ÉCHEC ({2})" -f $depot, $fichier, $entry.erreur)
    }
    $scans.Add($entry)
  }
  $appCfg = "$work\app\osv-scanner.toml"; $srvCfg = "$work\srv\osv-scanner.toml"
  if (Test-Path "$work\app\pubspec.lock") { Scan 'sarcade-app' 'pubspec.lock' "$work\app\pubspec.lock" $appCfg }
  if (Test-Path "$work\app\ios\Podfile.lock") { Scan 'sarcade-app' 'ios/Podfile.lock' "$work\app\ios\Podfile.lock" $appCfg } else { Say 'ios/Podfile.lock : absent de la branche (iPhone construit par Xcode Cloud), non contrôlé' }
  $android = Get-ChildItem 'C:\dev\sarcade\publications\sbom' -Filter '*android*.cdx.json' -ErrorAction SilentlyContinue | Sort-Object LastWriteTime -Descending | Select-Object -First 1
  if ($android) { Scan 'sarcade-app' "inventaire Android $($android.Name)" $android.FullName $appCfg } else { Say 'inventaire Android : absent (C:\dev\sarcade\publications\sbom\*android*.cdx.json), non contrôlé' }
  if (Test-Path "$work\srv\uv.lock") { Scan 'sarcade-server' 'uv.lock' "$work\srv\uv.lock" $srvCfg }
  # Image servie par le NAS : son inventaire (/sarcade-sbom.cdx.json), avec la
  # version Debian coupée à son numéro majeur (tool/security/osv_input.py).
  $image = "$work\sarcade-image.cdx.json"
  try {
    Invoke-WebRequest -UseBasicParsing -TimeoutSec 20 "$nasUrl/sarcade-sbom.cdx.json" -OutFile "$work\image-brut.json"
    python -X utf8 -I "$work\srv\tool\security\osv_input.py" "$work\image-brut.json" $image | Out-Null
    if ($LASTEXITCODE -eq 0 -and (Test-Path $image)) { Scan 'sarcade-server' 'image du NAS' $image $srvCfg; $data.image_sbom = $image } else { Say 'image du NAS : inventaire illisible, non contrôlé' }
  } catch { Say "image du NAS : inventaire non disponible ($($_.Exception.Message)), non contrôlé" }
  # Registres des failles écartées : raison et date de réexamen obligatoires.
  $registres = New-Object System.Collections.Generic.List[object]
  foreach ($r in @(@('sarcade-app', $appCfg), @('sarcade-server', $srvCfg))) {
    if (-not (Test-Path $r[1])) { Say "$($r[0])/osv-scanner.toml : absent"; continue }
    $lines = @(python -X utf8 -I "$work\app\tool\osv_nightly.py" check-config $r[1] 2>&1 | ForEach-Object { ("$_" -replace '^::\w+ title=[^:]*::', '') })
    $registres.Add([ordered]@{ depot = $r[0]; fichier = 'osv-scanner.toml'; code = $LASTEXITCODE; lignes = $lines })
    Say ("{0}/osv-scanner.toml : {1}" -f $r[0], ($lines -join ' ; '))
  }
  $data.scans = $scans
  $data.registres = $registres
} else {
  # Application : flutter pub outdated sur une copie détachée de la branche.
  $wtApp = "C:\dev\sarcade\wt\veille-app-$stamp"
  git -C $app worktree add -q --detach $wtApp $Ref 2>&1 | Out-Null
  if ($LASTEXITCODE -eq 0) {
    Push-Location $wtApp
    try {
      flutter pub get 2>&1 | Out-Null
      $pub = Text (flutter pub outdated --json 2>$null)
      if ($pub -match '"packages"') { WriteUtf8 "$work\pub-outdated.json" $pub; $data.pub = "$work\pub-outdated.json"; Say 'flutter pub outdated : fait' }
      else { $data.pub_erreur = 'flutter pub outdated sans résultat'; Say $data.pub_erreur }
    } finally { Pop-Location; git -C $app worktree remove --force $wtApp 2>&1 | Out-Null }
  } else { $data.pub_erreur = "copie de $Ref impossible"; Say $data.pub_erreur }
  # Serveur : uv sur pyproject.toml et uv.lock de la branche, sans les modifier.
  if (Test-Path "$work\srv\uv.lock") {
    Push-Location "$work\srv"
    try {
      $data.uv_maj = Text (uv lock --upgrade --dry-run 2>&1)
      $data.uv_arbre = Text (uv tree --outdated --depth 1 --frozen --all-groups 2>&1)
      Say 'uv : fait'
    } finally { Pop-Location }
  } else { $data.uv_erreur = 'uv.lock absent'; Say $data.uv_erreur }
  # Flutter : version stable publiée et version installée sur l'i9.
  $fl = [ordered]@{}
  try {
    $rel = Invoke-RestMethod -TimeoutSec 30 'https://storage.googleapis.com/flutter_infra_release/releases/releases_windows.json'
    $hash = $rel.current_release.stable
    $st = @($rel.releases | Where-Object { $_.hash -eq $hash }) | Select-Object -First 1
    $fl.stable = $st.version; $fl.date = ([string]$st.release_date).Substring(0, 10)
    $fl.installe = ((Text (flutter --version --machine 2>$null)) | ConvertFrom-Json).frameworkVersion
    Say "Flutter : stable $($fl.stable), installée $($fl.installe)"
  } catch { $fl.erreur = $_.Exception.Message; Say "Flutter : $($fl.erreur)" }
  $data.flutter = $fl
  # Images de base : nouvelle version, ou même étiquette reconstruite (le
  # condensé local diffère de celui de Docker Hub).
  $dfs = @(@('Dockerfile', "$work\srv\Dockerfile"), @('docker/caddy/Dockerfile', "$work\srv\docker\caddy\Dockerfile")) | Where-Object { Test-Path $_[1] }
  $digests = @{}
  foreach ($df in $dfs) {
    foreach ($m in [regex]::Matches([IO.File]::ReadAllText($df[1]), '(?m)^FROM\s+(\S+:\S+)')) {
      $refImg = $m.Groups[1].Value
      if ($refImg -match '\$') { continue }
      $d = docker image inspect --format '{{index .RepoDigests 0}}' $refImg 2>$null
      if ($LASTEXITCODE -eq 0 -and "$d" -match '@(sha256:[0-9a-f]+)') { $digests[$refImg] = $Matches[1] }
    }
  }
  WriteUtf8 "$work\digests.json" ($digests | ConvertTo-Json -Compress)
  $dfArgs = @(); foreach ($df in $dfs) { $dfArgs += @('--dockerfile', "$($df[0])=$($df[1])") }
  $img = Text (python -X utf8 -I $helper images @dfArgs --local-digests "$work\digests.json")
  try { $data.images = @(($img | ConvertFrom-Json) | ForEach-Object { $_ }); Say "images de base : $(@($data.images).Count) examinées" } catch { $data.images = @(); Say "images de base : $img" }
}

$data.journal = $journal -join "`n"
WriteUtf8 "$work\collecte.json" ($data | ConvertTo-Json -Depth 8)
$res = Text (python -X utf8 -I $helper rapport --collecte "$work\collecte.json" --kanban $kanban --fiches $fiches --rapports $rapports)
if ($LASTEXITCODE -ne 0) { Write-Host "ÉCHEC du rapport : $res" -ForegroundColor Red; exit 1 }
$sum = $res | ConvertFrom-Json
Write-Host "Rapport : $($sum.rapport)" -ForegroundColor Green
Write-Host "Carte : $($sum.carte)$(if ($sum.fiche) { " ; fiche : $($sum.fiche)" })" -ForegroundColor Green

if ($mode -eq 'failles' -and $sum.failles -gt 0 -and (-not $Essai -or $Notifier)) {
  try {
    [Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
    $xml = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02)
    $t = $xml.GetElementsByTagName('text')
    $t.Item(0).AppendChild($xml.CreateTextNode('SARCADE : faille dans un composant')) | Out-Null
    $t.Item(1).AppendChild($xml.CreateTextNode("$($sum.failles) faille(s) non écartée(s). Voir la carte « Veille des dépendances » du Kanban.")) | Out-Null
    $toast = New-Object Windows.UI.Notifications.ToastNotification $xml
    [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe').Show($toast)
    Write-Host 'Notification Windows envoyée.'
  } catch { Write-Host "Notification impossible : $($_.Exception.Message)" -ForegroundColor Yellow }
}
# Copies de travail gardées 14 jours (rapports OSV bruts).
Get-ChildItem (Join-Path $out 'travail') -Directory -ErrorAction SilentlyContinue | Where-Object { $_.LastWriteTime -lt (Get-Date).AddDays(-14) } | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
exit 0
