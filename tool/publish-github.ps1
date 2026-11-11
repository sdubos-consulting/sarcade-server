# Publication d'une GRANDE VERSION validée sur GitHub, vitrine SANS HISTORIQUE
# (cartes « GitHub de nouveau actif (10 novembre 2026) », choix 5, et « GitHub,
# vitrine des grandes versions », décision B du 11 novembre 2026). GitHub ne
# sert plus qu'à mettre ces versions à la disposition de tous ; le travail et
# l'historique complet restent sur l'i9, GitLab et Forgejo (NAS). À lancer
# SEULEMENT à la demande du porteur.
#
#   powershell -NoProfile -ExecutionPolicy Bypass -File tool\publish-github.ps1 -Version 0.9.0 -Essai
#   powershell -NoProfile -ExecutionPolicy Bypass -File tool\publish-github.ps1 -Version 0.9.0
#
# 1. GitLab est la référence (gitlab/main de l'application et du serveur).
#    Sur GitHub, chaque grande version est UN commit « SARCADE vX.Y.Z » dont le
#    contenu est celui de gitlab/main, sans .github/workflows ni
#    .github/dependabot.yml (rien ne doit tourner sur GitHub) ; son parent est
#    la version précédente de GitHub : poussée simple, jamais forcée. Refus si
#    le main de GitHub n'est pas un tel instantané (ancien dépôt avec tout
#    l'historique : le porteur le supprime d'abord, l'outil le recrée).
#    Le coffre (sarcade-vault) n'est JAMAIS poussé : il est seulement lu, sur
#    l'i9, pour les notes de version (décision du 11 novembre 2026).
# 2. Dépôts absents de GitHub : créés (publics) ; Actions coupées avant toute
#    poussée (serveur), ou limitées aux actions de GitHub (application : seule
#    la publication du site tourne, aucun fichier d'automatisation n'étant
#    poussé). Site de l'application (politique de confidentialité, adresse
#    déclarée dans la Play Console) : branche gh-pages = dossier site/ de
#    l'instantané, publiée par GitHub Pages (mode branche), puis contrôlée.
#    Le site et main sont poussés AVANT la construction des fichiers, pour que
#    le site soit coupé le moins longtemps possible après la recréation.
# 3. fichiers de la version, ceux du commit de gitlab/main de l'application
#    (construits par tool\build-downloads.ps1 et tool\build-web.ps1 s'ils
#    manquent) : APK Android, installeur Windows et sarcade-windows-version.json
#    (lu par la mise à jour Windows et le serveur), client web, AppImage Linux
#    si présente, sources ; sources du serveur ;
# 4. notes de version : cartes validées (fiches du coffre « statut: Validé »)
#    depuis la grande version précédente (ou -Depuis AAAA-MM-JJ), sans les
#    cartes internes (poste, CI, NAS, coffre, GitHub) ni organisation citée
#    (texte public) ; proposées dans publications\github\notes-proposees-vX.Y.Z.md,
#    à relire, puis passées par -Notes <fichier> si elles ont été retouchées ;
# 5. étiquette vX.Y.Z : annotée sur gitlab/main (i9 et GitLab), simple sur
#    l'instantané (GitHub) ; version publique (gh release create) sur les deux
#    dépôts.
# Relançable : un instantané déjà poussé pour cette version, au même contenu,
# est repris tel quel (pas de second commit).
# -Essai : affiche tout ce qui serait fait, sans rien construire, pousser ni
# créer (seules lectures : gitlab et GitHub, récupérés à part dans
# refs/publish-github/*, retirés à la fin). Journal : C:\dev\sarcade\publications\github\.
param(
  [Parameter(Mandatory = $true)][string]$Version,
  [switch]$Essai,
  [string]$Depuis,
  [string]$Notes
)
$ErrorActionPreference = 'Continue'
[Console]::OutputEncoding = New-Object Text.UTF8Encoding($false)
# Références de lecture de GitHub retirées : rien ne reste de GitHub dans les dépôts.
function Cleanup {
  if (-not $repos) { return }
  foreach ($n in $repos.Keys) {
    foreach ($r in 'main', 'gh-pages') { git -C $repos[$n].Path update-ref -d "refs/publish-github/$r" 2>$null }
  }
}
function Fail([string]$m) { Write-Host "ÉCHEC : $m" -ForegroundColor Red; Cleanup; try { Stop-Transcript | Out-Null } catch {}; exit 1 }
function Step([string]$m) { Write-Host "== $m" -ForegroundColor Cyan }
function Would([string]$m) { Write-Host "  [essai] ferait : $m" -ForegroundColor Yellow }
# Un refus arrête la publication ; à l'essai, il est noté et l'essai continue.
$script:blocked = 0
function Block([string]$m) { if ($Essai) { Write-Host "  [essai] BLOQUERAIT : $m" -ForegroundColor Red; $script:blocked++ } else { Fail $m } }
function WriteUtf8([string]$path, [string]$text) { [IO.File]::WriteAllText($path, $text, (New-Object Text.UTF8Encoding($false))) }

