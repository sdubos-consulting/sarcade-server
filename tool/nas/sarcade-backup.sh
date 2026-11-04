#!/bin/bash
# sarcade-backup : sauvegarde chiffrée du serveur SARCADE d'exploitation du NAS.
#
#   sarcade-backup              une sauvegarde (tâche planifiée DSM, chaque nuit)
#   sarcade-backup --init-key   crée la clé de chiffrement (une fois, mot de passe demandé)
#   sarcade-backup --status     dernière sauvegarde, âge, liste
#   sarcade-backup --check      code 1 si la dernière sauvegarde réussie a plus
#                               de 26 h (tâche DSM avec e-mail en cas d'échec)
#
# Contenu : base (pg_dump, SQL compressé) et fichiers envoyés
# (SARCADE_FILE_ROOT du serveur), chiffrés avec la clé publique
# $ROOT/secrets/backup-key.pub.pem (la clé privée, protégée par le mot de
# passe du porteur, ne sert qu'à la restauration : sarcade-restore).
# Destination : un dossier partagé Synology hors des volumes Docker
# (/volume1/sarcade-sauvegardes par défaut), copié ailleurs par Hyper Backup.
# Rétention : 14 jours. État lisible par le serveur (Diagnostic) :
# $ROOT/backups/last.json (format dans docs/nas-backup-v0.1.md).
set -euo pipefail

ROOT=${SARCADE_ROOT:-/volume1/docker/sarcade}
DEST=${SARCADE_BACKUP_DEST:-/volume1/sarcade-sauvegardes}
STATE_DIR=${SARCADE_BACKUP_STATE_DIR:-$ROOT/backups}
KEY=${SARCADE_BACKUP_KEY:-$ROOT/secrets/backup-key.pem}
PUB=${SARCADE_BACKUP_PUBKEY:-$ROOT/secrets/backup-key.pub.pem}
KEEP_DAYS=${SARCADE_BACKUP_KEEP_DAYS:-14}
MAX_AGE_H=${SARCADE_BACKUP_MAX_AGE_H:-26}
DB_SERVICE=${SARCADE_DB_SERVICE:-postgres}
SERVER_SERVICE=${SARCADE_SERVER_SERVICE:-server}
REPO=${SARCADE_REPO:-$ROOT/sarcade-server}
OPENSSL=${SARCADE_OPENSSL:-openssl}
STATE=$STATE_DIR/last.json

compose() { (cd "$ROOT" && docker compose "$@"); }
die() { printf 'ERREUR : %s\n' "$*" >&2; exit 1; }
now_iso() { date -u +%Y-%m-%dT%H:%M:%SZ; }
# Empreinte courte de la clé publique (même valeur pour la clé privée).
fingerprint() {  # fingerprint <options pkey…>
  "$OPENSSL" pkey "$@" -pubout -outform DER 2>/dev/null | sha256sum | cut -c1-16
}

# Lecture d'un champ de last.json (vide si absent).
state_get() {
  [ -f "$STATE" ] || return 0
  python3 -c 'import json,sys
try:
    v=json.load(open(sys.argv[1])).get(sys.argv[2])
except Exception:
    v=None
print("" if v is None else v)' "$STATE" "$1"
}

