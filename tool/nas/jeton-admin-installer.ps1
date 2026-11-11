# Jeton d'administrateur du serveur du NAS (carte « Serveur du NAS :
# administration ouverte sans jeton sur le réseau local », feu vert du porteur
# le 8 novembre 2026). À lancer par le porteur dans PowerShell (normal) sur
# l'i9 : mot de passe de steph sur le NAS, deux fois. Relançable sans risque.
#
# 1. i9 : jeton créé une fois, rangé dans C:\dev\secrets\nas\admin-token.txt,
#    jamais affiché.
# 2. NAS (en root) : SARCADE_ADMIN_TOKEN placé dans /volume1/docker/sarcade/.env
#    (copie .env.avant-jeton), transmis au serveur si le compose ne le fait pas
#    (docker-compose.override.yml), serveur redémarré.
# 3. Contrôle depuis l'i9 : sans jeton 401, avec le jeton 200.
# Retour arrière : retirer la ligne SARCADE_ADMIN_TOKEN du .env, puis
# « docker compose up -d --no-build server » dans /volume1/docker/sarcade.
$ErrorActionPreference = 'Continue'
$nas = 'steph@192.168.0.110'
$url = 'http://192.168.0.110:8000/api/v0.1/admin/updates/plan'
$sec = 'C:\dev\secrets\nas'
$tokFile = Join-Path $sec 'admin-token.txt'
New-Item -ItemType Directory -Force $sec | Out-Null

Write-Host '== 1. Jeton (i9)' -ForegroundColor Cyan
if (-not (Test-Path $tokFile)) {
  $bytes = New-Object byte[] 32; [Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
  $tok = ([Convert]::ToBase64String($bytes) -replace '[+/=]', 'x')
  [IO.File]::WriteAllText($tokFile, $tok, (New-Object Text.UTF8Encoding($false)))
  Write-Host "créé : $tokFile (non affiché)"
} else { Write-Host "réutilisé : $tokFile (non affiché)" }
$tok = [IO.File]::ReadAllText($tokFile).Trim()

Write-Host '== 2. NAS (mot de passe de steph, puis sudo)' -ForegroundColor Cyan
$bash = @'
set -euo pipefail
export PATH=/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin
S=/volume1/docker/sarcade
cp -n "$S/.env" "$S/.env.avant-jeton" || true
if grep -q '^SARCADE_ADMIN_TOKEN=' "$S/.env"; then
  sed -i "s|^SARCADE_ADMIN_TOKEN=.*|SARCADE_ADMIN_TOKEN=__TOK__|" "$S/.env"
else
  printf '\nSARCADE_ADMIN_TOKEN=%s\n' '__TOK__' >> "$S/.env"
fi
echo "jeton placé dans $S/.env (non affiché)"
if ! grep -q 'SARCADE_ADMIN_TOKEN' "$S/compose.yaml" "$S/docker-compose.override.yml"; then
  cp -n "$S/docker-compose.override.yml" "$S/docker-compose.override.yml.avant-jeton" || true
  sed -i '0,/^      SARCADE_ADMIN_EMAILS:.*/s//&\n      SARCADE_ADMIN_TOKEN: ${SARCADE_ADMIN_TOKEN:-}/' "$S/docker-compose.override.yml"
  echo "SARCADE_ADMIN_TOKEN transmis au serveur (docker-compose.override.yml)"
fi
# Conteneur recréé par Watchtower : Compose ne le reconnaît plus comme le sien.
if [ -z "$(cd "$S" && docker compose ps -q server)" ] && docker inspect sarcade-server-1 >/dev/null 2>&1; then
  echo "conteneur recréé par Watchtower : retiré pour que Compose le recrée"; docker rm -f sarcade-server-1 >/dev/null
fi
(cd "$S" && docker compose up -d --no-build server)
for i in $(seq 1 60); do curl -fsS -o /dev/null http://localhost:8000/health && break; sleep 2; done
docker exec sarcade-server-1 sh -c 'test -n "$SARCADE_ADMIN_TOKEN" && echo "serveur : jeton présent"'
echo "== fin"
'@
$bash = $bash.Replace('__TOK__', $tok) -replace "`r`n", "`n"
$b64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($bash))
ssh -t $nas "echo $b64 | base64 -d > /tmp/sarcade-jeton.sh && sudo bash /tmp/sarcade-jeton.sh; r=`$?; rm -f /tmp/sarcade-jeton.sh; exit `$r"
if ($LASTEXITCODE -ne 0) { Write-Host 'ÉCHEC sur le NAS (messages ci-dessus)' -ForegroundColor Red; exit 1 }

Write-Host '== 3. Contrôle (i9)' -ForegroundColor Cyan
Start-Sleep 5
$sans = try { (Invoke-WebRequest -UseBasicParsing -TimeoutSec 10 $url).StatusCode } catch { [int]$_.Exception.Response.StatusCode }
$avec = try { (Invoke-WebRequest -UseBasicParsing -TimeoutSec 10 -Headers @{ Authorization = "Bearer $tok" } $url).StatusCode } catch { [int]$_.Exception.Response.StatusCode }
Write-Host "sans jeton : $sans (401 attendu) ; avec le jeton : $avec (200 attendu)"
if ($sans -eq 401 -and $avec -eq 200) { Write-Host 'Administration protégée.' -ForegroundColor Green } else { Write-Host 'Contrôle inattendu : colle ce résultat à Claude.' -ForegroundColor Red; exit 1 }
