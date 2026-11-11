# Clé Météo-France (API « Données radar », radar de pluie du lot 29) installée
# sur le NAS, sans jamais l'afficher (10 novembre 2026). À lancer par le porteur
# dans PowerShell (normal) sur l'i9 : mot de passe de steph deux fois, puis
# celui de sudo. Relançable sans risque.
#
#   powershell -NoProfile -ExecutionPolicy Bypass -File C:\dev\sarcade\sarcade-server\tool\nas\meteofrance-cle-installer.ps1
#
# 0. Clé prise dans C:\dev\secrets\API Meteo-France.txt (une seule ligne), ou
#    collée à la demande si ce fichier est vide ; forme contrôlée ; rangée dans
#    C:\dev\secrets\meteofrance\api-key.txt.
# 1. Clé envoyée au NAS dans un fichier à part (/tmp, droits 600), jamais
#    écrite dans le script : une clé est longue et peut casser une commande.
# 2. NAS (en root, meteofrance-nas.sh) : SARCADE_METEOFRANCE_API_KEY placé dans
#    /volume1/docker/sarcade/.env (copie .env.avant-meteofrance), transmis au
#    serveur si le compose ne le fait pas (docker-compose.override.yml),
#    serveur redémarré.
# 3. Contrôle depuis l'i9 : /api/v0.1/weather/radar doit passer à « ok ».
# Retour arrière : retirer la ligne SARCADE_METEOFRANCE_API_KEY du .env, puis
# « docker compose up -d --no-build server » dans /volume1/docker/sarcade.
$ErrorActionPreference = 'Continue'
$nas = 'steph@192.168.0.110'
$keyFile = 'C:\dev\secrets\meteofrance\api-key.txt'
$sh = Join-Path $PSScriptRoot 'meteofrance-nas.sh'
Write-Host '== 0. Clé Météo-France' -ForegroundColor Cyan
$source = 'C:\dev\secrets\API Meteo-France.txt'
$cle = if (Test-Path $source) { [IO.File]::ReadAllText($source) } else { '' }
if ($cle) { $cle = $cle.Trim() }
if (-not $cle) {
  Write-Host "Le fichier $source est vide : colle la clé ici (clic droit), puis Entrée. Rien ne s'affiche, c'est normal."
  $sec = Read-Host -AsSecureString 'Clé'
  $cle = [Runtime.InteropServices.Marshal]::PtrToStringBSTR([Runtime.InteropServices.Marshal]::SecureStringToBSTR($sec))
  $sec = $null
  if ($cle) { $cle = $cle.Trim() }
  if (-not $cle) { Write-Host 'Aucune clé collée : copie-la depuis Météo-France et relance.' -ForegroundColor Red; exit 1 }
}
if ($cle.Length -lt 20 -or $cle -notmatch '^[A-Za-z0-9_\-\.=]+$') { Write-Host "Le contenu de $source n'a pas la forme d'une clé (une seule ligne de lettres, chiffres, - _ . =) : vérifie qu'il ne contient que la clé, puis relance." -ForegroundColor Red; exit 1 }
New-Item -ItemType Directory -Force (Split-Path $keyFile) | Out-Null
[IO.File]::WriteAllText($keyFile, $cle, (New-Object Text.UTF8Encoding($false)))
Write-Host "clé de $($cle.Length) caractères rangée dans $keyFile (non affichée)"
$cle = $null; $sec = $null

Write-Host '== 1. Envoi de la clé au NAS (mot de passe de steph)' -ForegroundColor Cyan
Get-Content $keyFile -Raw | ssh $nas "umask 077; cat > /tmp/sarcade-cle"
if ($LASTEXITCODE -ne 0) { Write-Host 'ÉCHEC de l''envoi de la clé : colle ce résultat à Claude.' -ForegroundColor Red; exit 1 }

Write-Host '== 2. Installation sur le NAS (mot de passe de steph, puis sudo)' -ForegroundColor Cyan
$bash = [IO.File]::ReadAllText($sh) -replace "`r`n", "`n"
$b64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($bash))
ssh -t $nas "echo $b64 | base64 -d > /tmp/sarcade-meteo.sh && sudo bash /tmp/sarcade-meteo.sh; r=`$?; rm -f /tmp/sarcade-meteo.sh /tmp/sarcade-cle; exit `$r"
if ($LASTEXITCODE -ne 0) { Write-Host 'ÉCHEC sur le NAS (messages ci-dessus) : colle-les à Claude.' -ForegroundColor Red; exit 1 }

Write-Host '== 3. Contrôle du radar (jusqu''à 6 minutes)' -ForegroundColor Cyan
$etat = ''; $r = $null
for ($i = 0; $i -lt 36; $i++) {
  try { $r = Invoke-RestMethod -TimeoutSec 15 http://192.168.0.110:8000/api/v0.1/weather/radar; $etat = $r.state } catch { $etat = 'injoignable' }
  if ($etat -eq 'ok' -or $etat -eq 'error') { break }
  Start-Sleep 10
}
Write-Host "état du radar : $etat ; images : $($r.frames.Count) ; erreur : $($r.error)"
if ($etat -eq 'ok') { Write-Host 'Radar de pluie actif.' -ForegroundColor Green } else { Write-Host 'Radar pas encore actif : colle ce résultat à Claude.' -ForegroundColor Red; exit 1 }