# Écrit last.json (atomique). Les valeurs viennent de l'environnement.
write_state() {  # write_state ok|error [message]
  mkdir -p "$STATE_DIR"
  B_STATUS=$1 B_ERROR=${2:-} B_STATE=$STATE B_ATTEMPT=${attempt_at:-$(now_iso)} \
  B_DEST=$DEST B_KEEP=$KEEP_DAYS B_MAX=$MAX_AGE_H \
  python3 - <<'PY'
import json, os
e = os.environ
path = e["B_STATE"]
try:
    prev = json.load(open(path))
except Exception:
    prev = {}
ok = e["B_STATUS"] == "ok"
def num(name):
    v = e.get(name, "")
    return int(v) if v.isdigit() else None
state = {
    "format": 1,
    "status": e["B_STATUS"],
    "last_attempt_at": e["B_ATTEMPT"],
    "last_success_at": e.get("B_FINISHED") if ok else prev.get("last_success_at"),
    "last_success_name": e.get("B_NAME") if ok else prev.get("last_success_name"),
    "duration_s": num("B_DURATION") if ok else prev.get("duration_s"),
    "db_bytes": num("B_DB_BYTES") if ok else prev.get("db_bytes"),
    "files_bytes": num("B_FILES_BYTES") if ok else prev.get("files_bytes"),
    "files_count": num("B_FILES_COUNT") if ok else prev.get("files_count"),
    "alembic": (e.get("B_ALEMBIC") or None) if ok else prev.get("alembic"),
    "server_commit": (e.get("B_COMMIT") or None) if ok else prev.get("server_commit"),
    "key_fingerprint": (e.get("B_KEYFP") or None) if ok else prev.get("key_fingerprint"),
    "destination": e["B_DEST"],
    "kept": num("B_KEPT") if ok else prev.get("kept"),
    "retention_days": int(e["B_KEEP"]),
    "max_age_hours": int(e["B_MAX"]),
    "error": None if ok else (e.get("B_ERROR") or "échec"),
}
tmp = path + ".tmp"
with open(tmp, "w") as f:
    json.dump(state, f, ensure_ascii=False, indent=2)
    f.write("\n")
os.chmod(tmp, 0o644)
os.replace(tmp, path)
PY
}

age_hours() {  # âge en heures (entier) d'une date ISO UTC, vide si illisible
  python3 -c 'import sys,datetime as d
try:
    t=d.datetime.strptime(sys.argv[1],"%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=d.timezone.utc)
    print(int((d.datetime.now(d.timezone.utc)-t).total_seconds()//3600))
except Exception:
    pass' "$1"
}

list_backups() { find "$DEST" -mindepth 1 -maxdepth 1 -type d -name 'sarcade-*' 2>/dev/null | sort; }

init_key() {
  [ -f "$KEY" ] && die "clé déjà présente : $KEY (empreinte $(fingerprint -pubin -in "$PUB")) ; rien n'est changé"
  mkdir -p "$(dirname "$KEY")"
  ( umask 077
    echo "Choisissez le mot de passe de la clé de sauvegarde (demandé deux fois, puis une troisième pour vérifier)."
    "$OPENSSL" genpkey -algorithm RSA -pkeyopt rsa_keygen_bits:"${SARCADE_BACKUP_KEY_BITS:-4096}" -aes-256-cbc \
      ${SARCADE_BACKUP_PASSOUT:+-pass "$SARCADE_BACKUP_PASSOUT"} -out "$KEY.tmp"
    "$OPENSSL" pkey -in "$KEY.tmp" ${SARCADE_BACKUP_PASSOUT:+-passin "$SARCADE_BACKUP_PASSOUT"} \
      -pubout -out "$PUB.tmp"
  ) || { rm -f "$KEY.tmp" "$PUB.tmp"; die "clé non créée"; }
  mv "$KEY.tmp" "$KEY"; mv "$PUB.tmp" "$PUB"; chmod 600 "$KEY"; chmod 644 "$PUB"
  echo "Clé créée : $KEY (privée, protégée par le mot de passe) et $PUB (publique)"
  echo "Empreinte : $(fingerprint -pubin -in "$PUB")"
}

status() {
  if [ ! -f "$STATE" ]; then echo "Aucune sauvegarde enregistrée ($STATE absent)"; else
    local ok_at; ok_at=$(state_get last_success_at)
    echo "État          : $(state_get status) (dernier essai $(state_get last_attempt_at))"
    echo "Dernière OK   : ${ok_at:-aucune}${ok_at:+, il y a $(age_hours "$ok_at") h} $(state_get last_success_name)"
    [ -n "$(state_get error)" ] && echo "Erreur        : $(state_get error)"
  fi
  echo "Destination   : $DEST ($(list_backups | wc -l | tr -d ' ') sauvegardes, $(du -sh "$DEST" 2>/dev/null | cut -f1))"
  list_backups | tail -n 3 | sed 's#.*/#  #'
  local r=$STATE_DIR/last-restore.json
  [ -f "$r" ] && python3 -c 'import json,sys;d=json.load(open(sys.argv[1]));print("Restauration  : %s le %s (%s)"%(d.get("status"),d.get("restored_at"),d.get("backup_name")))' "$r"
  return 0
}

check() {
  local ok_at age
  ok_at=$(state_get last_success_at)
  [ -n "$ok_at" ] || die "aucune sauvegarde réussie enregistrée ($STATE)"
  age=$(age_hours "$ok_at")
  [ -n "$age" ] || die "date illisible dans $STATE : $ok_at"
  [ "$age" -le "$MAX_AGE_H" ] || die "dernière sauvegarde réussie il y a $age h (plus de $MAX_AGE_H h) : $(state_get last_success_name) ; dernier essai : $(state_get status) $(state_get error)"
  echo "OK : dernière sauvegarde réussie il y a $age h ($(state_get last_success_name))"
}

case "${1:-}" in
  --init-key) init_key; exit 0 ;;
  --status) status; exit 0 ;;
  --check) check; exit 0 ;;
  "") ;;
  *) sed -n '2,19p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
