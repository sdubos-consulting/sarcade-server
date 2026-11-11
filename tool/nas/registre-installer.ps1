# Installation, une seule fois, de la mise à jour automatique du serveur du NAS
# par Watchtower (carte « NAS : mise à jour automatique après chaque fusion »,
# plan validé par le porteur le 8 novembre 2026, écriture autorisée par lui).
# À lancer par le porteur dans PowerShell (normal) sur l'i9 : mot de passe de
# steph sur le NAS, deux fois. Relançable sans risque (mot de passe du registre
# réutilisé, copies .avant-registre gardées).
#
# 1. i9 : mot de passe du registre créé une fois, rangé dans C:\dev\secrets\nas\,
#    jamais affiché ; empreinte bcrypt calculée par Docker (image httpd).
# 2. NAS (en root) : registre d'images « sarcade-registry » (registry:2, port 5000,
#    mot de passe obligatoire) ; Watchtower surveille aussi sarcade-server-1,
#    toutes les 5 minutes, avec l'accès au registre ; le serveur prend son image
#    dans le registre (localhost:5000/sarcade-server:main) ; sauvegarde de la
#    base chaque nuit à 3 h 30, 14 gardées.
# 3. i9 : Docker Desktop accepte le registre du NAS (insecure-registries,
#    adresse du NAS seulement), puis connexion au registre.
# Continue : PowerShell 5.1 prend la sortie d'erreur de docker et ssh pour une erreur ;
# chaque étape vérifie elle-même son résultat.
$ErrorActionPreference = 'Continue'
$nas = 'steph@192.168.0.110'
$registry = '192.168.0.110:5000'
$sec = 'C:\dev\secrets\nas'
$pwFile = Join-Path $sec 'registry-password.txt'
New-Item -ItemType Directory -Force $sec | Out-Null

Write-Host '== 1. Mot de passe du registre (i9)' -ForegroundColor Cyan
if (-not (Test-Path $pwFile)) {
  $bytes = New-Object byte[] 24; [Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
  $pw = ([Convert]::ToBase64String($bytes) -replace '[+/=]', 'x')
  [IO.File]::WriteAllText($pwFile, $pw, (New-Object Text.UTF8Encoding($false)))
  Write-Host "créé : $pwFile (non affiché)"
} else { Write-Host "réutilisé : $pwFile (non affiché)" }
$pw = [IO.File]::ReadAllText($pwFile).Trim()
$hash = (docker run --rm httpd:2.4-alpine htpasswd -nbB sarcade $pw 2>$null | Select-Object -First 1).Trim()
if ($hash -notmatch '^sarcade:\$2') { throw 'empreinte du mot de passe non calculée (Docker Desktop lancé ?)' }
$auth = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes("sarcade:$pw"))

Write-Host '== 2. NAS (mot de passe de steph, puis sudo)' -ForegroundColor Cyan
$bash = @'
set -euo pipefail
export PATH=/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin
say() { printf '\n== %s\n' "$*"; }
R=/volume1/docker/registry; S=/volume1/docker/sarcade; W=/volume1/docker/compose.yaml
say "Registre d'images (sarcade-registry, port 5000, mot de passe)"
mkdir -p "$R/data" "$R/auth"
printf '%s\n' '__HASH__' > "$R/auth/htpasswd"; chmod 600 "$R/auth/htpasswd"
cat > "$R/compose.yaml" <<'YML'
services:
  registry:
    image: registry:2
    container_name: sarcade-registry
    restart: unless-stopped
    ports:
      - "5000:5000"
    environment:
      REGISTRY_AUTH: htpasswd
      REGISTRY_AUTH_HTPASSWD_REALM: SARCADE
      REGISTRY_AUTH_HTPASSWD_PATH: /auth/htpasswd
      REGISTRY_STORAGE_DELETE_ENABLED: "true"
    volumes:
      - ./data:/var/lib/registry
      - ./auth:/auth:ro
YML
(cd "$R" && docker compose up -d)
say "Accès au registre pour root (docker et Watchtower)"
mkdir -p /root/.docker
[ -f /root/.docker/config.json ] && cp -n /root/.docker/config.json /root/.docker/config.json.avant-registre || true
printf '{"auths":{"localhost:5000":{"auth":"%s"}}}\n' '__AUTH__' > /root/.docker/config.json
chmod 600 /root/.docker/config.json
for i in $(seq 1 20); do curl -fsS -o /dev/null -u 'sarcade:__PW__' http://localhost:5000/v2/ && break; sleep 1; done
curl -fsS -o /dev/null -u 'sarcade:__PW__' http://localhost:5000/v2/ && echo "registre : OK (mot de passe accepté)"
curl -s -o /dev/null -w "registre sans mot de passe : %{http_code} (401 attendu)\n" http://localhost:5000/v2/
say "Serveur SARCADE : image prise dans le registre"
cp -n "$S/docker-compose.override.yml" "$S/docker-compose.override.yml.avant-registre" || true
if ! grep -q 'localhost:5000/sarcade-server' "$S/docker-compose.override.yml"; then
  sed -i '0,/^  server:$/s//  server:\n    image: localhost:5000\/sarcade-server:main/' "$S/docker-compose.override.yml"
