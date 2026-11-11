# Mise à jour du serveur d'exploitation du NAS depuis l'i9, sans GitHub.
# À lancer par le porteur dans PowerShell (normal) : il faut le mot de passe
# de steph sur le NAS (trois fois : copie, connexion, sudo).
#
#   powershell -NoProfile -ExecutionPolicy Bypass -File C:\dev\sarcade\sarcade-server\tool\nas\publish-nas.ps1
#
# 1. client web de gitlab/main de l'application : archive prise dans
#    C:\dev\sarcade\publications\web, construite avant si besoin
#    (sarcade-app tool\build-web.ps1) ;
# 2. copie de l'archive dans /tmp du NAS (scp) ;
# 3. tool/nas/sarcade-update-gitlab.sh lancé en root sur le NAS (passé en
#    base64 : PowerShell 5.1 abîme les guillemets) : sauvegarde de la base,
#    code depuis la copie GitLab du NAS, image reconstruite, contrôle.
# Relançable sans risque.
$ErrorActionPreference = 'Continue'
$nas = 'steph@192.168.0.110'
$app = 'C:\dev\sarcade\sarcade-app'

git -C $app fetch -q gitlab
$sha = (git -C $app rev-parse --short gitlab/main).Trim()
$tar = "C:\dev\sarcade\publications\web\sarcade-web-$sha.tar.gz"
if (-not (Test-Path $tar)) {
  Write-Host "=== client web $sha absent : construction (5 minutes environ)"
  powershell -NoProfile -ExecutionPolicy Bypass -File "$app\tool\build-web.ps1"
  if (-not (Test-Path $tar)) { Write-Host "ÉCHEC : client web non construit" -ForegroundColor Red; exit 1 }
}
Write-Host "=== client web : $tar"

Write-Host "=== copie vers le NAS (mot de passe de steph)"
# -O : ancien protocole scp. En SFTP (défaut d'OpenSSH 9), le NAS ne montre
# que ses dossiers partagés : « /tmp » y est le partage /volume1/tmp.
scp -O -q $tar "${nas}:/tmp/sarcade-web-$sha.tar.gz"
if ($LASTEXITCODE -ne 0) { Write-Host 'ÉCHEC : copie vers le NAS' -ForegroundColor Red; exit 1 }

Write-Host "=== mise à jour sur le NAS (mot de passe de steph, puis celui de sudo)"
$script = [IO.File]::ReadAllText((Join-Path $PSScriptRoot 'sarcade-update-gitlab.sh')) -replace "`r`n", "`n"
$b64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($script))
ssh -t $nas "echo $b64 | base64 -d > /tmp/sarcade-update-gitlab.sh && sudo bash /tmp/sarcade-update-gitlab.sh /tmp/sarcade-web-$sha.tar.gz; r=`$?; sudo rm -f /volume1/tmp/sarcade-web-*.tar.gz; exit `$r"
if ($LASTEXITCODE -ne 0) { Write-Host 'ÉCHEC : voir les messages ci-dessus' -ForegroundColor Red; exit 1 }
Write-Host 'Serveur du NAS à jour.' -ForegroundColor Green