esac

[ "$(id -u)" = 0 ] || [ "${SARCADE_ALLOW_NONROOT:-0}" = 1 ] || die "lancer en root (sudo su -)"
attempt_at=$(now_iso)
step="préparation"
work=""
fail() {
  local msg="échec à l'étape « $step »${1:+ : $1}"
  [ -n "$work" ] && rm -rf "$work"
  write_state error "$msg" 2>/dev/null || true
  printf '%s ERREUR : %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$msg" >&2
  exit 1
}
trap 'fail' ERR

[ -f "$PUB" ] || fail "clé publique absente ($PUB) : lancer d'abord sarcade-backup --init-key"
[ -d "$DEST" ] || fail "dossier de destination absent ($DEST) : créer le dossier partagé dans DSM"
command -v "$OPENSSL" >/dev/null || fail "openssl introuvable"
mkdir -p "$STATE_DIR"

# Un seul passage à la fois.
if command -v flock >/dev/null 2>&1; then
  exec 9>"$STATE_DIR/.lock"; flock -n 9 || fail "une sauvegarde est déjà en cours"
fi

start=$(date +%s)
name=sarcade-$(date +%Y%m%d-%H%M%S)
work=$DEST/.partial-$name
mkdir -p "$work"
chmod 700 "$work"

# Clé de session tirée au hasard pour chaque sauvegarde, chiffrée par la clé
# publique (RSA-OAEP) ; les données sont chiffrées en AES-256 avec elle.
step="chiffrement de la clé de session"
session=$("$OPENSSL" rand -hex 32)
printf '%s' "$session" | "$OPENSSL" pkeyutl -encrypt -pubin -inkey "$PUB" \
  -pkeyopt rsa_padding_mode:oaep -out "$work/key.enc"
enc() { K=$session "$OPENSSL" enc -aes-256-cbc -pbkdf2 -iter 10000 -md sha256 -salt -pass env:K; }

step="lecture de la base"
read -r db_user db_name < <(compose exec -T "$DB_SERVICE" sh -c 'printf "%s %s\n" "${POSTGRES_USER:-postgres}" "${POSTGRES_DB:-${POSTGRES_USER:-postgres}}"' | tr -d '\r')
alembic=$(compose exec -T "$DB_SERVICE" psql -U "$db_user" -d "$db_name" -tAc "select version_num from alembic_version" 2>/dev/null | tr -d '[:space:]' || true)