if ($Version -notmatch '^\d+\.\d+\.\d+$') { Fail "-Version attend X.Y.Z (par exemple 1.0.0), reçu « $Version »" }
$tag = "v$Version"
$org = 'sdubos-consulting'
# Dépôts publiés sur GitHub. Le coffre n'y figure jamais (garde-fou plus bas).
$repos = [ordered]@{
  'sarcade-app'    = @{ Path = 'C:\dev\sarcade\sarcade-app'; Pages = $true }
  'sarcade-server' = @{ Path = 'C:\dev\sarcade\sarcade-server'; Pages = $false }
}
# Coffre : lu sur l'i9 pour les notes de version, jamais poussé.
$vault = 'C:\dev\sarcade\sarcade-vault'
# Jamais sur GitHub : automatisations (Actions, Dependabot).
$exclus = @('.github/workflows', '.github/dependabot.yml')
$subjectPattern = '^SARCADE v\d+\.\d+\.\d+$'
$siteCheck = 'confidentialite/'
$pub = 'C:\dev\sarcade\publications'
$stage = "$pub\github\$tag"
New-Item -ItemType Directory -Force "$pub\github" | Out-Null
Start-Transcript -Path ("$pub\github\publication-$tag-{0}{1}.log" -f (Get-Date -Format 'yyyyMMdd-HHmmss'), $(if ($Essai) { '-essai' } else { '' })) | Out-Null
if ($Essai) { Write-Host "ESSAI : rien ne sera construit, poussé ni créé." -ForegroundColor Yellow }
# Identifiants GitHub de gh (connexion faite par le porteur), jamais écrits.
$cred = @('-c', 'credential.helper=', '-c', 'credential.helper=!gh auth git-credential')
foreach ($n in $repos.Keys) {
  if ($n -match 'vault' -or $repos[$n].Path -match 'vault') { Fail "le coffre ne doit jamais être publié sur GitHub ($n)" }
}

# Instantané : arbre de gitlab/main sans les automatisations (index temporaire,
# la copie de travail n'est pas touchée).
function SnapshotTree([string]$path) {
  $idx = Join-Path $env:TEMP ('publish-github-{0}.index' -f [guid]::NewGuid())
  $env:GIT_INDEX_FILE = $idx
  try {
    git -C $path read-tree gitlab/main
    if ($LASTEXITCODE -ne 0) { return $null }
    git -C $path rm --cached -r -q --ignore-unmatch -- @exclus | Out-Null
    $t = (git -C $path write-tree).Trim()
    if ($LASTEXITCODE -ne 0) { return $null }
    return $t
  } finally {
    Remove-Item Env:GIT_INDEX_FILE -ErrorAction SilentlyContinue
    Remove-Item -Force $idx -ErrorAction SilentlyContinue
  }
}
# Branche distante lue à part ; renvoie son commit, ou $null si elle n'existe pas.
function RemoteHead([string]$path, [string]$url, [string]$branch) {
  $line = git -C $path @cred ls-remote $url "refs/heads/$branch" 2>$null
  if (-not $line) { return $null }
  git -C $path @cred fetch -q $url "+refs/heads/${branch}:refs/publish-github/$branch" 2>&1 | Out-Null
  if ($LASTEXITCODE -ne 0) { Fail "lecture de $branch sur GitHub impossible ($url)" }
  return (git -C $path rev-parse "refs/publish-github/$branch").Trim()
}

