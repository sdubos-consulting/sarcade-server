# Partie NAS de meteofrance-cle-installer.ps1 (lancée en root par ce script) :
# clé lue dans /tmp/sarcade-cle puis effacée, placée dans le .env, transmise
# au serveur, serveur redémarré. Jamais affichée.
set -euo pipefail
export PATH=/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin
S=/volume1/docker/sarcade
K=/tmp/sarcade-cle
[ -s "$K" ] || { echo "ÉCHEC : clé absente de $K"; exit 1; }
CLE="$(tr -d '\r\n' < "$K")"
rm -f "$K"
cp -n "$S/.env" "$S/.env.avant-meteofrance" || true
grep -v '^SARCADE_METEOFRANCE_API_KEY=' "$S/.env" > /tmp/sarcade-env.tmp || true
printf 'SARCADE_METEOFRANCE_API_KEY=%s\n' "$CLE" >> /tmp/sarcade-env.tmp
cat /tmp/sarcade-env.tmp > "$S/.env"
rm -f /tmp/sarcade-env.tmp
CLE=
echo "clé placée dans $S/.env (non affichée)"
if ! grep -qs 'SARCADE_METEOFRANCE_API_KEY' "$S/compose.yaml" "$S/docker-compose.override.yml"; then
  cp -n "$S/docker-compose.override.yml" "$S/docker-compose.override.yml.avant-meteofrance" || true
  LIGNE='      SARCADE_METEOFRANCE_API_KEY: ${SARCADE_METEOFRANCE_API_KEY:-}'
  if grep -q '^      SARCADE_ADMIN_TOKEN:' "$S/docker-compose.override.yml"; then
    ANCRE='^      SARCADE_ADMIN_TOKEN:'
  elif grep -q '^      SARCADE_ADMIN_EMAILS:' "$S/docker-compose.override.yml"; then
    ANCRE='^      SARCADE_ADMIN_EMAILS:'
  else
    echo "ÉCHEC : impossible de transmettre la clé au serveur (compose inattendu)"; exit 1
  fi
  awk -v a="$ANCRE" -v l="$LIGNE" '{print} !fait && $0 ~ a {print l; fait=1}' "$S/docker-compose.override.yml" > /tmp/sarcade-override.tmp
  cat /tmp/sarcade-override.tmp > "$S/docker-compose.override.yml"
  rm -f /tmp/sarcade-override.tmp
  echo "clé transmise au serveur (docker-compose.override.yml)"
fi
if [ -z "$(cd "$S" && docker compose ps -q server)" ] && docker inspect sarcade-server-1 >/dev/null 2>&1; then
  echo "conteneur recréé par Watchtower : retiré pour que Compose le recrée"
  docker rm -f sarcade-server-1 >/dev/null
fi
(cd "$S" && docker compose up -d --no-build server)
for i in $(seq 1 60); do curl -fsS -o /dev/null http://localhost:8000/health && break; sleep 2; done
docker exec sarcade-server-1 sh -c 'test -n "$SARCADE_METEOFRANCE_API_KEY" && echo "serveur : clé présente"'
echo "== fin"
