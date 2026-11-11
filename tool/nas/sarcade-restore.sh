#!/bin/bash
# sarcade-restore : restaure une sauvegarde de sarcade-backup sur le SERVEUR DE TEST.
#
#   sarcade-restore             la dernière sauvegarde
#   sarcade-restore <nom>       une sauvegarde précise (ex. sarcade-20261105-024700)
#   sarcade-restore --list      sauvegardes disponibles
#
# Cible : /volume1/docker/sarcade-test (SARCADE_RESTORE_ROOT). La base du test
# est effacée puis remplacée, ses fichiers envoyés aussi ; le serveur de test
# redémarre (migrations à jour au démarrage) et le résultat est vérifié.
# Le mot de passe de la clé de sauvegarde est demandé (jamais affiché).
# L'exploitation n'est jamais visée, sauf secours explicite :
# SARCADE_RESTORE_ROOT=/volume1/docker/sarcade sarcade-restore <nom> --exploitation
# (confirmation à taper).
set -euo pipefail

PROD_ROOT=${SARCADE_PROD_ROOT:-/volume1/docker/sarcade}
TARGET=${SARCADE_RESTORE_ROOT:-/volume1/docker/sarcade-test}
DEST=${SARCADE_BACKUP_DEST:-/volume1/sarcade-sauvegardes}
STATE_DIR=${SARCADE_BACKUP_STATE_DIR:-$PROD_ROOT/backups}
KEY=${SARCADE_BACKUP_KEY:-$PROD_ROOT/secrets/backup-key.pem}
OPENSSL=${SARCADE_OPENSSL:-openssl}
DB_SERVICE=${SARCADE_DB_SERVICE:-postgres}
SERVER_SERVICE=${SARCADE_SERVER_SERVICE:-server}
WAIT_S=${SARCADE_WAIT_S:-180}
HEALTH_DEFAULT=http://localhost:8010/health
# Adresse de santé de la cible : SARCADE_HEALTH, sinon instance.env du
# dossier cible, sinon port 8010 (test) ou 8000 (exploitation).
health_of() {
  ( given=${SARCADE_HEALTH:-}; SARCADE_HEALTH=""
    # shellcheck disable=SC1091
    [ -f "$1/instance.env" ] && . "$1/instance.env"
    d=$HEALTH_DEFAULT; same_dir "$1" "$PROD_ROOT" && d=http://localhost:8000/health
    echo "${given:-${SARCADE_HEALTH:-$d}}" )
}

compose() { (cd "$TARGET" && docker compose "$@"); }
say() { printf '\n== %s\n' "$*"; }
die() {
  printf '\nERREUR : %s\n' "$*" >&2
  [ -n "${name:-}" ] && write_restore_state error "$*"
  exit 1
}
same_dir() { [ "$(cd "$1" 2>/dev/null && pwd -P)" = "$(cd "$2" 2>/dev/null && pwd -P)" ]; }
list_backups() { find "$DEST" -mindepth 1 -maxdepth 1 -type d -name 'sarcade-*' 2>/dev/null | sort; }
now_iso() { date -u +%Y-%m-%dT%H:%M:%SZ; }

write_restore_state() {  # write_restore_state ok|error [message]
  mkdir -p "$STATE_DIR" 2>/dev/null || return 0
  R_STATUS=$1 R_ERROR=${2:-} R_PATH=$STATE_DIR/last-restore.json R_AT=$(now_iso) \
  R_NAME=${name:-} R_TARGET=$TARGET R_ALEMBIC=${alembic:-} R_COUNTS=${counts:-} \
  python3 - <<'PY' || true
import json, os
e = os.environ
counts = {}
for item in (e.get("R_COUNTS") or "").split():
    k, _, v = item.partition("=")
    counts[k] = int(v) if v.isdigit() else None
state = {"format": 1, "status": e["R_STATUS"], "restored_at": e["R_AT"],
         "backup_name": e["R_NAME"] or None, "target": e["R_TARGET"],
         "alembic": e["R_ALEMBIC"] or None, "counts": counts,
         "error": None if e["R_STATUS"] == "ok" else (e["R_ERROR"] or "échec")}
tmp = e["R_PATH"] + ".tmp"
with open(tmp, "w") as f:
    json.dump(state, f, ensure_ascii=False, indent=2)
    f.write("\n")
os.chmod(tmp, 0o644)
os.replace(tmp, e["R_PATH"])
PY
}

choice=""; to_prod=0
for a in "$@"; do
  case "$a" in
    -h|--help) sed -n '2,15p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    --list) list_backups | sed 's#.*/##'; exit 0 ;;
    --exploitation) to_prod=1 ;;
    -*) die "option inconnue : $a" ;;
    *) choice=$a ;;
  esac
done

[ "$(id -u)" = 0 ] || [ "${SARCADE_ALLOW_NONROOT:-0}" = 1 ] || die "lancer en root (sudo su -)"
[ -d "$TARGET" ] || die "serveur cible absent : $TARGET (installer d'abord le serveur de test)"
if same_dir "$TARGET" "$PROD_ROOT"; then
  [ "$to_prod" = 1 ] || die "la cible est le serveur d'exploitation ($TARGET) : restauration refusée.
