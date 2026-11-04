#!/usr/bin/env bash
# sarcade-autoupdate : met à jour le SERVEUR DE TEST du NAS tout seul, depuis GitHub.
#
# Lancé toutes les 5 minutes par le Planificateur de tâches de DSM (en root).
# Le serveur de test (/volume1/docker/sarcade-test, test.sarcade.org) suit
# main ; le serveur d'exploitation (/volume1/docker/sarcade, app.sarcade.org)
# ne suit plus main : il est mis à jour à la main sur une version de
# référence, par sarcade-switch <étiquette>.
# À chaque passage :
#   1. ne fait rien si le fichier $ROOT/autoupdate.off existe (pause manuelle) ;
#   2. ne fait rien si le serveur de test n'est pas installé, ou si $ROOT est
#      le serveur d'exploitation (sauf SARCADE_AUTO_PRODUCTION=1, ancien mode) ;
#   3. ne fait rien si le serveur de test a été basculé à la main sur une
#      autre branche (recette d'une PR) : seule $BRANCH (main) est suivie ;
#   4. nouveau commit serveur sur $BRANCH, avec la CI GitHub au vert :
#      sarcade-switch $BRANCH (sauvegarde de la base si nouvelles migrations,
#      refus d'une base plus récente, reconstruction, attente de /health),
#      puis réinstallation des outils tool/nas/sarcade-*.sh ;
#   5. sinon, nouveau client web publié (pré-version web-dev) : reconstruction
#      du serveur seul pour l'embarquer.
# Journal : $ROOT/autoupdate.log (500 dernières lignes).
#
#   sarcade-autoupdate            un passage (ce que fait la tâche planifiée)
#   sarcade-autoupdate --status   dernier état connu, sans rien changer
#   sarcade-autoupdate --dry-run  dit ce qu'il ferait, sans rien changer
set -uo pipefail

PROD_ROOT=${SARCADE_PROD_ROOT:-/volume1/docker/sarcade}
ROOT=${SARCADE_ROOT:-${SARCADE_TEST_ROOT:-/volume1/docker/sarcade-test}}
# Réglages propres à l'instance (SARCADE_INSTANCE, SARCADE_HEALTH…), sans secret.
# shellcheck disable=SC1091
[ -f "$ROOT/instance.env" ] && . "$ROOT/instance.env"
REPO=${SARCADE_REPO:-$ROOT/sarcade-server}
BRANCH=${SARCADE_AUTO_BRANCH:-main}
SERVER_REPO=${SARCADE_SERVER_REPO:-sdubos-consulting/sarcade-server}
APP_REPO=${SARCADE_APP_REPO:-sdubos-consulting/sarcade-app}
SWITCH=${SARCADE_SWITCH:-/usr/local/bin/sarcade-switch}
BIN=${SARCADE_BIN:-/usr/local/bin}
HEALTH=${SARCADE_HEALTH:-http://localhost:8010/health}
STATE=$ROOT/.autoupdate
LOG=$ROOT/autoupdate.log
API=${SARCADE_GITHUB_API:-https://api.github.com}

# Une ligne par jour dans le journal de l'exploitation, sans rien créer
# dans un serveur de test absent.
note_daily() {  # note_daily <clé> <message>
  local dir=$PROD_ROOT/.autoupdate stamp
  mkdir -p "$dir" 2>/dev/null || return 0
  stamp="$dir/$1-$(date +%F)"
  [ -f "$stamp" ] && return 0
  touch "$stamp"
  printf '%s %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$2" | tee -a "$PROD_ROOT/autoupdate.log"
}
same_dir() { [ "$(cd "$1" 2>/dev/null && pwd -P)" = "$(cd "$2" 2>/dev/null && pwd -P)" ]; }

if [ "${1:-}" != --status ] && [ "${1:-}" != --dry-run ] && [ -n "${1:-}" ]; then
  sed -n '2,25p' "$0" | sed 's/^# \{0,1\}//'; exit 0
fi

if same_dir "$ROOT" "$PROD_ROOT" && [ "${SARCADE_AUTO_PRODUCTION:-0}" != 1 ]; then
  note_daily production "rien : $ROOT est le serveur d'exploitation, mis à jour à la main (sarcade-switch <étiquette>) ; la mise à jour automatique suit le serveur de test"
  exit 0
fi
if [ ! -d "$REPO/.git" ]; then
  note_daily no-test "rien : serveur de test non installé ($REPO absent) ; l'exploitation reste sur sa version (sarcade-switch <étiquette>)"
  [ "${1:-}" = --status ] && echo "Serveur de test non installé : $REPO absent"
  exit 0
fi

mkdir -p "$STATE"
git_() { git -C "$REPO" -c safe.directory='*' "$@"; }
compose() { (cd "$ROOT" && docker compose "$@"); }
log() { printf '%s %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" | tee -a "$LOG"; }
trim_log() { [ -f "$LOG" ] && tail -n 500 "$LOG" > "$LOG.tmp" && mv "$LOG.tmp" "$LOG"; }
json() { python3 -c "import sys,json;d=json.load(sys.stdin);print($1)" 2>/dev/null; }

case "${1:-}" in
  --status)
    echo "Instance       : ${SARCADE_INSTANCE:-test} ($ROOT)"
    echo "Branche suivie : $BRANCH"
    echo "Pause          : $([ -f "$ROOT/autoupdate.off" ] && echo oui || echo non)"
    echo "Code du NAS    : $(git_ branch --show-current) $(git_ log --oneline -1)"
    echo "Client web     : $(cat "$STATE/web" 2>/dev/null || echo inconnu)"
    echo "Derniers passages :"; tail -n 8 "$LOG" 2>/dev/null
    exit 0 ;;
  --dry-run) DRY=1 ;;
  *) DRY=0 ;;
