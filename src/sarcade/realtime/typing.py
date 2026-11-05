# SPDX-License-Identifier: AGPL-3.0-only
# © SDUBOS Consulting et contributeurs SARCADE
"""« en train d'écrire… » (card « Messages : en train d'écrire », lot 29,
group 7): a very light signal, by the real time only, never written down nor
sent again; at most one every 5 s per person and conversation. Never by LoRa
nor APRS (the application sends it only on the real time).
"""
from __future__ import annotations

import json
import time

MIN_GAP_S = 5.0
_last: dict[tuple[str, str, str], float] = {}


def read(event_id: str, text: str, now: float | None = None) -> dict | None:
    """The « typing » payload to relay, or None (not a typing signal,
    malformed, or too soon after the previous one)."""
    if not text.startswith("{") or len(text) > 512:
        return None
    try:
        msg = json.loads(text)
    except ValueError:
        return None
    if not isinstance(msg, dict) or msg.get("type") != "typing" or not isinstance(msg.get("data"), dict):
        return None
    actor = str(msg["data"].get("actor_id") or "")[:64]
    conversation = str(msg["data"].get("conversation") or "")[:80]
    if not actor or not conversation:
        return None
    now = time.monotonic() if now is None else now
    key = (event_id, actor, conversation)
    if now - _last.get(key, -MIN_GAP_S) < MIN_GAP_S:
        return None
    _last[key] = now
    if len(_last) > 5000:  # forget the old ones
        for k in [k for k, t in _last.items() if now - t > 60]:
            _last.pop(k, None)
    return {"type": "typing", "data": {"actor_id": actor, "conversation": conversation}}