step="sauvegarde de la base (pg_dump)"
# Données des extensions PostGIS (tiger, topology) exclues : recréées par
# CREATE EXTENSION à la restauration (sinon doublons, restauration refusée).
compose exec -T "$DB_SERVICE" pg_dump -U "$db_user" --no-owner --no-privileges \
  --exclude-table-data='tiger.*' --exclude-table-data='tiger_data.*' --exclude-table-data='topology.*' \
  "$db_name" | gzip -6 | enc > "$work/db.sql.gz.enc"
[ "$(stat -c %s "$work/db.sql.gz.enc")" -gt 1024 ] || fail "sauvegarde de la base trop petite"

step="sauvegarde des fichiers envoyés"
files_count=$(compose exec -T "$SERVER_SERVICE" sh -c 'cd "${SARCADE_FILE_ROOT:-/var/lib/sarcade/files}" && find . -type f | wc -l' | tr -d '[:space:]')
compose exec -T "$SERVER_SERVICE" sh -c 'cd "${SARCADE_FILE_ROOT:-/var/lib/sarcade/files}" && tar -cf - .' | gzip -6 | enc > "$work/files.tar.gz.enc"

step="contrôle"
commit=$(git -C "$REPO" -c safe.directory='*' rev-parse --short HEAD 2>/dev/null || true)
keyfp=$(fingerprint -pubin -in "$PUB")
(cd "$work" && sha256sum key.enc db.sql.gz.enc files.tar.gz.enc > SHA256SUMS)
finished=$(now_iso)
B_NAME=$name B_FINISHED=$finished B_ALEMBIC=$alembic B_COMMIT=$commit B_KEYFP=$keyfp \
B_FILES_COUNT=$files_count python3 - "$work/manifest.json" <<'PY'
import json, os, sys
e = os.environ
json.dump({"format": 1, "name": e["B_NAME"], "finished_at": e["B_FINISHED"],
           "alembic": e["B_ALEMBIC"] or None, "server_commit": e["B_COMMIT"] or None,
           "key_fingerprint": e["B_KEYFP"], "files_count": int(e["B_FILES_COUNT"] or 0),
           "encryption": "RSA-OAEP (clé de session) + AES-256-CBC, openssl enc -pbkdf2 -iter 10000 -md sha256"},
          open(sys.argv[1], "w"), ensure_ascii=False, indent=2)
PY
mv "$work" "$DEST/$name"; work=""

step="rétention ($KEEP_DAYS jours)"
newest=$(list_backups | tail -n 1)
find "$DEST" -mindepth 1 -maxdepth 1 -type d -name 'sarcade-*' -mtime +$((KEEP_DAYS - 1)) -print0 \
  | while IFS= read -r -d '' d; do [ "$d" = "$newest" ] || rm -rf "$d"; done
find "$DEST" -mindepth 1 -maxdepth 1 -type d -name '.partial-*' -mtime +0 -exec rm -rf {} + 2>/dev/null || true
kept=$(list_backups | wc -l | tr -d ' ')

trap - ERR
B_NAME=$name B_FINISHED=$finished B_DURATION=$(( $(date +%s) - start )) \
B_DB_BYTES=$(stat -c %s "$DEST/$name/db.sql.gz.enc") B_FILES_BYTES=$(stat -c %s "$DEST/$name/files.tar.gz.enc") \
B_FILES_COUNT=$files_count B_ALEMBIC=$alembic B_COMMIT=$commit B_KEYFP=$keyfp B_KEPT=$kept \
  write_state ok
echo "$(date '+%Y-%m-%d %H:%M:%S') sauvegarde $name : base $(du -h "$DEST/$name/db.sql.gz.enc" | cut -f1), fichiers $(du -h "$DEST/$name/files.tar.gz.enc" | cut -f1) ($files_count), migration ${alembic:-?}, $kept gardées"