Step "1. GitLab, référence, et GitHub ($tag)"
if (-not (Get-Command gh -ErrorAction SilentlyContinue)) { Fail 'gh absent (winget install --id GitHub.cli)' }
gh auth status 2>&1 | Out-Null
if ($LASTEXITCODE -ne 0) { Fail 'gh non connecté (gh auth login, par le porteur)' }
$sha = @{}; $tree = @{}; $exists = @{}; $parent = @{}; $reuse = @{}; $pagesParent = @{}
foreach ($name in $repos.Keys) {
  $p = $repos[$name].Path
  git -C $p fetch -q gitlab
  if ($LASTEXITCODE -ne 0) { Fail "$name : git fetch gitlab" }
  $sha[$name] = (git -C $p rev-parse gitlab/main).Trim()
  $tree[$name] = SnapshotTree $p
  if (-not $tree[$name]) { Fail "$name : instantané de gitlab/main impossible" }
  $files = @(git -C $p ls-tree -r --name-only $tree[$name])
  $auto = @($files | Where-Object { $_ -like '.github/workflows/*' -or $_ -eq '.github/dependabot.yml' })
  if ($auto.Count) { Fail "$name : l'instantané contient encore des automatisations ($($auto -join ', '))" }
  if ($repos[$name].Pages -and -not ($files -contains "site/$($siteCheck)index.html")) { Block "$name : site/$($siteCheck)index.html absent de gitlab/main (site de la politique de confidentialité)" }
  gh repo view "$org/$name" --json name 2>&1 | Out-Null
  $exists[$name] = ($LASTEXITCODE -eq 0)
  $url = "https://github.com/$org/$name.git"
  $parent[$name] = $null; $reuse[$name] = $null; $pagesParent[$name] = $null; $legacy = $null
  if ($exists[$name]) {
    $gh = RemoteHead $p $url 'main'
    if ($gh) {
      $subject = (git -C $p log -1 --format=%s $gh).Trim()
      if ($subject -notmatch $subjectPattern) {
        $legacy = $gh
        Block "$name : le main de GitHub ($($gh.Substring(0,7)), « $subject ») n'est pas un instantané de version : ancien dépôt avec tout l'historique, à supprimer d'abord par le porteur (l'outil le recrée) ; jamais de poussée forcée"
      } elseif ($subject -eq "SARCADE $tag" -and (git -C $p rev-parse "$gh^{tree}").Trim() -eq $tree[$name]) {
        $reuse[$name] = $gh
      } else { $parent[$name] = $gh }
    }
    if ($repos[$name].Pages) { $pagesParent[$name] = RemoteHead $p $url 'gh-pages' }
  }
  $state = if (-not $exists[$name]) { 'absent de GitHub : sera créé (public)' }
           elseif ($legacy) { "ancien dépôt avec tout l'historique ($($legacy.Substring(0,7))) : à supprimer d'abord" }
           elseif ($reuse[$name]) { "instantané $tag déjà poussé ($($reuse[$name].Substring(0,7))), repris" }
           elseif ($parent[$name]) { "version précédente $($parent[$name].Substring(0,7))" }
           else { 'dépôt vide' }
  Write-Host ("{0,-15} gitlab/main {1}  instantané : {2} fichiers  GitHub : {3}" -f $name, $sha[$name].Substring(0, 7), $files.Count, $state)
}
foreach ($name in $repos.Keys) {
  $p = $repos[$name].Path
  if (git -C $p tag -l $tag) { Block "$name : l'étiquette $tag existe déjà sur l'i9" }
  if ($exists[$name]) {
    gh release view $tag --repo "$org/$name" 2>&1 | Out-Null
    if ($LASTEXITCODE -eq 0) { Block "$name : la version $tag existe déjà sur GitHub" }
    $remoteTag = git -C $p @cred ls-remote --tags "https://github.com/$org/$name.git" "refs/tags/$tag" 2>$null
    if ($remoteTag) { Block "$name : l'étiquette $tag existe déjà sur GitHub" }
  }
}

