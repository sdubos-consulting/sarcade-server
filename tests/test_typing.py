# SPDX-License-Identifier: AGPL-3.0-only
# © SDUBOS Consulting et contributeurs SARCADE
"""« en train d'écrire… » by the real time only (lot 29, group 7)."""
import json

from fastapi.testclient import TestClient

from sarcade.api.app import app
from sarcade.realtime import typing


def _sig(actor="TEL-01", conv="all"):
    return json.dumps({"type": "typing", "data": {"actor_id": actor, "conversation": conv}})


def test_one_signal_every_5_s_per_person_and_conversation():
    typing._last.clear()
    assert typing.read("e1", _sig(), now=100.0)["data"] == {"actor_id": "TEL-01", "conversation": "all"}
    assert typing.read("e1", _sig(), now=102.0) is None
    assert typing.read("e1", _sig(conv="group:pco"), now=102.0) is not None
    assert typing.read("e1", _sig(), now=105.5) is not None
    assert typing.read("e1", "ping") is None and typing.read("e1", "{bad") is None
    assert typing.read("e1", json.dumps({"type": "other", "data": {}})) is None


def test_relayed_to_the_others_never_to_the_sender():
    typing._last.clear()
    c = TestClient(app)
    with c.websocket_connect("/api/v0.1/events/e1/ws") as a, c.websocket_connect("/api/v0.1/events/e1/ws") as b:
        a.send_text(_sig())
        assert b.receive_json() == {"type": "typing", "data": {"actor_id": "TEL-01", "conversation": "all"}}
        a.send_text("ping")
        assert a.receive_json()["type"] == "pong", "the sender got nothing before its pong"