fi
grep -n -A1 '^  server:$' "$S/docker-compose.override.yml" | head -2
cur=$(docker inspect -f '{{.Image}}' sarcade-server-1)
docker tag "$cur" localhost:5000/sarcade-server:main
docker push -q localhost:5000/sarcade-server:main
(cd "$S" && docker compose up -d --no-build server)
say "Watchtower : cloudflared et sarcade-server-1, toutes les 5 minutes"
cp -n "$W" "$W.avant-registre" || true
cat > "$W" <<'YML'
services:
  watchtower:
    image: nickfedor/watchtower:latest
    container_name: watchtower
    restart: unless-stopped
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock
      - /root/.docker/config.json:/config.json:ro
    environment:
      TZ: Europe/Paris
      WATCHTOWER_SCHEDULE: "0 */5 * * * *"
      WATCHTOWER_CLEANUP: "false"
    command: cloudflared sarcade-server-1
YML
# Même nom de projet que le Gestionnaire de conteneurs de DSM (« watchtower »).
docker network rm docker_default >/dev/null 2>&1 || true
docker compose -p watchtower -f "$W" up -d watchtower
say "Sauvegarde de la base chaque nuit (3 h 30, 14 gardées)"
cat > "$S/sarcade-sauvegarde-nuit.sh" <<'SH'
#!/bin/bash
export PATH=/usr/local/bin:/usr/bin:/bin
D=/volume1/docker/sarcade/sauvegardes; mkdir -p "$D"
f="$D/sarcade-$(date +%Y%m%d-%H%M)-nuit.sql.gz"
docker exec sarcade-postgres-1 pg_dump -U sarcade sarcade | gzip > "$f" && gzip -t "$f" || { rm -f "$f"; exit 1; }
ls -1t "$D"/sarcade-*-nuit.sql.gz | tail -n +15 | xargs -r rm -f
SH
chmod 750 "$S/sarcade-sauvegarde-nuit.sh"
grep -q 'sarcade-sauvegarde-nuit.sh' /etc/crontab || printf '30\t3\t*\t*\t*\troot\t/bin/bash %s\n' "$S/sarcade-sauvegarde-nuit.sh" >> /etc/crontab
(synosystemctl restart crond 2>/dev/null || systemctl restart crond 2>/dev/null || true)
grep 'sarcade-sauvegarde-nuit' /etc/crontab
say "Contrôle"
docker ps --format '{{.Names}} | {{.Image}} | {{.Status}}' | grep -E 'registry|watchtower|sarcade-server'
curl -fsS http://localhost:8000/health && echo
echo "== fin"
'@
$bash = $bash.Replace('__HASH__', $hash).Replace('__AUTH__', $auth).Replace('__PW__', $pw) -replace "`r`n", "`n"
$b64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($bash))
ssh -t $nas "echo $b64 | base64 -d > /tmp/sarcade-registre.sh && sudo bash /tmp/sarcade-registre.sh; r=`$?; rm -f /tmp/sarcade-registre.sh; exit `$r"
if ($LASTEXITCODE -ne 0) { throw 'installation sur le NAS en échec (messages ci-dessus)' }

Write-Host '== 3. Docker Desktop de l''i9 : registre du NAS accepté' -ForegroundColor Cyan
$dj = Join-Path $env:USERPROFILE '.docker\daemon.json'
$cfg = if (Test-Path $dj) { Get-Content -Raw $dj | ConvertFrom-Json } else { New-Object PSObject }
if (Test-Path $dj) { Copy-Item -Force $dj "$dj.avant-registre" }
$list = @(); if ($cfg.PSObject.Properties.Name -contains 'insecure-registries') { $list = @($cfg.'insecure-registries') }
if ($list -notcontains $registry) {
  $list += $registry
  $cfg | Add-Member -NotePropertyName 'insecure-registries' -NotePropertyValue $list -Force
  [IO.File]::WriteAllText($dj, ($cfg | ConvertTo-Json -Depth 10), (New-Object Text.UTF8Encoding($false)))
  Write-Host 'Docker Desktop redémarre (une minute environ)…'
  Stop-Process -Name 'Docker Desktop' -Force -ErrorAction SilentlyContinue
  Start-Sleep 5
  Start-Process "$env:ProgramFiles\Docker\Docker\Docker Desktop.exe"
  for ($i = 0; $i -lt 60; $i++) { docker info *> $null; if ($LASTEXITCODE -eq 0) { break }; Start-Sleep 3 }
}
$pw | docker login $registry -u sarcade --password-stdin
if ($LASTEXITCODE -ne 0) { throw 'connexion de l''i9 au registre refusée' }
Write-Host 'Mise à jour automatique installée.' -ForegroundColor Green