Step '2. Dépôts, instantanés et site sur GitHub'
$snap = @{}
foreach ($name in $repos.Keys) {
  $p = $repos[$name].Path
  $url = "https://github.com/$org/$name.git"
  $actions = if ($repos[$name].Pages) { 'limitées aux actions de GitHub (publication du site seulement)' } else { 'coupées' }
  if ($Essai) {
    if (-not $exists[$name]) { Would "gh repo create $org/$name --public" }
    Would "Actions de $name : $actions, avant toute poussée"
    if ($reuse[$name]) { Write-Host "  $name : instantané $tag déjà sur GitHub, rien à pousser" }
    else { Would ("commit « SARCADE $tag » (contenu de gitlab/main {0}{1}) poussé sur main de $url" -f $sha[$name].Substring(0, 7), $(if ($parent[$name]) { ", parent $($parent[$name].Substring(0,7))" } else { ', premier commit' })) }
    if ($repos[$name].Pages) { Would "branche gh-pages = site/ de l'instantané ; GitHub Pages en mode branche (gh-pages, /) ; contrôle de https://$org.github.io/$name/$siteCheck" }
    continue
  }
  if (-not $exists[$name]) {
    gh repo create "$org/$name" --public --description "SARCADE : grandes versions publiées (historique de développement tenu ailleurs)" --disable-wiki
    if ($LASTEXITCODE -ne 0) { Fail "$name : création du dépôt sur GitHub" }
  }
  if ($repos[$name].Pages) {
    gh api -X PUT "repos/$org/$name/actions/permissions" -F enabled=true -f allowed_actions=selected | Out-Null
    gh api -X PUT "repos/$org/$name/actions/permissions/selected-actions" -F github_owned_allowed=true -F verified_allowed=false | Out-Null
  } else {
    gh api -X PUT "repos/$org/$name/actions/permissions" -F enabled=false | Out-Null
  }
  $perm = gh api "repos/$org/$name/actions/permissions" --jq '"\(.enabled) \(.allowed_actions // "")"'
  if ($LASTEXITCODE -ne 0 -or ($repos[$name].Pages -and $perm -ne 'true selected') -or (-not $repos[$name].Pages -and $perm -notmatch '^false')) { Fail "$name : réglage des Actions non appliqué ($perm)" }
  if ($reuse[$name]) { $snap[$name] = $reuse[$name] }
  else {
    $msgFile = Join-Path $env:TEMP "publish-github-$name.txt"
    WriteUtf8 $msgFile "SARCADE $tag`n`nInstantané de main@$($sha[$name].Substring(0,12)). L'historique de développement n'est pas publié ici.`n"
    $ct = @('commit-tree', $tree[$name], '-F', $msgFile); if ($parent[$name]) { $ct += @('-p', $parent[$name]) }
    $snap[$name] = (git -C $p -c user.name="SDUBOS Consulting" -c user.email="github@sdubos.com" @ct).Trim()
    Remove-Item -Force $msgFile -ErrorAction SilentlyContinue
    if ($LASTEXITCODE -ne 0 -or -not $snap[$name]) { Fail "$name : commit de l'instantané" }
    git -C $p @cred push $url "$($snap[$name]):refs/heads/main"
    if ($LASTEXITCODE -ne 0) { Fail "$name : poussée de l'instantané refusée (jamais de poussée forcée)" }
  }
  Write-Host "  $name : main de GitHub = $($snap[$name].Substring(0,7)) (SARCADE $tag)"
  if ($repos[$name].Pages) {
    $siteTree = (git -C $p rev-parse "$($tree[$name]):site").Trim()
    $old = $pagesParent[$name]
    if ($old -and (git -C $p rev-parse "$old^{tree}").Trim() -eq $siteTree) { $site = $old }
    else {
      $ct = @('commit-tree', $siteTree, '-m', "Site SARCADE $tag"); if ($old) { $ct += @('-p', $old) }
      $site = (git -C $p -c user.name="SDUBOS Consulting" -c user.email="github@sdubos.com" @ct).Trim()
      git -C $p @cred push $url "${site}:refs/heads/gh-pages"
      if ($LASTEXITCODE -ne 0) { Fail "$name : poussée de gh-pages refusée" }
    }
    $cfg = gh api "repos/$org/$name/pages" --jq '"\(.build_type) \(.source.branch) \(.source.path)"' 2>$null
    if ($LASTEXITCODE -ne 0) {
      gh api -X POST "repos/$org/$name/pages" -f build_type=legacy -f 'source[branch]=gh-pages' -f 'source[path]=/' | Out-Null
    } elseif ($cfg -ne 'legacy gh-pages /') {
      gh api -X PUT "repos/$org/$name/pages" -f build_type=legacy -f 'source[branch]=gh-pages' -f 'source[path]=/' | Out-Null
    }
    gh api -X POST "repos/$org/$name/pages/builds" 2>&1 | Out-Null
    $siteUrl = "https://$org.github.io/$name/$siteCheck"
    $ok = $false
    for ($i = 0; $i -lt 30 -and -not $ok; $i++) {
      try { $ok = ((Invoke-WebRequest -UseBasicParsing -TimeoutSec 10 $siteUrl).StatusCode -eq 200) } catch { Start-Sleep -Seconds 10 }
    }
    if ($ok) { Write-Host "  site en ligne : $siteUrl" -ForegroundColor Green }
    else { Write-Host "  ATTENTION : $siteUrl ne répond pas après 5 minutes (GitHub › $name › Settings › Pages ; la publication continue)" -ForegroundColor Yellow }
  }
}

