"""Hand-off of the Cloudflare Access session to the SARCADE applications
(« Accès protégé par Cloudflare Access », decisions of 23 October 2026).

Behind Cloudflare Access, every request that reaches the server carries the
application token of the user: the ``CF_Authorization`` cookie and the
``Cf-Access-Jwt-Assertion`` header. The applications open this page in an
embedded browser window: the operator signs in with Cloudflare (email and
code), then the page hands the token to the application, which sends it
back with its own calls (``cf-access-token`` header and cookie).

The page only echoes the caller's own token, never stores or logs it. Its
signature is checked by Cloudflare before the request reaches the server;
the server-side check of the token is a separate setting (card « Accès
Cloudflare, serveur »).
"""
from __future__ import annotations

import base64
import html
import json
import re
from datetime import UTC, datetime

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from sarcade.access.verify import identity_of, verifier

router = APIRouter(prefix="/api/v0.1/access")

_JWT = re.compile(r"^[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]*$")

NO_STORE = {"Cache-Control": "no-store, max-age=0", "Pragma": "no-cache",
            "X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer"}


def token_claims(token: str) -> dict:
    """Readable claims of a JWT, without checking its signature (display only)."""
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        data = json.loads(base64.urlsafe_b64decode(payload.encode()))
        return data if isinstance(data, dict) else {}
    except (IndexError, ValueError, UnicodeDecodeError):
        return {}


def access_token(request: Request) -> str | None:
    """Token of the caller: the cookie the browser holds, else the header."""
    token = request.cookies.get("CF_Authorization") or request.headers.get("cf-access-jwt-assertion")
    if not token or len(token) > 8192 or not _JWT.match(token):
        return None
    return token


_PAGE = """<!doctype html>
<html lang="fr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex">
<title>SARCADE · connexion</title>
<style>
 body{{font-family:system-ui,sans-serif;margin:0;padding:24px;background:#f5f7fb;color:#1b2533}}
 .card{{max-width:520px;margin:0 auto;background:#fff;border-radius:14px;padding:22px;box-shadow:0 2px 10px #0001}}
 h1{{font-size:20px;margin:0 0 12px}} p{{line-height:1.45}}
 button{{font:inherit;padding:10px 16px;border-radius:10px;border:0;background:#173a6a;color:#fff;cursor:pointer}}
 .ok{{color:#1b7a3a}} .warn{{color:#a24b00}} small{{color:#5b6675}}
</style></head><body><div class="card">{body}</div>{script}</body></html>"""


@router.get("/handoff", response_class=HTMLResponse)
def handoff(request: Request):
    token = access_token(request)
    if token is None:
        body = ("<h1>SARCADE</h1><p class=\"warn\">Ce serveur n'est pas derrière Cloudflare Access "
                "à cette adresse : aucune connexion n'est nécessaire.</p>"
                "<p><small>Une adresse locale (réseau de l'ADRASEC) fonctionne sans connexion.</small></p>")
        return HTMLResponse(_PAGE.format(body=body, script=""), headers=NO_STORE)

    claims = token_claims(token)
    email = html.escape(str(claims.get("email") or ""))
    expiry = ""
    if isinstance(claims.get("exp"), (int, float)):
        expiry = datetime.fromtimestamp(claims["exp"], UTC).strftime("%d/%m/%Y %H:%M UTC")
    body = (
        "<h1>SARCADE · connecté</h1>"
        f"<p class=\"ok\">Connexion réussie{(' pour <b>' + email + '</b>') if email else ''}.</p>"
        f"{('<p><small>Session valable jusqu’au ' + expiry + '.</small></p>') if expiry else ''}"
        "<p id=\"auto\">Retour à l'application…</p>"
        "<div id=\"manual\" hidden><p>Si l'application ne reprend pas la main, copie le jeton "
        "et colle-le dans SARCADE (Paramètres, « Se connecter »).</p>"
        "<p><button id=\"copy\">Copier le jeton</button> <span id=\"done\" class=\"ok\" hidden>Copié</span></p></div>"
    )
    script = (
        "<script>(function(){var t=" + json.dumps(token) + ";"
        "if(window.SarcadeAccess&&window.SarcadeAccess.postMessage){window.SarcadeAccess.postMessage(t);return;}"
        "document.getElementById('auto').hidden=true;document.getElementById('manual').hidden=false;"
        "document.getElementById('copy').onclick=function(){navigator.clipboard.writeText(t).then(function(){"
        "document.getElementById('done').hidden=false;});};})();</script>"
    )
    return HTMLResponse(_PAGE.format(body=body, script=script), headers=NO_STORE)


@router.get("/session")
def session(request: Request):
    """Who is connected through Access, for the applications (never the token)."""
    token = access_token(request)
    if token is None:
        return {"protected": False}
    claims = token_claims(token)
    exp = claims.get("exp")
    identity = identity_of(request)
    return {
        "protected": True,
        "email": claims.get("email"),
        "expires_at": datetime.fromtimestamp(exp, UTC) if isinstance(exp, (int, float)) else None,
        # Server-side check: true (signature, issuer, audience checked), false
        # (refused, mode « log »), null (not checked at this address).
        "verified": True if identity is not None else (False if hasattr(request.state, "access_denied") else None),
        "verify_reason": getattr(request.state, "access_denied", None),
    }


@router.get("/status")
def status():
    """State of the server-side check, for the recette and the console:
    mode, keys, counts of accepted and refused requests. No secret."""
    return verifier().status()
