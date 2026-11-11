#!/bin/sh
# Generates the Caddyfile of the SARCADE reverse proxy from the environment,
# then starts Caddy (docs/https-v0.1.md).
#
#   SARCADE_TLS_MODE      acme-dns (option A, default) | local-ca (option B)
#   SARCADE_DOMAIN        name of the server, e.g. adrasec78.sarcade.org
#   SARCADE_TLS_NAMES     other names or local IP addresses (option B), space separated
#   SARCADE_ACME_EMAIL    contact address for Let's Encrypt (option A)
#   SARCADE_DNS_PROVIDER  cloudflare | ovh | gandi (option A)
#   SARCADE_ACME_RESOLVERS public resolvers used to check the DNS challenge
#   SARCADE_UPSTREAM      address of the SARCADE server (default server:8000)
#
# The credentials of the DNS provider (CLOUDFLARE_API_TOKEN, OVH_*,
# GANDI_BEARER_TOKEN) are placed by the operator in .env, never in the
# repository.
set -eu

MODE="${SARCADE_TLS_MODE:-acme-dns}"
DOMAIN="${SARCADE_DOMAIN:-}"
UPSTREAM="${SARCADE_UPSTREAM:-server:8000}"
OUT=/etc/caddy/Caddyfile

if [ -z "$DOMAIN" ]; then
  echo "sarcade-caddy: SARCADE_DOMAIN is required" >&2
  exit 2
fi

site_body() {
  cat <<EOF
	encode zstd gzip
	reverse_proxy ${UPSTREAM}
	header Strict-Transport-Security "max-age=31536000"
EOF
}

case "$MODE" in
  acme-dns)
    PROVIDER="${SARCADE_DNS_PROVIDER:-}"
    case "$PROVIDER" in
      cloudflare) DNS="dns cloudflare {env.CLOUDFLARE_API_TOKEN}" ;;
      gandi) DNS="dns gandi {env.GANDI_BEARER_TOKEN}" ;;
      ovh) DNS="dns ovh {
			endpoint {env.OVH_ENDPOINT}
			application_key {env.OVH_APPLICATION_KEY}
			application_secret {env.OVH_APPLICATION_SECRET}
			consumer_key {env.OVH_CONSUMER_KEY}
		}" ;;
      *) echo "sarcade-caddy: SARCADE_DNS_PROVIDER must be cloudflare, ovh or gandi" >&2; exit 2 ;;
    esac
    {
      echo "{"
      [ -n "${SARCADE_ACME_EMAIL:-}" ] && echo "	email ${SARCADE_ACME_EMAIL}"
      echo "}"
      echo ""
      # Wildcard: the same certificate covers the other machines of the
      # organisation (Gateway, second server) under the same name.
      echo "${DOMAIN}, *.${DOMAIN} {"
      echo "	tls {"
      echo "		${DNS}"
      echo "		resolvers ${SARCADE_ACME_RESOLVERS:-1.1.1.1 9.9.9.9}"
      echo "	}"
      site_body
      echo "}"
    } > "$OUT"
    ;;
  local-ca)
    NAMES="${DOMAIN} ${SARCADE_TLS_NAMES:-}"
    SITES=""
    HTTP_SITES=""
    for n in $NAMES; do
      SITES="${SITES:+$SITES, }${n}"
      HTTP_SITES="${HTTP_SITES:+$HTTP_SITES, }http://${n}"
    done
    {
      echo "{"
      echo "	skip_install_trust"
      echo "	pki {"
      echo "		ca local {"
      echo "			name \"SARCADE ${DOMAIN}\""
      echo "		}"
      echo "	}"
      echo "}"
      echo ""
      # Plain HTTP: only the root certificate, so that a new device can
      # fetch it before trusting the server; everything else goes to HTTPS.
      echo "${HTTP_SITES} {"
      echo "	handle /sarcade-root.crt {"
      echo "		root * /data/caddy/pki/authorities/local"
      echo "		rewrite * /root.crt"
      echo "		header Content-Type application/x-x509-ca-cert"
      echo "		file_server"
      echo "	}"
      echo "	handle {"
      echo "		redir https://{host}{uri} 308"
      echo "	}"
      echo "}"
      echo ""
      echo "${SITES} {"
      echo "	tls internal"
      site_body
      echo "}"
    } > "$OUT"
    ;;
  *)
    echo "sarcade-caddy: SARCADE_TLS_MODE must be acme-dns or local-ca" >&2
    exit 2
    ;;
esac

caddy fmt --overwrite "$OUT" >/dev/null 2>&1 || true
caddy validate --config "$OUT" --adapter caddyfile
exec caddy run --config "$OUT" --adapter caddyfile