Step '3. Fichiers de la version'
$appShort = $sha['sarcade-app'].Substring(0, 7)
$srvShort = $sha['sarcade-server'].Substring(0, 7)
$dl = "$pub\downloads\$appShort"
$web = "$pub\web\sarcade-web-$appShort.tar.gz"
if (-not (Test-Path "$dl\app-files.json")) {
  if ($Essai) { Would "construire installeur Windows, APK et sources de l'application main@$appShort (sarcade-app\tool\build-downloads.ps1)" }
  else { powershell -NoProfile -ExecutionPolicy Bypass -File 'C:\dev\sarcade\sarcade-app\tool\build-downloads.ps1'; if (-not (Test-Path "$dl\app-files.json")) { Fail "fichiers de main@$appShort non construits" } }
}
if (-not (Test-Path $web)) {
  if ($Essai) { Would "construire le client web main@$appShort (sarcade-app\tool\build-web.ps1)" }
  else { powershell -NoProfile -ExecutionPolicy Bypass -File 'C:\dev\sarcade\sarcade-app\tool\build-web.ps1'; if (-not (Test-Path $web)) { Fail "client web main@$appShort non construit" } }
}
$appimage = @(Get-ChildItem "$pub\linux\$appShort", $dl -Filter '*.AppImage' -ErrorAction SilentlyContinue) | Select-Object -First 1
$appAssets = New-Object System.Collections.Generic.List[string]
$srvAssets = New-Object System.Collections.Generic.List[string]
$appDir = "$stage\sarcade-app"; $srvDir = "$stage\sarcade-server"
if (-not $Essai) { foreach ($d in $appDir, $srvDir) { if (Test-Path $d) { Remove-Item -Recurse -Force $d }; New-Item -ItemType Directory -Force $d | Out-Null } }
$entries = @()
if (Test-Path "$dl\app-files.json") { $entries = @(([IO.File]::ReadAllText("$dl\app-files.json") | ConvertFrom-Json) | ForEach-Object { $_ }) }
$win = $entries | Where-Object { $_.kind -eq 'windows' } | Select-Object -First 1
$plan = @(
  @('sarcade-android.apk', "$dl\sarcade-android.apk"),
  @('sarcade-windows-setup.exe', "$dl\sarcade-windows-setup.exe"),
  @('sarcade-web.tar.gz', $web),
  @("sarcade-app-sources-$tag.tar.gz", "$dl\sarcade-app-sources-$appShort.tar.gz")
)
if ($appimage) { $plan += , @("sarcade-linux-$tag.AppImage", $appimage.FullName) } else { Write-Host "  AppImage Linux : absente ($pub\linux\$appShort\), version publiée sans elle" }
foreach ($a in $plan) {
  $dest = Join-Path $appDir $a[0]
  if ($Essai) { Write-Host ("  application : {0,-34} <- {1}{2}" -f $a[0], $a[1], $(if (Test-Path $a[1]) { '' } else { ' (à construire)' })) }
  else { Copy-Item -Force $a[1] $dest; $appAssets.Add($dest) }
}
# Manifeste lu par la mise à jour Windows (application) et par
# POST /admin/clients/windows/fetch (serveur) : releases/latest/download/.
$manifest = $null
if ($win) { $manifest = [ordered]@{ version = $win.version; build = $win.build; sha256 = $win.sha256; size = $win.size; installer = 'sarcade-windows-setup.exe' } }
if ($Essai) { Write-Host ("  application : sarcade-windows-version.json        <- {0}" -f $(if ($manifest) { ($manifest | ConvertTo-Json -Compress) } else { "app-files.json de main@$appShort (à construire)" })) }
else {
  if (-not $manifest) { Fail 'installeur Windows absent de app-files.json' }
  WriteUtf8 "$appDir\sarcade-windows-version.json" ($manifest | ConvertTo-Json)
  $appAssets.Add("$appDir\sarcade-windows-version.json")
}
$srcSrv = "$srvDir\sarcade-server-sources-$tag.tar.gz"
if ($Essai) { Write-Host "  serveur     : sarcade-server-sources-$tag.tar.gz  <- git archive gitlab/main ($srvShort)" }
else {
  git -C $repos['sarcade-server'].Path archive --format=tar.gz --prefix=sarcade-server/ -o $srcSrv gitlab/main
  if ($LASTEXITCODE -ne 0) { Fail 'sources du serveur' }
  $srvAssets.Add($srcSrv)
}

