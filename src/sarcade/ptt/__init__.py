# SPDX-License-Identifier: AGPL-3.0-only
# © SDUBOS Consulting et contributeurs SARCADE
"""PTT natif (talkie-walkie), card « PTT natif » (lot 29, decisions of
6 Nov 2026): WebRTC through a LiveKit server installed on the NAS (Apache
2.0). The SARCADE server only hands out the access: a short token per
device and channel, signed with the LiveKit secret of the NAS
(``SARCADE_LIVEKIT_API_KEY``, ``SARCADE_LIVEKIT_API_SECRET``); the voice
goes between the devices and LiveKit, never through SARCADE, and is never
recorded.

Channels: « Général » (every operator of the event) and one per group of
the event (its members, or everyone for an open group); listen-only groups:
only their senders, managers and the PCO talk.
"""
from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta

import jwt

TOKEN_TTL = timedelta(hours=6)
GENERAL = "general"


def config() -> dict | None:
    url = os.getenv("SARCADE_LIVEKIT_URL", "").strip()
    key = os.getenv("SARCADE_LIVEKIT_API_KEY", "").strip()
    secret = os.getenv("SARCADE_LIVEKIT_API_SECRET", "").strip()
    if not (url and key and secret):
        return None
    return {"url": url, "key": key, "secret": secret}


def room_name(event_id: str, channel: str) -> str:
    return f"sarcade-{event_id}-{channel}"[:120]


def token(cfg: dict, *, identity: str, name: str, room: str, can_talk: bool, now: datetime | None = None) -> str:
    """LiveKit access token (JWT HS256): join one room, listen, talk when
    allowed; nothing else (no data channel, no video)."""
    now = now or datetime.now(UTC)
    claims = {
        "iss": cfg["key"], "sub": identity, "name": name,
        "nbf": int((now - timedelta(seconds=10)).timestamp()),
        "exp": int((now + TOKEN_TTL).timestamp()),
        "video": {"room": room, "roomJoin": True, "canSubscribe": True, "canPublish": can_talk,
                  "canPublishData": False, "canPublishSources": ["microphone"] if can_talk else []},
    }
    return jwt.encode(claims, cfg["secret"], algorithm="HS256")


def channels(groups: list[dict], device_id: str) -> list[dict]:
    """Channels of a device: « Général », then its groups (members, or open
    to everyone), in the order of the groups; ``can_talk`` per channel."""
    from sarcade.groups.service import can_send, is_pco
    out = [{"id": GENERAL, "name": "Général", "can_talk": True}]
    for g in groups:
        if g.get("archived") or g.get("kind") == "all":
            continue
        members = g.get("members") or []
        if members and device_id not in members and not is_pco(device_id) and device_id not in (g.get("managers") or []):
            continue
        out.append({"id": str(g["id"]), "name": g.get("name") or "", "can_talk": can_send(g, device_id)})
    return out
