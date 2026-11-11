# Enregistre la veille des dépendances dans le Planificateur de tâches de
# Windows (carte « GitHub de nouveau actif (10 novembre 2026) », choix 4f) :
#   « SARCADE veille des failles »   chaque jour à 6 h  (veille-dependances.ps1 -Failles)
#   « SARCADE veille des versions »  chaque lundi à 7 h (veille-dependances.ps1 -Versions)
# Sous le compte courant, sans droits d'administrateur (PowerShell normal) ;
# une veille manquée (i9 éteint ou session fermée) est faite dès que possible
# (StartWhenAvailable). Relançable : les tâches existantes sont remplacées.
#
#   powershell -NoProfile -ExecutionPolicy Bypass -File C:\dev\sarcade\sarcade-server\tool\veille-installer.ps1 [-Essai] [-Retirer]
#
# -Essai : vérifie les outils et affiche les deux tâches, sans rien enregistrer.
# -Retirer : supprime les deux tâches.
param([switch]$Essai, [switch]$Retirer)
$ErrorActionPreference = 'Stop'
$script = 'C:\dev\sarcade\sarcade-server\tool\veille-dependances.ps1'
$logs = 'C:\dev\sarcade\publications\veille'
$user = "$env:USERDOMAIN\$env:USERNAME"
$tasks = @(
  @{ Name = 'SARCADE veille des failles'; Mode = 'Failles'; Trigger = (New-ScheduledTaskTrigger -Daily -At '06:00') },
  @{ Name = 'SARCADE veille des versions'; Mode = 'Versions'; Trigger = (New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday -At '07:00') }
)

if ($Retirer) {
  foreach ($t in $tasks) {
    if (Get-ScheduledTask -TaskName $t.Name -ErrorAction SilentlyContinue) {
      if ($Essai) { Write-Host "ESSAI : supprimerait « $($t.Name) »" } else { Unregister-ScheduledTask -TaskName $t.Name -Confirm:$false; Write-Host "Supprimée : « $($t.Name) »" }
    } else { Write-Host "Absente : « $($t.Name) »" }
  }
  exit 0
}

# Outils nécessaires (le PATH de la tâche est celui de la session).
$wingetLinks = "$env:LOCALAPPDATA\Microsoft\WinGet\Links"
if (($env:Path -split ';') -notcontains $wingetLinks) { $env:Path += ";$wingetLinks" }
$missing = @()
foreach ($tool in 'git', 'tar', 'python', 'osv-scanner', 'uv', 'flutter', 'docker') {
  $c = Get-Command $tool -ErrorAction SilentlyContinue
  if ($c) { Write-Host ("outil {0,-12} {1}" -f $tool, $c.Source) } else { $missing += $tool; Write-Host ("outil {0,-12} ABSENT" -f $tool) -ForegroundColor Yellow }
}
if ($missing -contains 'osv-scanner') { Write-Host 'OSV-Scanner : winget install --id Google.OSVScanner -e --scope user' -ForegroundColor Yellow }
if (-not (Test-Path $script)) { Write-Host "ÉCHEC : $script absent (branche github-decouplage pas encore fusionnée ?)" -ForegroundColor Red; if (-not $Essai) { exit 1 } }
New-Item -ItemType Directory -Force $logs | Out-Null

$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
  -ExecutionTimeLimit (New-TimeSpan -Hours 1) -MultipleInstances IgnoreNew
$principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
foreach ($t in $tasks) {
  $log = Join-Path $logs ("derniere-veille-{0}.log" -f $t.Mode.ToLower())
  $arg = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -Command `"& '$script' -$($t.Mode) *> '$log'`""
  $action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument $arg -WorkingDirectory 'C:\dev\sarcade\sarcade-server'
  $task = New-ScheduledTask -Action $action -Trigger $t.Trigger -Settings $settings -Principal $principal `
    -Description "SARCADE : veille des dépendances ($($t.Mode)), carte « GitHub de nouveau actif (10 novembre 2026) »."
  if ($Essai) {
    Write-Host "ESSAI : enregistrerait « $($t.Name) »" -ForegroundColor Cyan
    Write-Host "  compte      : $($task.Principal.UserId) (niveau $($task.Principal.RunLevel), $($task.Principal.LogonType))"
    $at = ([DateTime]::Parse($t.Trigger.StartBoundary)).ToString('HH:mm')
    Write-Host "  déclencheur : $(if ($t.Mode -eq 'Versions') { 'chaque lundi' } else { 'chaque jour' }) à $at (heure locale)"
    Write-Host "  rattrapage  : StartWhenAvailable=$($task.Settings.StartWhenAvailable), durée max $($task.Settings.ExecutionTimeLimit)"
    Write-Host "  commande    : powershell.exe $arg"
  } else {
    Register-ScheduledTask -TaskName $t.Name -InputObject $task -Force | Out-Null
    $info = Get-ScheduledTaskInfo -TaskName $t.Name
    Write-Host "Enregistrée : « $($t.Name) », prochaine exécution $($info.NextRunTime)" -ForegroundColor Green
  }
}
if (-not $Essai) {
  Write-Host 'Vérification : Planificateur de tâches › Bibliothèque › les deux tâches « SARCADE veille … », état « Prêt ».'
}