Step '4. Notes de version'
$prev = (git -C $repos['sarcade-app'].Path tag -l 'v[0-9]*' --sort=-creatordate --merged gitlab/main | Select-Object -First 1)
$since = $null
if ($Depuis) { $since = $Depuis } elseif ($prev) { $since = (git -C $repos['sarcade-app'].Path log -1 --format=%cs $prev).Trim() }
Write-Host ("  grande version précédente : {0} ; cartes validées {1}" -f $(if ($prev) { $prev } else { 'aucune' }), $(if ($since) { "depuis le $since" } else { 'toutes (première grande version)' }))
$fichesDir = 'SARCADE/08 - Backlog/fiches'
if ($since) { $paths = @(git -C $vault log --since="$since" --name-only --format= gitlab/main -- $fichesDir | Sort-Object -Unique) }
else { $paths = @(git -C $vault ls-tree -r --name-only gitlab/main -- $fichesDir) }
$cards = New-Object System.Collections.Generic.List[string]
$hidden = 0
foreach ($p in $paths | Where-Object { $_ -like '*.md' }) {
  $text = (git -C $vault show "gitlab/main:$p" 2>$null) -join "`n"
  if ($text -notmatch '(?m)^statut:\s*Validé') { continue }
  $title = if ($text -match '(?m)^# (.+)$') { $Matches[1].Trim() } else { [IO.Path]::GetFileNameWithoutExtension($p) }
  $tags = if ($text -match '(?m)^tags:\s*\[([^\]]*)\]') { @($Matches[1] -split ',' | ForEach-Object { $_.Trim() }) } else { @() }
  # Texte public : SARCADE présenté comme un outil général, sans organisation,
  # sans les cartes du poste de développement ni les actions du porteur.
  $internal = @($tags | Where-Object { @('infra', 'ci', 'i9', 'coffre', 'github', 'nas', 'hebergement', 'recette', 'vikunja', 'forgejo', 'sauvegarde') -contains $_ }).Count -gt 0
  if ($internal -or $title -match '(?i)ADRASEC|FNRASEC|\bi9\b|\bNAS\b|GitHub|Forgejo|Vikunja|Codemagic|Xcode Cloud|\bCI\b|^Action du porteur|^fait le |^Diagnostic \(|^Consolidation') { $hidden++; continue }
  $title = ($title -replace '\s*\((décision|build|fait)[^)]*\)?\s*$', '').Trim()
  $cards.Add("- $title")
}
$assetLines = @('- `sarcade-android.apk` : Android, pour les appareils sans Google Play', '- `sarcade-windows-setup.exe` : installeur Windows',
  '- `sarcade-web.tar.gz` : client web, servi par le serveur SARCADE')