Essai de restauration : sur le serveur de test (cible par défaut). Secours seulement : --exploitation."
  printf 'Restauration sur l'"'"'EXPLOITATION : la base et les fichiers actuels seront remplacés.\nTaper EXPLOITATION pour confirmer : '
  confirm=${SARCADE_RESTORE_CONFIRM:-}
  [ -n "$confirm" ] || read -r confirm
  [ "$confirm" = EXPLOITATION ] || die "non confirmé, rien n'est changé"
fi

if [ -z "$choice" ] || [ "$choice" = latest ]; then
  src=$(list_backups | tail -n 1)
  [ -n "$src" ] || die "aucune sauvegarde dans $DEST"
elif [ -d "$choice" ]; then src=$choice
else src=$DEST/$choice
fi
[ -d "$src" ] || die "sauvegarde introuvable : $choice (voir sarcade-restore --list)"
name=$(basename "$src")
HEALTH=$(health_of "$TARGET")
alembic=""; counts=""
trap 'die "échec à l'"'"'étape « ${step:-?} »"' ERR

step="contrôle de la sauvegarde"
say "Sauvegarde $name ($(du -sh "$src" | cut -f1)) vers $TARGET"
(cd "$src" && sha256sum -c --quiet SHA256SUMS) || die "sauvegarde abîmée (empreintes différentes) : $src"
[ -f "$KEY" ] || die "clé privée absente : $KEY"

step="déchiffrement de la clé de session"
echo "Mot de passe de la clé de sauvegarde :"
session=$("$OPENSSL" pkeyutl -decrypt -inkey "$KEY" ${SARCADE_BACKUP_PASSIN:+-passin "$SARCADE_BACKUP_PASSIN"} \
  -pkeyopt rsa_padding_mode:oaep -in "$src/key.enc") || die "mot de passe ou clé incorrects"
dec() { K=$session "$OPENSSL" enc -d -aes-256-cbc -pbkdf2 -iter 10000 -md sha256 -pass env:K -in "$1"; }

step="démarrage de la base cible"
compose up -d "$DB_SERVICE" >/dev/null
read -r db_user db_name < <(compose exec -T "$DB_SERVICE" sh -c 'printf "%s %s\n" "${POSTGRES_USER:-postgres}" "${POSTGRES_DB:-${POSTGRES_USER:-postgres}}"' | tr -d '\r')
for _ in $(seq 1 30); do compose exec -T "$DB_SERVICE" pg_isready -q -U "$db_user" -d postgres && break; sleep 2; done
psql_() { compose exec -T "$DB_SERVICE" psql -U "$db_user" -v ON_ERROR_STOP=1 -q "$@"; }

step="arrêt du serveur cible"
say "Arrêt du serveur cible"
compose stop "$SERVER_SERVICE"

step="restauration de la base"
say "Base : effacement de $db_name puis restauration"
psql_ -d postgres -c "DROP DATABASE IF EXISTS \"$db_name\" WITH (FORCE)"
psql_ -d postgres -c "CREATE DATABASE \"$db_name\" OWNER \"$db_user\""
dec "$src/db.sql.gz.enc" | gunzip | psql_ -d "$db_name" >/dev/null

step="restauration des fichiers envoyés"
say "Fichiers envoyés"
dec "$src/files.tar.gz.enc" | gunzip | compose run --rm --no-deps -T "$SERVER_SERVICE" sh -c \
  'set -e; d="${SARCADE_FILE_ROOT:-/var/lib/sarcade/files}"; mkdir -p "$d"; find "$d" -mindepth 1 -delete; tar -C "$d" -xf -; find "$d" -type f | wc -l'

step="redémarrage du serveur cible"
say "Redémarrage du serveur cible (migrations au démarrage)"
compose up -d "$SERVER_SERVICE"
ok=0
for _ in $(seq 1 $(( WAIT_S / 3 ))); do
  if curl -fsS --max-time 3 "$HEALTH" >/dev/null 2>&1; then ok=1; break; fi
  sleep 3
done
if [ "$ok" != 1 ]; then
  compose logs --tail 40 "$SERVER_SERVICE" || true
  die "le serveur cible ne répond pas après $WAIT_S s (journal ci-dessus)"
fi

step="vérification"
alembic=$(psql_ -d "$db_name" -tAc "select version_num from alembic_version" | tr -d '[:space:]')
for t in events messages map_features pois logbook users shared_files personal_files; do
  n=$(psql_ -d "$db_name" -tAc "select count(*) from $t" 2>/dev/null | tr -d '[:space:]' || true)
  counts="$counts $t=${n:-?}"
done
trap - ERR
write_restore_state ok
say "Terminé"
echo "Sauvegarde : $name"
echo "Cible      : $TARGET (santé : $(curl -fsS --max-time 5 "$HEALTH"))"
echo "Base       : migration $alembic"
echo "Contenu    :$counts"
