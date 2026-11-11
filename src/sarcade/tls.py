"""HTTPS of the server (note « Mises à jour et sécurité », section HTTPS).

The certificate is handled by the Caddy reverse proxy (``docker/caddy``):

- ``acme-dns`` (option A, default): Let's Encrypt certificate obtained by a
  DNS-01 challenge for a name of the organisation such as
  ``adrasec78.sarcade.org``, renewed automatically; the name points to the
  local address of the server in the local DNS, so the server needs no
  opening on the Internet;
- ``local-ca`` (option B): certificate of a local certification authority,
  for the installations that never go online; the root certificate is
  installed once on each device;
- ``off``: plain HTTP (development, transition).

The server itself only needs to know the mode and the public HTTPS address,
which it gives to the clients so that they switch to HTTPS.
"""
from __future__ import annotations

import os
from urllib.parse import urlparse

MODES = ("acme-dns", "local-ca", "off")


def mode() -> str:
    value = os.getenv("SARCADE_TLS_MODE", "off").strip().lower()
    return value if value in MODES else "off"


def public_url() -> str | None:
    """HTTPS address announced to the clients (``SARCADE_PUBLIC_URL``)."""
    url = os.getenv("SARCADE_PUBLIC_URL", "").strip().rstrip("/")
    if not url:
        return None
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname:
        return None
    return url


def state() -> dict:
    url = public_url()
    return {
        "mode": mode(),
        "https_url": url if mode() != "off" else None,
        # Root certificate to install on the devices (option B only), served
        # over HTTP by the reverse proxy so that a new device can fetch it.
        "root_certificate_url": (url.replace("https://", "http://", 1) + "/sarcade-root.crt")
        if url and mode() == "local-ca" else None,
    }