if ($appimage) { $assetLines += "- ``sarcade-linux-$tag.AppImage`` : Linux" }
$assetLines += "- sources de l'application et du serveur"
$noteLines = @("SARCADE $Version : outil de coordination d'équipes sur le terrain.", '', '## Fichiers', '') + $assetLines + @('', '## Nouveautés validées', '')
if ($cards.Count) { $noteLines += ($cards | Sort-Object) } else { $noteLines += '- Corrections et améliorations.' }
$noteLines += @('', "Application main@$appShort, serveur main@$srvShort.")
$notesText = ($noteLines -join "`n") + "`n"
$notesSrv = "Serveur SARCADE $Version (sources ; l'image est construite par chaque exploitant). Notes complètes : https://github.com/$org/sarcade-app/releases/tag/$tag`n`n" + (($cards | Sort-Object) -join "`n") + "`n"
Write-Host "  $($cards.Count) cartes validées retenues$(if ($hidden) { ", $hidden écartées (internes ou organisation citée)" })"
if ($Notes) {
  if (-not (Test-Path $Notes)) { Fail "notes $Notes absentes" }
  $notesText = [IO.File]::ReadAllText((Resolve-Path $Notes).Path)
  Write-Host "  notes reprises de $Notes"
}
WriteUtf8 "$pub\github\notes-proposees-$tag.md" $notesText
Write-Host "  notes proposées : $pub\github\notes-proposees-$tag.md (à relire ; retouchées, les passer par -Notes)"
if ($Essai) {
  Write-Host '  --- notes (application) ---'; Write-Host $notesText; Write-Host '  ---'
} else {
  WriteUtf8 "$stage\notes-sarcade-app.md" $notesText
  WriteUtf8 "$stage\notes-sarcade-server.md" $notesSrv
}

Step "5. Étiquettes et versions publiques $tag"
foreach ($name in $repos.Keys) {
  $p = $repos[$name].Path
  if ($Essai) {
    Would "git -C $p tag -a $tag -m `"SARCADE $Version`" gitlab/main, poussée vers gitlab ; étiquette $tag sur l'instantané, poussée vers GitHub"
    continue
  }
  git -C $p -c user.name="SDUBOS Consulting" -c user.email="github@sdubos.com" tag -a $tag -m "SARCADE $Version" gitlab/main
  if ($LASTEXITCODE -ne 0) { Fail "$name : étiquette $tag" }
  git -C $p push gitlab "refs/tags/$tag"
  if ($LASTEXITCODE -ne 0) { Write-Host "  $name : $tag non poussée vers gitlab (à refaire : git -C $p push gitlab $tag)" -ForegroundColor Yellow }
  git -C $p @cred push "https://github.com/$org/$name.git" "$($snap[$name]):refs/tags/$tag"
  if ($LASTEXITCODE -ne 0) { Fail "$name : poussée de $tag vers GitHub" }
}
$appNames = @($plan | ForEach-Object { $_[0] }) + 'sarcade-windows-version.json'
if ($Essai) {
  Would "gh release create $tag --repo $org/sarcade-app --title `"SARCADE $Version`" --notes-file notes-sarcade-app.md --verify-tag --latest $($appNames -join ' ')"
  Would "gh release create $tag --repo $org/sarcade-server --title `"SARCADE $Version (serveur)`" --notes-file notes-sarcade-server.md --verify-tag --latest sarcade-server-sources-$tag.tar.gz"
  Write-Host "ESSAI terminé : rien n'a été poussé ni créé ; $($script:blocked) blocage(s) à lever avant la vraie publication." -ForegroundColor Green
} else {
  gh release create $tag --repo "$org/sarcade-app" --title "SARCADE $Version" --notes-file "$stage\notes-sarcade-app.md" --verify-tag --latest @appAssets
  if ($LASTEXITCODE -ne 0) { Fail 'version publique de sarcade-app' }
  gh release create $tag --repo "$org/sarcade-server" --title "SARCADE $Version (serveur)" --notes-file "$stage\notes-sarcade-server.md" --verify-tag --latest @srvAssets
  if ($LASTEXITCODE -ne 0) { Fail 'version publique de sarcade-server' }
  Write-Host "Publié : https://github.com/$org/sarcade-app/releases/tag/$tag et https://github.com/$org/sarcade-server/releases/tag/$tag" -ForegroundColor Green
}
Cleanup
try { Stop-Transcript | Out-Null } catch {}
