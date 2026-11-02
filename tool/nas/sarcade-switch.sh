#!/bin/bash
# Bascule du serveur SARCADE du NAS sur une branche, en une commande.
#
#   sarcade-switch <branche>       ex. sarcade-switch main, sarcade-switch feat/aprs
#   sarcade-switch --list          branches disponibles
#   sarcade-switch --status        branche, commit, version de la base, santé
#
# Étapes : vérifie la base, sauvegarde si la branche ajoute des migrations,
# met le code sur la branche (copie locale écrasée), reconstruit, attend /health.
# Refuse de passer sur une branche plus ancienne que la base (migration
# inconnue), sauf avec --force après restauration d'une sauvegarde.
set -euo pipefail

ROOT=${SARCADE_ROOT:-/volume1/docker/sarcade}
REPO=${SARCADE_REPO:-$ROOT/sarcade-server}
BACKUPS=${SARCADE_BACKUPS:-$ROOT/sauvegardes}
DB_SERVICE=${SARCADE_DB_SERVICE:-postgres}
DB_USER=${SARCADE_DB_USER:-sarcade}
DB_NAME=${SARCADE_DB_NAME:-sarcade}
HEALTH=${SARCADE_HEALTH:-http://localhost:8000/health}

git_() { git -C "$REPO" -c safe.directory='*' "$@"; }
compose() { (cd "$ROOT" && docker compose "$@"); }
say() { printf '\n== %s\n' "$*"; }
die() { printf '\nERREUR : %s\n' "$*" >&2; exit 1; }

[ "$(id -u)" = 0 ] || die "lancer en root (sudo su -)"
[ -d "$REPO/.git" ] || die "dépôt introuvable : $REPO"

db_version() {
  compose exec -T "$DB_SERVICE" psql -U "$DB_USER" -d "$DB_NAME" -tAc \
    "select version_num from alembic_version" 2>/dev/null | tr -d '[:space:]' || true
}
revisions_of() {  # revisions (numéros) présentes dans une révision git
  git_ ls-tree --name-only "$1" migrations/versions/ | sed -n 's#.*/\([0-9]\{4\}\)_.*\.py$#\1#p' | sort
}

status() {
  echo "Branche  : $(git_ branch --show-current)"
  echo "Commit   : $(git_ log --oneline -1)"
  echo "Base     : migration $(db_version)"
  printf 'Santé    : '; curl -fsS --max-time 5 "$HEALTH" || echo "pas de réponse"; echo
}

case "${1:-}" in
  ""|-h|--help) sed -n '2,11p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
  --list) git_ fetch -q --prune origin; git_ branch -r | sed 's#origin/##' | grep -v HEAD; exit 0 ;;
  --status) status; exit 0 ;;
esac

target=$1; force=${2:-}
say "Récupération des branches"
git_ fetch -q --prune origin
git_ rev-parse -q --verify "origin/$target" >/dev/null || die "branche inconnue : $target (voir sarcade-switch --list)"

current=$(git_ branch --show-current || echo "?")
dbv=$(db_version)
echo "Actuel   : $current, base en migration ${dbv:-inconnue}"
echo "Cible    : $target ($(git_ log --oneline -1 "origin/$target"))"

if [ -n "$dbv" ] && ! revisions_of "origin/$target" | grep -qx "$dbv"; then
  [ "$force" = "--force" ] || die "la base est en migration $dbv, inconnue de $target (branche plus ancienne).
Le serveur ne démarrerait pas. Restaurer d'abord une sauvegarde de $BACKUPS, puis relancer avec --force."
fi

new=$(comm -13 <(revisions_of HEAD) <(revisions_of "origin/$target") | tr '\n' ' ')
if [ -n "${new// /}" ]; then
  mkdir -p "$BACKUPS"
  file="$BACKUPS/sarcade-$(date +%Y%m%d-%H%M)-avant-${target//\//-}.sql.gz"
  say "Nouvelles migrations ($new) : sauvegarde de la base"
  compose exec -T "$DB_SERVICE" pg_dump -U "$DB_USER" "$DB_NAME" | gzip > "$file"
  [ -s "$file" ] || die "sauvegarde vide : $file"
  echo "Sauvegarde : $file ($(du -h "$file" | cut -f1))"
fi

say "Passage du code sur $target"
git_ checkout -q -B "$target" "origin/$target"
git_ log --oneline -1

say "Reconstruction (quelques minutes)"
# L'image vérifie elle-même le client web publié (ADD du Dockerfile) :
# le dernier client est pris sans réglage.
compose build
compose up -d

say "Attente du serveur"
for i in $(seq 1 45); do
  if curl -fsS --max-time 3 "$HEALTH" >/dev/null 2>&1; then
    say "Terminé"; status; exit 0
  fi
  sleep 2
done
compose logs --tail 40 server || true
die "le serveur ne répond pas après 90 s (journal ci-dessus)"
