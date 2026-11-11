#!/usr/bin/env bash
# sarcade-update-gitlab : met le serveur d'exploitation du NAS sur main,
# sans GitHub (suspendu depuis le 6 novembre 2026).
#
# - code du serveur : dépôt sarcade/sarcade-server du Forgejo du NAS
#   (/volume1/docker/forgejo/data/git/repositories/sarcade/sarcade-server.git,
#   alimenté par l'i9 après chaque fusion, depuis le 11 novembre 2026), au lieu
#   du Git Server du NAS (en cours d'arrêt) et de github.com ;
# - client web : archive construite sur l'i9 (sarcade-app tool/build-web.ps1),
#   déposée dans /tmp par tool/nas/publish-nas.ps1, intégrée par
#   SARCADE_WEB=local du Dockerfile ;
# - sauvegarde de la base avant tout changement, image en service gardée
#   sous l'étiquette sarcade-server:avant-<commit> ;
# - aucun retour arrière automatique : en cas d'échec, le journal et la
#   marche à suivre sont affichés.
#
# Lancé en root sur le NAS par tool/nas/publish-nas.ps1 (depuis l'i9) :
#   sarcade-update-gitlab.sh /tmp/sarcade-web-<commit>.tar.gz
set -euo pipefail
export PATH=/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin

ROOT=${SARCADE_ROOT:-/volume1/docker/sarcade}
REPO=${SARCADE_REPO:-$ROOT/sarcade-server}
MIRROR=${SARCADE_MIRROR:-/volume1/docker/forgejo/data/git/repositories/sarcade/sarcade-server.git}
BACKUPS=${SARCADE_BACKUPS:-$ROOT/sauvegardes}
HEALTH=${SARCADE_HEALTH:-http://localhost:8000/health}
WAIT_S=${SARCADE_WAIT_S:-180}
WEB=${1:?archive du client web manquante}

# git : celui du NAS s'il existe, sinon celui de l'image du déclencheur de Forgejo (git n'est plus installé sur
# le NAS depuis l'arrêt du Git Server, 11/11/2026). safe.directory passé par l'environnement : il vaut aussi pour
# le dépôt de Forgejo (autre propriétaire) lu par fetch, ce que « git -c » ne fait pas.
GIT_IMAGE=${SARCADE_GIT_IMAGE:-forgejo-declencheur:local}
g() {
  if [ "${SARCADE_GIT_DOCKER:-0}" != 1 ] && command -v git >/dev/null 2>&1; then
    GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=safe.directory GIT_CONFIG_VALUE_0='*' git -C "$REPO" "$@"
  else
    docker run --rm --user 0:0 --network none -e GIT_CONFIG_COUNT=1 -e GIT_CONFIG_KEY_0=safe.directory \
      -e GIT_CONFIG_VALUE_0='*' -v "${SARCADE_GIT_VOLUME:-/volume1}:${SARCADE_GIT_VOLUME:-/volume1}" \
      --entrypoint git "$GIT_IMAGE" -C "$REPO" "$@"
  fi
}
compose() { (cd "$ROOT" && docker compose "$@"); }
say() { printf '\n== %s\n' "$*"; }
die() { printf '\nERREUR : %s\n' "$*" >&2; exit 1; }

[ "$(id -u)" = 0 ] || die "lancer en root"
[ -d "$REPO/.git" ] || die "dépôt introuvable : $REPO"
[ -d "$MIRROR" ] || die "dépôt du serveur introuvable dans Forgejo : $MIRROR"
[ -s "$WEB" ] || die "archive du client web absente ou vide : $WEB"
command -v docker >/dev/null || die "docker introuvable"

say "Contexte de construction du serveur"
ctx=$(compose config | awk '/^  server:/{s=1; next} s && /^  [a-z]/{exit} s && /context:/{print $2; exit}')
[ -n "$ctx" ] || die "contexte de construction du service server introuvable (docker compose config)"
echo "$ctx"
[ "$(cd "$ctx" && pwd -P)" = "$(cd "$REPO" && pwd -P)" ] || die "le serveur se construit depuis $ctx, pas depuis $REPO : rien n'est changé"

say "Code : dépôt Forgejo du NAS"
g remote set-url origin "$MIRROR"
g fetch -q --prune --force origin
before=$(g rev-parse --short HEAD)
target=$(g rev-parse --short origin/main)
echo "En service : $before ; cible : $target ($(g log --format=%s -1 origin/main))"

say "Sauvegarde de la base"
mkdir -p "$BACKUPS"
dump="$BACKUPS/sarcade-$(date +%Y%m%d-%H%M)-avant-$target.sql.gz"
compose exec -T postgres pg_dump -U sarcade sarcade | gzip > "$dump"
gzip -t "$dump" && [ "$(stat -c %s "$dump")" -gt 1000 ] || die "sauvegarde vide ou abîmée : $dump"
echo "$dump ($(du -h "$dump" | cut -f1))"

say "Image en service gardée"
old=$(docker inspect -f '{{.Image}}' "$(compose ps -q server)" 2>/dev/null || true)
if [ -n "$old" ]; then docker tag "$old" "sarcade-server:avant-$before"; echo "sarcade-server:avant-$before"; fi

say "Passage du code sur main ($target)"
g checkout -q --force -B main origin/main
g log --oneline -1
grep -q 'AS web-local' "$REPO/Dockerfile" || die "le Dockerfile de $target ne connaît pas SARCADE_WEB=local"
cp "$WEB" "$REPO/sarcade-web.tar.gz"

say "Construction de l'image (quelques minutes)"
compose build --build-arg SARCADE_WEB=local --build-arg "SARCADE_COMMIT=$(g rev-parse HEAD)" server
compose up -d

say "Attente du serveur (migrations de la base au démarrage)"
for _ in $(seq 1 $(( WAIT_S / 3 ))); do
  if curl -fsS --max-time 3 "$HEALTH" >/dev/null 2>&1; then
    say "Terminé"
    curl -fsS --max-time 3 "${HEALTH%/health}/version"; echo
    curl -fsS --max-time 3 "${HEALTH%/health}/sarcade_build.json"; echo
    exit 0
  fi
  sleep 3
done
compose logs --tail 60 server || true
die "le serveur ne répond pas après $WAIT_S s (journal ci-dessus).
Retour arrière : restaurer $dump, puis git checkout $before dans $REPO et
docker tag sarcade-server:avant-$before sur l'image du service server."
