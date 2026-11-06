# SPDX-License-Identifier: AGPL-3.0-only
# © SDUBOS Consulting et contributeurs SARCADE
"""PTT natif: channels and LiveKit access (lot 29)."""
from datetime import UTC, datetime

import jwt
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import BigInteger, create_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from sarcade import ptt
from sarcade.accounts.caller import Caller
from sarcade.api import ptt as ptt_api
from sarcade.api.app import app
from sarcade.api.deps import get_db
from sarcade.db.base import Base
from sarcade.db.models import CommGroupRow, EventRow

SECRET = "s" * 40


@compiles(BigInteger, "sqlite")
def _bigint_sqlite(type_, compiler, **kw):
    return "INTEGER"


def _group(gid, **data):
    return CommGroupRow(id=gid, event_id="e1", created_by="PCO-1", updated_by="PCO-1", data={"id": gid, "name": gid.upper(), "kind": "custom", "mode": "discussion", **data},
                        created_at=datetime(2026, 11, 6, tzinfo=UTC), updated_at=datetime(2026, 11, 6, tzinfo=UTC))


@pytest.fixture
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine, tables=[EventRow.__table__, CommGroupRow.__table__])
    s = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)()
    s.add(EventRow(id="e1", name="Exercice", kind="exercise", status="active", created_at=datetime(2026, 11, 5, tzinfo=UTC), version=0))
    s.add(_group("ouvert"))
    s.add(_group("equipe2", members=["TEL-02"]))
    s.add(_group("pco", mode="listen_only", senders=["PCO-1"]))
    s.add(_group("vieux", archived=True))
    s.commit()
    yield s
    s.close()


@pytest.fixture
def client(db, monkeypatch):
    monkeypatch.setenv("SARCADE_LIVEKIT_URL", "wss://ptt.example.org")
    monkeypatch.setenv("SARCADE_LIVEKIT_API_KEY", "APIsarcade")
    monkeypatch.setenv("SARCADE_LIVEKIT_API_SECRET", SECRET)
    monkeypatch.setattr(ptt_api, "caller_of", lambda request: Caller())
    app.dependency_overrides[get_db] = lambda: db
    yield TestClient(app)
    app.dependency_overrides.pop(get_db, None)


def test_channels_of_a_device(client):
    r = client.get("/api/v0.1/events/e1/ptt", params={"device_id": "TEL-01"}).json()
    assert r["available"] is True
    assert [(c["id"], c["can_talk"]) for c in r["channels"]] == [("general", True), ("ouvert", True), ("pco", False)]
    mine = client.get("/api/v0.1/events/e1/ptt", params={"device_id": "TEL-02"}).json()
    assert "equipe2" in [c["id"] for c in mine["channels"]]


def test_token_joins_one_room_and_talks_only_when_allowed(client):
    r = client.post("/api/v0.1/events/e1/ptt/token", json={"device_id": "TEL-01", "channel": "pco", "name": "Équipe 1"})
    assert r.status_code == 200
    body = r.json()
    assert body["url"] == "wss://ptt.example.org" and body["room"] == "sarcade-e1-pco" and body["can_talk"] is False
    claims = jwt.decode(body["token"], SECRET, algorithms=["HS256"])
    assert claims["iss"] == "APIsarcade" and claims["sub"] == "TEL-01" and claims["name"] == "Équipe 1"
    assert claims["video"] == {"room": "sarcade-e1-pco", "roomJoin": True, "canSubscribe": True, "canPublish": False,
                               "canPublishData": False, "canPublishSources": []}
    assert claims["exp"] - claims["nbf"] <= 6 * 3600 + 10
    talk = jwt.decode(client.post("/api/v0.1/events/e1/ptt/token", json={"device_id": "TEL-01", "channel": "general"}).json()["token"],
                      SECRET, algorithms=["HS256"])
    assert talk["video"]["canPublish"] is True and talk["video"]["canPublishSources"] == ["microphone"]


def test_refused_channels_and_devices(client, monkeypatch):
    assert client.post("/api/v0.1/events/e1/ptt/token", json={"device_id": "TEL-01", "channel": "equipe2"}).status_code == 403
    assert client.post("/api/v0.1/events/e1/ptt/token", json={"device_id": "TEL-01", "channel": "vieux"}).status_code == 403
    assert client.post("/api/v0.1/events/zz/ptt/token", json={"device_id": "TEL-01", "channel": "general"}).status_code == 404
    monkeypatch.setattr(ptt_api, "caller_of", lambda request: Caller(email="a@x.fr", via="device", device_id="TEL-09"))
    assert client.post("/api/v0.1/events/e1/ptt/token", json={"device_id": "TEL-01", "channel": "general"}).status_code == 403


def test_without_livekit(client, monkeypatch):
    monkeypatch.delenv("SARCADE_LIVEKIT_API_SECRET")
    assert client.get("/api/v0.1/events/e1/ptt", params={"device_id": "TEL-01"}).json() == {"available": False, "channels": []}
    assert client.post("/api/v0.1/events/e1/ptt/token", json={"device_id": "TEL-01", "channel": "general"}).status_code == 503
    assert ptt.config() is None
