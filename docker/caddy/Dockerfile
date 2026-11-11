# Reverse proxy HTTPS of SARCADE (docs/https-v0.1.md).
# Caddy with the DNS modules needed by the DNS-01 challenge of option A
# (Let's Encrypt certificate without opening the server on the Internet).
FROM caddy:2.10-builder AS build
RUN xcaddy build \
    --with github.com/caddy-dns/cloudflare \
    --with github.com/caddy-dns/ovh \
    --with github.com/caddy-dns/gandi

FROM caddy:2.10
COPY --from=build /usr/bin/caddy /usr/bin/caddy
COPY entrypoint.sh /usr/local/bin/sarcade-caddy
RUN chmod +x /usr/local/bin/sarcade-caddy
ENTRYPOINT ["/usr/local/bin/sarcade-caddy"]
