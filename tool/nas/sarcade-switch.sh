#!/bin/bash
# Bascule d'un serveur SARCADE du NAS sur une étiquette ou une branche.
#
#   sarcade-switch <étiquette>     exploitation : version de référence (ex. v0.1.0-exercice1)
#   sarcade-switch <branche>       serveur de test : ex. main, feat/aprs
#   sarcade-switch --list          étiquettes et branches disponibles
#   sarcade-switch --status        instance, version, commit, base, santé
#
# Instance : exploitation par défaut (/volume1/docker/sarcade) ; serveur de
# test avec SARCADE_ROOT=/volume1/docker/sarcade-test (ses réglages, port
# compris, sont lus dans $SARCADE_ROOT/instance.env).
# L'exploitation ne prend une branche qu'avec --branche (dépannage) : une
# fusion ne doit jamais arriver directement sur le serveur de l'exercice.
# Étapes : vérifie la base, sauvegarde si la cible ajoute des migrations,
# met le code sur la cible (copie locale écrasée), reconstruit, attend /health.
# Refuse une cible plus ancienne que la base (migration inconnue), sauf avec
# --force après restauration d'une sauvegarde.
set -euo pipefail

ROOT=${SARCADE_ROOT:-/volume1/docker/sarcade}
# Réglages propres à l'instance (serveur de test : SARCADE_INSTANCE=test,
# SARCADE_HEALTH=http://localhost:8010/health…), sans secret.
# shellcheck disable=SC1091
[ -f "$ROOT/instance.env" ] && . "$ROOT/instance.env"
INSTANCE=${SARCADE_INSTANCE:-exploitation}
REPO=${SARCADE_REPO:-$ROOT/sarcade-server}
BACKUPS=${SARCADE_BACKUPS:-$ROOT/sauvegardes}
DB_SERVICE=${SARCADE_DB_SERVICE:-postgres}
DB_USER=${SARCADE_DB_USER:-sarcade}
DB_NAME=${SARCADE_DB_NAME:-sarcade}
HEALTH=${SARCADE_HEALTH:-http://localhost:8000/health}
WAIT_S=${SARCADE_WAIT_S:-90}

# sudo du Synology : PATH réduit, docker est dans /usr/local/bin (ajouté en fin : les essais gardent leurs doublures).
export PATH="$PATH:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
# git : celui du NAS s'il existe, sinon celui de l'image du déclencheur de Forgejo (git n'est plus installé sur
# le NAS depuis l'arrêt du Git Server, 11/11/2026). safe.directory passé par l'environnement : il vaut aussi pour
# le dépôt d'origine (dépôt de Forgejo, d'un autre propriétaire), ce que « git -c » ne fait pas.
GIT_IMAGE=${SARCADE_GIT_IMAGE:-forgejo-declencheur:local}
git_cmd() {
  if [ "${SARCADE_GIT_DOCKER:-0}" != 1 ] && command -v git >/dev/null 2>&1; then
    GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=safe.directory GIT_CONFIG_VALUE_0='*' git "$@"
  else
    docker run --rm --user 0:0 --network none -e GIT_CONFIG_COUNT=1 -e GIT_CONFIG_KEY_0=safe.directory \
      -e GIT_CONFIG_VALUE_0='*' -v "${SARCADE_GIT_VOLUME:-/volume1}:${SARCADE_GIT_VOLUME:-/volume1}" \
      --entrypoint git "$GIT_IMAGE" "$@"
  fi
}
git_() { git_cmd -C "$REPO" "$@"; }
compose() { (cd "$ROOT" && docker compose "$@"); }
say() { printf '\n== %s\n' "$*"; }
die() { printf '\nERREUR : %s\n' "$*" >&2; exit 1; }

[ "$(id -u)" = 0 ] || [ "${SARCADE_ALLOW_NONROOT:-0}" = 1 ] || die "lancer en root (sudo su -)"
[ -d "$REPO/.git" ] || die "dépôt introuvable : $REPO"

db_version() {
  compose exec -T "$DB_SERVICE" psql -U "$DB_USER" -d "$DB_NAME" -tAc \
    "select version_num from alembic_version" 2>/dev/null | tr -d '[:space:]' || true
}
revisions_of() {  # revisions (numéros) présentes dans une révision git
  git_ ls-tree --name-only "$1" migrations/versions/ | sed -n 's#.*/\([0-9]\{4\}\)_.*\.py$#\1#p' | sort
}
# Version en service : étiquette exacte si le code est sur une étiquette,
# sinon branche.
current_ref() {
  git_ describe --tags --exact-match HEAD 2>/dev/null && return 0
  local b; b=$(git_ branch --show-current 2>/dev/null || true)
  echo "${b:-commit $(git_ rev-parse --short HEAD)}"
}
fetch() { git_ fetch -q --prune --tags --force origin; }

status() {
  echo "Instance : $INSTANCE ($ROOT)"
  echo "Version  : $(current_ref)"
  echo "Commit   : $(git_ log --oneline -1)"
  echo "Base     : migration $(db_version)"
  printf 'Santé    : '; curl -fsS --max-time 5 "$HEALTH" || echo "pas de réponse"; echo
}

case "${1:-}" in
  ""|-h|--help) sed -n '2,17p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
  --list)
    fetch
    echo "Étiquettes (versions de référence, les plus récentes d'abord) :"
    git_ tag --sort=-creatordate | head -n 15 | sed 's/^/  /'
    echo "Branches :"
    git_ branch -r | sed 's#origin/##' | grep -v HEAD
    exit 0 ;;
  --status) status; exit 0 ;;
