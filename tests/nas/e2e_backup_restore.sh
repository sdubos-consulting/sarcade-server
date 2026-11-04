#!/bin/bash
# Essai de bout en bout (CI) : sarcade-backup sur une « exploitation »,
# puis sarcade-restore sur un « serveur de test » neuf, avec de vrais
# conteneurs (PostGIS + image du serveur construite par le job).
# Usage : tests/nas/e2e_backup_restore.sh <dossier de travail>
set -euo pipefail

HERE=$(cd "$(dirname "$0")" && pwd)
TOOLS=$(cd "$HERE/../../tool/nas" && pwd)
W=${1:?dossier de travail}
PROD=$W/sarcade; TEST=$W/sarcade-test; DEST=$W/sarcade-sauvegardes
mkdir -p "$PROD" "$TEST" "$DEST"
umask 077; printf 'phrase de passe CI %s\n' "$RANDOM$RANDOM" > "$W/pass"; umask 022

for d in "$PROD:8000" "$TEST:8010"; do
  dir=${d%%:*}; port=${d##*:}
  cp "$HERE/compose.ci.yaml" "$dir/compose.yaml"
  printf 'POSTGRES_PASSWORD=%s\nPORT=%s\n' "$(openssl rand -hex 12)" "$port" > "$dir/.env"
done
printf 'SARCADE_INSTANCE=test\nSARCADE_HEALTH=http://localhost:8010/health\n' > "$TEST/instance.env"

export SARCADE_ALLOW_NONROOT=1 SARCADE_ROOT=$PROD SARCADE_PROD_ROOT=$PROD SARCADE_RESTORE_ROOT=$TEST \
  SARCADE_BACKUP_DEST=$DEST SARCADE_BACKUP_PASSOUT=file:$W/pass SARCADE_BACKUP_PASSIN=file:$W/pass

wait_health() {
  for _ in $(seq 1 60); do curl -fsS "$1" >/dev/null 2>&1 && return 0; sleep 2; done
  echo "pas de réponse : $1" >&2; return 1
}
psql_in() { (cd "$1" && docker compose exec -T postgres psql -U sarcade -d sarcade -v ON_ERROR_STOP=1 -tAq "${@:2}"); }

echo "== Démarrage des deux instances"
(cd "$PROD" && docker compose up -d --wait)
(cd "$TEST" && docker compose up -d --wait)
wait_health http://localhost:8000/health
wait_health http://localhost:8010/health

echo "== Données de l'exploitation"
psql_in "$PROD" -c "CREATE TABLE ci_marker (id int primary key, note text, geom geometry(Point, 4326));
  INSERT INTO ci_marker VALUES (1, 'événement d''exercice', ST_SetSRID(ST_MakePoint(1.95, 48.8), 4326)),
                               (2, 'message', ST_SetSRID(ST_MakePoint(2.0, 48.9), 4326));"
(cd "$PROD" && docker compose exec -T server sh -c 'mkdir -p /var/lib/sarcade/files/personal/alice && echo bonjour > /var/lib/sarcade/files/personal/alice/note.txt')
(cd "$TEST" && docker compose exec -T server sh -c 'echo ancien > /var/lib/sarcade/files/ancien.txt')
prod_alembic=$(psql_in "$PROD" -c "select version_num from alembic_version")

echo "== Clé et sauvegarde"
SARCADE_BACKUP_KEY_BITS=2048 bash "$TOOLS/sarcade-backup.sh" --init-key
bash "$TOOLS/sarcade-backup.sh"
bash "$TOOLS/sarcade-backup.sh" --check
bash "$TOOLS/sarcade-backup.sh" --status
python3 - "$PROD/backups/last.json" <<'PY'
import json, sys
s = json.load(open(sys.argv[1]))
assert s["format"] == 1 and s["status"] == "ok", s
assert s["files_count"] >= 1 and s["db_bytes"] > 1024 and s["alembic"], s
print("last.json :", json.dumps(s, ensure_ascii=False))
PY
b=$(ls -d "$DEST"/sarcade-* | tail -n 1)
if grep -a -q "ci_marker" "$b/db.sql.gz.enc"; then echo "sauvegarde non chiffrée" >&2; exit 1; fi

echo "== Restauration refusée sur l'exploitation"
if SARCADE_RESTORE_ROOT=$PROD bash "$TOOLS/sarcade-restore.sh"; then echo "restauration sur l'exploitation acceptée" >&2; exit 1; fi

echo "== Restauration sur le serveur de test"
bash "$TOOLS/sarcade-restore.sh"
wait_health http://localhost:8010/health
test "$(psql_in "$TEST" -c "select count(*) from ci_marker")" = 2
test "$(psql_in "$TEST" -c "select round(ST_X(geom)::numeric, 2) from ci_marker where id = 1")" = 1.95
test "$(psql_in "$TEST" -c "select note from ci_marker where id = 1")" = "événement d'exercice"
test "$(psql_in "$TEST" -c "select version_num from alembic_version")" = "$prod_alembic"
(cd "$TEST" && docker compose exec -T server cat /var/lib/sarcade/files/personal/alice/note.txt) | grep -qx bonjour
if (cd "$TEST" && docker compose exec -T server test -e /var/lib/sarcade/files/ancien.txt); then
  echo "fichier ancien du test non effacé" >&2; exit 1
fi
python3 - "$PROD/backups/last-restore.json" <<'PY'
import json, sys
s = json.load(open(sys.argv[1]))
assert s["status"] == "ok", s
print("last-restore.json :", json.dumps(s, ensure_ascii=False))
PY
# L'exploitation n'a pas bougé.
test "$(psql_in "$PROD" -c "select count(*) from ci_marker")" = 2
wait_health http://localhost:8000/health
echo "== Essai de bout en bout réussi"