esac

[ "$(id -u)" = 0 ] || [ "${SARCADE_ALLOW_NONROOT:-0}" = 1 ] || { echo "lancer en root" >&2; exit 1; }
[ -f "$ROOT/autoupdate.off" ] && exit 0

# Un seul passage à la fois (une reconstruction peut dépasser 5 minutes).
if command -v flock >/dev/null 2>&1; then
  exec 9>"$STATE/lock"; flock -n 9 || exit 0
else
  mkdir "$STATE/lock.d" 2>/dev/null || exit 0
  trap 'rmdir "$STATE/lock.d"' EXIT
fi

current=$(git_ branch --show-current)
if [ "$current" != "$BRANCH" ]; then
  # Recette manuelle en cours : on ne touche à rien, une seule ligne par jour.
  stamp="$STATE/manual-$(date +%F)"
  [ -f "$stamp" ] || { log "pause : serveur $ROOT basculé à la main sur $current (suivi automatique de $BRANCH seulement)"; touch "$stamp"; }
  exit 0
fi

git_ fetch -q origin "$BRANCH" || { log "erreur : git fetch impossible"; exit 1; }
local_sha=$(git_ rev-parse HEAD)
remote_sha=$(git_ rev-parse "origin/$BRANCH")

# Client web publié par la CI de sarcade-app (pré-version web-dev).
web_now=$(curl -fsS --max-time 20 "$API/repos/$APP_REPO/releases/tags/web-dev" \
  | json "max(a['updated_at'] for a in d['assets'])")
web_done=$(cat "$STATE/web" 2>/dev/null || true)

if [ "$local_sha" != "$remote_sha" ]; then
  # Nouveau code serveur : seulement si la CI de ce commit est terminée et verte.
  ci=$(curl -fsS --max-time 20 "$API/repos/$SERVER_REPO/commits/$remote_sha/check-runs" \
    | json "'pending' if any(r['status']!='completed' for r in d['check_runs']) or not d['check_runs'] else ('success' if all(r['conclusion'] in ('success','skipped','neutral') for r in d['check_runs']) else 'failure')")
  case "$ci" in
    success) ;;
    pending|"") exit 0 ;;  # on repassera dans 5 minutes
    *) stamp="$STATE/failed-$remote_sha"
       [ -f "$stamp" ] || { log "attente : CI en échec pour ${remote_sha:0:7}, NAS laissé sur ${local_sha:0:7}"; touch "$stamp"; }
       exit 0 ;;
  esac
  log "mise à jour serveur : ${local_sha:0:7} -> ${remote_sha:0:7} ($(git_ log --format=%s -1 "$remote_sha"))"
  [ "$DRY" = 1 ] && { log "(essai : rien n'est fait)"; exit 0; }
  extra=()
  same_dir "$ROOT" "$PROD_ROOT" && extra=(--branche)
  if SARCADE_ROOT="$ROOT" "$SWITCH" "$BRANCH" "${extra[@]}" >> "$LOG" 2>&1; then
    [ -n "$web_now" ] && echo "$web_now" > "$STATE/web"
    # Les outils du NAS suivent main (version du commit installé sur le test).
    for f in "$REPO"/tool/nas/sarcade-*.sh; do
      [ -f "$f" ] && install -m 755 "$f" "$BIN/$(basename "$f" .sh)"
    done
    log "terminé : serveur ${remote_sha:0:7}, client web $web_now"
  else
    log "ÉCHEC de sarcade-switch (voir ci-dessus) : intervention manuelle nécessaire"
    touch "$ROOT/autoupdate.off"
    log "mise à jour automatique mise en pause (supprimer $ROOT/autoupdate.off pour reprendre)"
  fi
  trim_log; exit 0
fi

if [ -n "$web_now" ] && [ "$web_now" != "$web_done" ]; then
  log "nouveau client web ($web_now) : reconstruction du serveur"
  [ "$DRY" = 1 ] && { log "(essai : rien n'est fait)"; exit 0; }
  # Reconstruction normale : le Dockerfile voit lui-même le nouveau client.
  if compose build server >> "$LOG" 2>&1 \
     && compose up -d server >> "$LOG" 2>&1; then
    for i in $(seq 1 45); do curl -fsS --max-time 3 "$HEALTH" >/dev/null 2>&1 && break; sleep 2; done
    if curl -fsS --max-time 3 "$HEALTH" >/dev/null 2>&1; then
      echo "$web_now" > "$STATE/web"; log "terminé : client web $web_now"
    else
      log "ÉCHEC : le serveur ne répond pas après la reconstruction"
    fi
  else
    log "ÉCHEC de la reconstruction (voir ci-dessus)"
  fi
  trim_log
fi
exit 0