esac

target=$1; shift
force=""; allow_branch=0
for a in "$@"; do
  case "$a" in
    --force) force=--force ;;
    --branche) allow_branch=1 ;;
    *) die "option inconnue : $a" ;;
  esac
done

say "Récupération des étiquettes et des branches"
fetch
if git_ rev-parse -q --verify "refs/tags/$target^{commit}" >/dev/null; then
  kind=étiquette; ref="refs/tags/$target"
elif git_ rev-parse -q --verify "origin/$target" >/dev/null; then
  kind=branche; ref="origin/$target"
else
  die "étiquette ou branche inconnue : $target (voir sarcade-switch --list)"
fi

if [ "$INSTANCE" = exploitation ] && [ "$kind" = branche ] && [ "$allow_branch" != 1 ]; then
  die "serveur d'exploitation : il suit une version de référence (étiquette), pas la branche $target.
Les essais vont sur le serveur de test : SARCADE_ROOT=/volume1/docker/sarcade-test sarcade-switch $target
Dépannage seulement : sarcade-switch $target --branche"
fi

dbv=$(db_version)
echo "Actuel   : $(current_ref), base en migration ${dbv:-inconnue}"
echo "Cible    : $kind $target ($(git_ log --oneline -1 "$ref"))"

if [ -n "$dbv" ] && ! revisions_of "$ref" | grep -qx "$dbv"; then
  [ "$force" = "--force" ] || die "la base est en migration $dbv, inconnue de $target (version plus ancienne).
Le serveur ne démarrerait pas. Restaurer d'abord une sauvegarde de $BACKUPS, puis relancer avec --force."
fi

new=$(comm -13 <(revisions_of HEAD) <(revisions_of "$ref") | tr '\n' ' ')
if [ -n "${new// /}" ]; then
  mkdir -p "$BACKUPS"
  file="$BACKUPS/sarcade-$(date +%Y%m%d-%H%M)-avant-${target//\//-}.sql.gz"
  say "Nouvelles migrations ($new) : sauvegarde de la base"
  compose exec -T "$DB_SERVICE" pg_dump -U "$DB_USER" "$DB_NAME" | gzip > "$file"
  [ -s "$file" ] || die "sauvegarde vide : $file"
  echo "Sauvegarde : $file ($(du -h "$file" | cut -f1))"
fi

say "Passage du code sur $target"
if [ "$kind" = étiquette ]; then
  git_ checkout -q --force --detach "$ref"
else
  git_ checkout -q --force -B "$target" "$ref"
fi
git_ log --oneline -1

say "Reconstruction (quelques minutes)"
# L'image vérifie elle-même le client web publié (ADD du Dockerfile) :
# le dernier client est pris sans réglage.
compose build
compose up -d

say "Attente du serveur"
for _ in $(seq 1 $(( WAIT_S / 2 ))); do
  if curl -fsS --max-time 3 "$HEALTH" >/dev/null 2>&1; then
    say "Terminé"; status; exit 0
  fi
  sleep 2
done
compose logs --tail 40 server || true
die "le serveur ne répond pas après $WAIT_S s (journal ci-dessus)"
