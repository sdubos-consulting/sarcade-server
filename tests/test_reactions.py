# SPDX-License-Identifier: AGPL-3.0-only
# © SDUBOS Consulting et contributeurs SARCADE
"""Reactions on a message (lot 29, group 7)."""
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import BigInteger, create_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from sarcade.api import app as app_module
from sarcade.api.app import app
from sarcade.api.deps import get_db
from sarcade.db.base import Base
from sarcade.db.models import (AckRow, CommGroupRow, EventRow, LogbookRow, MessageRow, SecurityEventRow,
                               ServerSettingRow, SharedFileRow, SyncOperationRow, ReactionRow)
from sarcade.messages import deletion


@compiles(BigInteger, "sqlite")
def _bigint_sqlite(type_, compiler, **kw):  # autoincrement keys on SQLite
    return "INTEGER"


@pytest.fixture
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine, tables=[t.__table__ for t in (
        EventRow, LogbookRow, MessageRow, AckRow, SyncOperationRow, SharedFileRow, ServerSettingRow,
        SecurityEventRow, CommGroupRow, ReactionRow)])
    session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)()
    session.add(EventRow(id="e1", name="Exercice", kind="exercise", status="active",
                         created_at=datetime(2026, 10, 29, tzinfo=UTC), version=0))
    session.commit()
    yield session
    session.close()


@pytest.fixture
def client(db, monkeypatch):
    async def _quiet(*a, **k):
        return None
    monkeypatch.setattr(app_module.manager, "broadcast", _quiet)
    monkeypatch.setattr(app_module.security_journal, "record", lambda *a, **k: None)
    app.dependency_overrides[get_db] = lambda: db
    yield TestClient(app)
    app.dependency_overrides.pop(get_db, None)


def _iso(t):
    return t.isoformat().replace("+00:00", "Z")


def _send(client, mid, sender="TEL-01", when=None, attachments=None):
    when = when or datetime.now(UTC)
    p = {"id": mid, "event_id": "e1", "sender_id": sender, "recipient_ids": [], "priority": "routine",
         "body": "Véhicule repéré", "created_at": _iso(when), "attachments": attachments or []}
    op = {"operation_id": f"op-{mid}", "event_id": "e1", "object_id": mid, "object_type": "message",
          "action": "create", "client_time": _iso(when), "payload": p}
    return client.post("/api/v0.1/sync", json=[op]).json()[0]


def _react(client, mid, actor, emoji, action="create", n=1):
    op = {"operation_id": f"r-{mid}-{actor}-{n}", "event_id": "e1", "object_id": f"{mid}:{actor}", "object_type": "reaction",
          "action": action, "client_time": _iso(datetime.now(UTC)), "payload": {"message_id": mid, "actor_id": actor, "emoji": emoji}}
    return client.post("/api/v0.1/sync", json=[op]).json()[0]


def test_one_reaction_per_person_changed_then_removed(client, db):
    _send(client, "m1")
    r0 = _react(client, "m1", "PCO", "👍")
    assert r0["status"] == "accepted", r0["reason"]
    assert _react(client, "m1", "PCO", "❤️", "update", 2)["status"] == "accepted"
    assert _react(client, "m1", "TEL-02", "🙏")["status"] == "accepted"
    got = sorted((r["actor_id"], r["emoji"]) for r in client.get("/api/v0.1/events/e1/reactions").json())
    assert got == [("PCO", "❤️"), ("TEL-02", "🙏")]
    assert _react(client, "m1", "PCO", None, "delete", 3)["status"] == "accepted"
    assert [r["actor_id"] for r in client.get("/api/v0.1/events/e1/reactions").json()] == ["TEL-02"]
    assert db.query(LogbookRow).filter(LogbookRow.kind == "reaction").count() == 0, "not in the Main courante"


def test_never_on_a_distress_message_nor_an_unknown_emoji(client):
    p = {"id": "m4", "event_id": "e1", "sender_id": "TEL-01", "recipient_ids": [], "priority": "distress",
         "body": "🆘 SOS", "created_at": _iso(datetime.now(UTC)), "attachments": []}
    client.post("/api/v0.1/sync", json=[{"operation_id": "op-m4", "event_id": "e1", "object_id": "m4", "object_type": "message",
                                         "action": "create", "client_time": _iso(datetime.now(UTC)), "payload": p}])
    assert _react(client, "m4", "PCO", "👍")["status"] == "rejected"
    _send(client, "m5")
    assert _react(client, "m5", "PCO", "🔥")["status"] == "rejected"
