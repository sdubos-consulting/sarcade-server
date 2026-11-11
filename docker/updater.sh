#!/bin/sh
# SARCADE server updater, run on the host every 5 minutes (cron or systemd
# timer), from the directory of docker-compose.yml.
#
# It asks the server whether to install (GET /admin/updates/plan: never
# during an active event; in a maintenance window or on « Installer
# maintenant »), then: backup of the database, check of the image signature
# (Sigstore, published by the Image workflow of this repository), new image,
# health check, rollback (previous image and database) if anything fails.
# Each step is reported to the server and goes to the security journal.
#
#   docker/updater.sh                      online update
#   docker/updater.sh --offline FILE.tar   signed package brought on a USB key
#                                          (made with « cosign save »)
#
# Configuration: /etc/sarcade/updater.env (placed by the operator, never in
# the repository): SARCADE_ADMIN_TOKEN, and optionally SARCADE_URL,
# SARCADE_BACKUP_DIR.
#
# Status: written with the server PR « updates and security », not yet run
# on a real installation nor with a real signed release (to try out).
set -eu

[ -f /etc/sarcade/updater.env ] && . /etc/sarcade/updater.env
URL="${SARCADE_URL:-http://localhost:8000}/api/v0.1"
IMAGE="ghcr.io/sdubos-consulting/sarcade-server"
BACKUP_DIR="${SARCADE_BACKUP_DIR:-/var/backups/sarcade}"
IDENTITY="^https://github.com/sdubos-consulting/sarcade-server/.github/workflows/image.yml@refs/tags/v"
ISSUER="https://token.actions.githubusercontent.com"
AUTH="Authorization: Bearer ${SARCADE_ADMIN_TOKEN:?SARCADE_ADMIN_TOKEN missing}"
OFFLINE=""
[ "${1:-}" = "--offline" ] && OFFLINE="${2:?package file missing}"

api() { curl -fsS -H "$AUTH" -H "Content-Type: application/json" "$@"; }
report() { api -X POST "$URL/admin/updates/report" -d "{\"version\":\"$1\",\"status\":\"$2\",\"detail\":\"$3\"}" >/dev/null || true; }
field() { python3 -c "import json,sys; v=json.load(sys.stdin).get('$1'); print('' if v is None else str(v).lower() if isinstance(v,bool) else v)"; }

PLAN="$(api "$URL/admin/updates/plan")"
INSTALL="$(echo "$PLAN" | field install)"
VERSION="$(echo "$PLAN" | field available)"
if [ -n "$OFFLINE" ]; then
  # The plan still decides: never during an active event.
  REASON="$(echo "$PLAN" | field reason)"
  [ "$REASON" = "active_event" ] && { echo "active event: offline update postponed"; exit 0; }
  VERSION="$(basename "$OFFLINE" .tar | sed 's/^sarcade-server-//')"
  INSTALL=true
fi
[ "$INSTALL" = "true" ] || { echo "no installation: $(echo "$PLAN" | field reason)"; exit 0; }

report "$VERSION" started "installation of $VERSION"
PREVIOUS="$(grep -E '^SARCADE_IMAGE_TAG=' .env 2>/dev/null | cut -d= -f2 || true)"
PREVIOUS="${PREVIOUS:-edge}"

# 1. Signature of the new image, before anything changes.
if [ -n "$OFFLINE" ]; then
  WORK="$(mktemp -d)"
  tar -xf "$OFFLINE" -C "$WORK"
  if ! cosign verify --local-image "$WORK" --offline \
       --certificate-identity-regexp "$IDENTITY" --certificate-oidc-issuer "$ISSUER" >/dev/null; then
    report "$VERSION" signature_invalid "offline package signature invalid"; exit 1
  fi
  cosign load --dir "$WORK" "$IMAGE:$VERSION"
else
  if ! cosign verify "$IMAGE:$VERSION" \
       --certificate-identity-regexp "$IDENTITY" --certificate-oidc-issuer "$ISSUER" >/dev/null; then
    report "$VERSION" signature_invalid "image signature invalid"; exit 1
  fi
fi

# 2. Backup of the database before the migrations.
mkdir -p "$BACKUP_DIR"
BACKUP="$BACKUP_DIR/sarcade-before-$VERSION-$(date -u +%Y%m%dT%H%M%SZ).dump"
if ! docker compose exec -T postgres sh -c 'pg_dump -Fc -U "$POSTGRES_USER" "$POSTGRES_DB"' > "$BACKUP"; then
  report "$VERSION" backup_failed "pg_dump failed"; exit 1
fi

set_tag() {
  if grep -qE '^SARCADE_IMAGE_TAG=' .env 2>/dev/null; then
    sed -i "s/^SARCADE_IMAGE_TAG=.*/SARCADE_IMAGE_TAG=$1/" .env
  else
    echo "SARCADE_IMAGE_TAG=$1" >> .env
  fi
}

healthy() {
  i=0
  while [ $i -lt 60 ]; do
    if [ "$(api "$URL/admin/updates" 2>/dev/null | field installed)" = "$VERSION" ]; then return 0; fi
    i=$((i + 1)); sleep 3
  done
  return 1
}

# 3. New image, migrations at start, health check.
set_tag "$VERSION"
[ -n "$OFFLINE" ] || docker compose pull server
docker compose up -d server
if healthy; then
  report "$VERSION" succeeded "installed, previous $PREVIOUS, backup $BACKUP"
  exit 0
fi

# 4. Rollback: previous image and database as before the migrations.
set_tag "$PREVIOUS"
docker compose stop server
docker compose exec -T postgres sh -c 'pg_restore --clean --if-exists -U "$POSTGRES_USER" -d "$POSTGRES_DB"' < "$BACKUP" || true
docker compose up -d server
sleep 20
report "$VERSION" rolled_back "health check failed, back to $PREVIOUS"
exit 1
