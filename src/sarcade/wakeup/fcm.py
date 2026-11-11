"""Firebase Cloud Messaging, HTTP v1 API, with the service account placed
by the operator (SARCADE_FCM_SERVICE_ACCOUNT: path of the JSON file)."""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

import httpx
import jwt

SCOPE = "https://www.googleapis.com/auth/firebase.messaging"


def load_account() -> dict | None:
    """Service account, or None when not configured (Android not woken)."""
    value = (os.getenv("SARCADE_FCM_SERVICE_ACCOUNT") or "").strip()
    if not value:
        return None
    try:
        text = value if value.startswith("{") else Path(value).read_text(encoding="utf-8")
        account = json.loads(text)
        for field in ("client_email", "private_key", "project_id"):
            if not account.get(field):
                return None
        return account
    except (OSError, ValueError):
        return None


class AccessToken:
    def __init__(self):
        self.value: str | None = None
        self.expires = 0.0

    async def get(self, client: httpx.AsyncClient, account: dict) -> str:
        if self.value and time.time() < self.expires - 120:
            return self.value
        now = int(time.time())
        token_uri = account.get("token_uri") or "https://oauth2.googleapis.com/token"
        assertion = jwt.encode({"iss": account["client_email"], "scope": SCOPE, "aud": token_uri,
                                "iat": now, "exp": now + 3600}, account["private_key"], algorithm="RS256")
        r = await client.post(token_uri, data={"grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                                               "assertion": assertion}, timeout=15)
        r.raise_for_status()
        body = r.json()
        self.value = body["access_token"]
        self.expires = time.time() + float(body.get("expires_in", 3600))
        return self.value


def title(data: dict[str, str]) -> str:
    if data.get("kind") == "test":
        return "SARCADE : réveil reçu"
    if data.get("priority") in ("immediate", "urgent"):
        return "SARCADE : message prioritaire"
    return "SARCADE : nouveau message"


def message(token: str, data: dict[str, str], high: bool, ios: bool = False) -> dict:
    """Android: data message only, the application decides what to show
    after its synchronisation. iPhone: iOS does not wake a closed
    application for a data message, so a generic notification is shown by
    the system (no content of the message, as on Android)."""
    msg = {"token": token, "data": data,
           "android": {"priority": "high" if high else "normal", "ttl": "3600s"}}
    if ios:
        headers = {"apns-priority": "10" if high else "5", "apns-push-type": "alert"}
        if data.get("tag"):
            # One notification per message or distress: a new alert with the
            # same collapse id replaces the previous one on the iPhone.
            headers["apns-collapse-id"] = data["tag"][:64]
        msg["apns"] = {
            "headers": headers,
            "payload": {"aps": {"alert": {"title": title(data), "body": "Ouvrez SARCADE pour le lire."},
                                "sound": "default", "content-available": 1,
                                **({"interruption-level": "time-sensitive"} if high else {})}},
        }
    return {"message": msg}


def is_gone(status: int, body: str) -> bool:
    """Token no longer valid: removed from the server."""
    return status == 404 or (status == 400 and "UNREGISTERED" in body) or "registration-token-not-registered" in body
