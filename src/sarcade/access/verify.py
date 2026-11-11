"""Server-side check of the Cloudflare Access token (« Accès protégé par
Cloudflare Access », decision 6 of 23 October 2026).

Cloudflare checks the user before the request reaches the server. This
module checks it again on the server, so that a request reaching the public
address without going through Access (tunnel misconfigured, policy removed)
is refused: signature against the keys of the team, issuer, audience of the
SARCADE application, expiry.

Settings (environment of the server):

``SARCADE_ACCESS_VERIFY``
    ``off`` (default), ``log`` (check and record, never block: for the
    recette) or ``enforce`` (refuse).
``SARCADE_ACCESS_TEAM_DOMAIN``
    Team domain, e.g. ``sdubos.cloudflareaccess.com``.
``SARCADE_ACCESS_AUD``
    Application Audience (AUD) tag of the SARCADE application in Cloudflare,
    several separated by commas.
``SARCADE_ACCESS_HOSTS``
    Public host names checked, e.g. ``app.sarcade.org``. Every other address
    (local network ``192.168.0.110:8000``) is left without Access
    (decision 4).

The token is never stored nor logged.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
import time
import urllib.request
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime

import jwt
from jwt import PyJWK

log = logging.getLogger("sarcade.access")

MODES = ("off", "log", "enforce")
LEEWAY_S = 60
KEYS_TTL_S = 3600
KEYS_RETRY_S = 60
EXEMPT_PATHS = ("/health",)


@dataclass(frozen=True)
class AccessConfig:
    mode: str = "off"
    team_domain: str = ""
    audiences: tuple[str, ...] = ()
    hosts: tuple[str, ...] = ()
    error: str | None = None

    @property
    def issuer(self) -> str:
        return f"https://{self.team_domain}"

    @property
    def certs_url(self) -> str:
        return f"https://{self.team_domain}/cdn-cgi/access/certs"

    def applies_to(self, host: str | None) -> bool:
        return self.mode != "off" and normal_host(host) in self.hosts


def _split(raw: str | None) -> tuple[str, ...]:
    return tuple(p.strip() for p in (raw or "").split(",") if p.strip())


def normal_host(host: str | None) -> str:
    """Host name without port, lower case (``App.Sarcade.org:443``)."""
    host = (host or "").strip().lower()
    if host.startswith("["):
        return host.split("]")[0] + "]"
    return host.rsplit(":", 1)[0] if host.count(":") == 1 else host


def load_config(env: dict | None = None) -> AccessConfig:
    env = os.environ if env is None else env
    mode = (env.get("SARCADE_ACCESS_VERIFY") or "off").strip().lower()
    team = (env.get("SARCADE_ACCESS_TEAM_DOMAIN") or "").strip().lower()
    team = team.removeprefix("https://").rstrip("/")
    audiences = _split(env.get("SARCADE_ACCESS_AUD"))
    hosts = tuple(normal_host(h) for h in _split(env.get("SARCADE_ACCESS_HOSTS")))
    if mode not in MODES:
        return AccessConfig(error=f"SARCADE_ACCESS_VERIFY inconnu : {mode}")
    if mode == "off":
        return AccessConfig()
    missing = [name for name, value in (("SARCADE_ACCESS_TEAM_DOMAIN", team), ("SARCADE_ACCESS_AUD", audiences),
                                        ("SARCADE_ACCESS_HOSTS", hosts)) if not value]
    if missing:
        # Never guess: a check without audience or hosts is switched off.
        return AccessConfig(error="réglage incomplet : " + ", ".join(missing))
    if not team.endswith(".cloudflareaccess.com"):
        return AccessConfig(error="SARCADE_ACCESS_TEAM_DOMAIN doit finir par .cloudflareaccess.com")
    return AccessConfig(mode=mode, team_domain=team, audiences=audiences, hosts=hosts)


@dataclass(frozen=True)
class AccessIdentity:
    """Who Cloudflare let through: a person (email) or a service token
    (Gateway, ``common_name`` = client id)."""
    kind: str
    email: str | None = None
    subject: str | None = None
    common_name: str | None = None
    expires_at: datetime | None = None

    @property
    def label(self) -> str:
        return self.email or (f"jeton de service {self.common_name}" if self.common_name else "inconnu")


class AccessDenied(Exception):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def _fetch_json(url: str) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": "sarcade-server"})
    with urllib.request.urlopen(req, timeout=10) as resp:  # noqa: S310 (https, fixed host)
        return json.loads(resp.read(1_000_000))


class KeyStore:
    """Public keys of the team (JWKS), cached one hour; fetched again when a
    token names an unknown key, at most once a minute."""

    def __init__(self, url: str, fetch=_fetch_json, clock=time.monotonic):
        self.url, self._fetch, self._clock = url, fetch, clock
        self._keys: dict[str, PyJWK] = {}
        self._loaded_at: float | None = None
        self._tried_at: float | None = None
        self._lock = threading.Lock()
        self.last_error: str | None = None
        self.fetched_at: datetime | None = None

    @property
    def count(self) -> int:
        return len(self._keys)

    def _refresh(self) -> None:
        now = self._clock()
        if self._tried_at is not None and now - self._tried_at < KEYS_RETRY_S:
            return
        self._tried_at = now
        try:
            data = self._fetch(self.url)
            keys = {}
            for item in data.get("keys", []):
                if item.get("kid") and item.get("kty") == "RSA":
                    keys[item["kid"]] = PyJWK(item, algorithm="RS256")
            if not keys:
                raise ValueError("aucune clé RSA")
            self._keys, self._loaded_at = keys, now
            self.fetched_at, self.last_error = datetime.now(UTC), None
        except Exception as exc:  # network, JSON, key format: keep the old keys
            self.last_error = f"{type(exc).__name__}: {exc}"[:200]
            log.warning("Cloudflare Access: clés indisponibles (%s)", self.last_error)

    def get(self, kid: str) -> PyJWK | None:
        with self._lock:
            stale = self._loaded_at is None or self._clock() - self._loaded_at > KEYS_TTL_S
            if stale or kid not in self._keys:
                self._refresh()
            return self._keys.get(kid)


def request_token(headers, cookies) -> str | None:
    """Token set by Cloudflare: the ``Cf-Access-Jwt-Assertion`` header, else the cookie."""
    token = headers.get("cf-access-jwt-assertion") or cookies.get("CF_Authorization")
    if not token or len(token) > 8192:
        return None
    return token


class Verifier:
    def __init__(self, config: AccessConfig, keys: KeyStore | None = None):
        self.config = config
        self.keys = keys or (KeyStore(config.certs_url) if config.team_domain else None)
        self.counts: Counter = Counter()
        self.last_denied: dict | None = None

    def verify(self, token: str | None) -> AccessIdentity:
        if not token:
            raise AccessDenied("missing_token")
        try:
            header = jwt.get_unverified_header(token)
        except jwt.InvalidTokenError:
            raise AccessDenied("malformed_token") from None
        if header.get("alg") != "RS256" or not header.get("kid"):
            raise AccessDenied("unexpected_algorithm")
        key = self.keys.get(header["kid"]) if self.keys else None
        if key is None:
            raise AccessDenied("keys_unavailable" if self.keys and self.keys.count == 0 else "unknown_key")
        try:
            claims = jwt.decode(token, key=key.key, algorithms=["RS256"], audience=list(self.config.audiences),
                                issuer=self.config.issuer, leeway=LEEWAY_S,
                                options={"require": ["exp", "iss", "aud"]})
        except jwt.ExpiredSignatureError:
            raise AccessDenied("expired") from None
        except jwt.InvalidAudienceError:
            raise AccessDenied("wrong_audience") from None
        except jwt.InvalidIssuerError:
            raise AccessDenied("wrong_issuer") from None
        except jwt.InvalidSignatureError:
            raise AccessDenied("bad_signature") from None
        except jwt.InvalidTokenError:
            raise AccessDenied("invalid_token") from None
        exp = claims.get("exp")
        email = claims.get("email") if isinstance(claims.get("email"), str) else None
        common_name = claims.get("common_name") if isinstance(claims.get("common_name"), str) else None
        return AccessIdentity(
            kind="user" if email else "service", email=email,
            subject=claims.get("sub") if isinstance(claims.get("sub"), str) else None,
            common_name=common_name,
            expires_at=datetime.fromtimestamp(exp, UTC) if isinstance(exp, (int, float)) else None)

    def note(self, outcome: str, reason: str | None = None, *, path: str = "", client: str | None = None) -> None:
        self.counts[outcome if reason is None else f"{outcome}:{reason}"] += 1
        if outcome != "accepted":
            self.last_denied = {"at": datetime.now(UTC).isoformat(), "reason": reason, "path": path[:120]}
            log.warning("Cloudflare Access: jeton refusé (%s) %s depuis %s, mode %s",
                        reason, path[:120], client or "?", self.config.mode)

    def status(self) -> dict:
        """Diagnostics without any secret (no token, no audience value)."""
        c = self.config
        return {
            "mode": c.mode, "config_error": c.error, "team_domain": c.team_domain or None,
            "hosts": list(c.hosts), "audiences": len(c.audiences),
            "keys": self.keys.count if self.keys else 0,
            "keys_fetched_at": self.keys.fetched_at.isoformat() if self.keys and self.keys.fetched_at else None,
            "keys_error": self.keys.last_error if self.keys else None,
            "counts": dict(self.counts), "last_denied": self.last_denied,
        }


_verifier: Verifier | None = None


def verifier() -> Verifier:
    global _verifier
    if _verifier is None:
        config = load_config()
        if config.error:
            log.error("Cloudflare Access: vérification désactivée, %s", config.error)
        elif config.mode != "off":
            log.info("Cloudflare Access: vérification %s pour %s", config.mode, ", ".join(config.hosts))
        _verifier = Verifier(config)
    return _verifier


def set_verifier(v: Verifier | None) -> None:
    """Tests: replace the verifier (None reloads the environment)."""
    global _verifier
    _verifier = v


def _header_map(scope) -> dict[str, str]:
    return {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers") or []}


def _cookies(raw: str) -> dict[str, str]:
    jar = {}
    for part in (raw or "").split(";"):
        name, sep, value = part.strip().partition("=")
        if sep:
            jar[name] = value
    return jar


DENIED_BODY = json.dumps({"detail": "access_denied",
                          "message": "Connexion Cloudflare Access requise ou expirée"}).encode()


class AccessMiddleware:
    """Checks every HTTP and WebSocket request to a public host. The
    identity is put in ``scope["state"]["access"]`` for the endpoints."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] not in ("http", "websocket"):
            return await self.app(scope, receive, send)
        v = verifier()
        headers = _header_map(scope)
        path = scope.get("path", "")
        if not v.config.applies_to(headers.get("host")) or path in EXEMPT_PATHS:
            return await self.app(scope, receive, send)
        client = headers.get("cf-connecting-ip") or (scope.get("client") or ("?",))[0]
        token = request_token(headers, _cookies(headers.get("cookie", "")))
        try:
            # The key fetch may hit the network: keep it off the event loop.
            identity = await asyncio.to_thread(v.verify, token)
            v.note("accepted")
            scope.setdefault("state", {})["access"] = identity
        except AccessDenied as exc:
            v.note("denied", exc.reason, path=path, client=client)
            scope.setdefault("state", {})["access_denied"] = exc.reason
            if v.config.mode == "enforce":
                return await _deny(scope, send, exc.reason)
        return await self.app(scope, receive, send)


async def _deny(scope, send, reason: str):
    if scope["type"] == "websocket":
        # Closing before accepting answers the handshake with HTTP 403.
        await send({"type": "websocket.close", "code": 1008, "reason": "access_denied"})
        return
    status = 503 if reason == "keys_unavailable" else 403
    await send({"type": "http.response.start", "status": status, "headers": [
        (b"content-type", b"application/json"), (b"cache-control", b"no-store"),
        # Read by the applications as « Connexion requise » (isAccessChallenge).
        (b"www-authenticate", b'Cloudflare-Access realm="SARCADE"'),
        (b"x-sarcade-access", reason.encode()),
    ]})
    await send({"type": "http.response.body", "body": DENIED_BODY})


def identity_of(request_or_ws) -> AccessIdentity | None:
    return getattr(request_or_ws.state, "access", None) if hasattr(request_or_ws, "state") else None
