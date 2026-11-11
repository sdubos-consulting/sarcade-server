# SPDX-License-Identifier: AGPL-3.0-only
# © SDUBOS Consulting et contributeurs SARCADE
"""Reply to a message (31 Oct 2026)."""
from datetime import UTC, datetime

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
                               ServerSettingRow, SharedFileRow, SyncOperationRow)


@compiles(BigInteger, "sqlite")
def _bigint_sqlite(type_, compiler, **kw):  # autoincrement keys on SQLite
    return "INTEGER"


@pytest.fixture
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine, tables=[t.__table__ for t in (
        EventRow, LogbookRow, MessageRow, AckRow, SyncOperationRow, SharedFileRow, ServerSettingRow,
        SecurityEventRow, CommGroupRow)])
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


def _send(client, mid, sender="TEL-01", when=None, attachments=None, reply_to=None):
    when = when or datetime.now(UTC)
    p = {"id": mid, "event_id": "e1", "sender_id": sender, "recipient_ids": [], "priority": "routine",
         "body": "Véhicule repéré", "created_at": _iso(when), "attachments": attachments or []}
    if reply_to is not None:
        p["reply_to"] = reply_to
    op = {"operation_id": f"op-{mid}", "event_id": "e1", "object_id": mid, "object_type": "message",
          "action": "create", "client_time": _iso(when), "payload": p}
    return client.post("/api/v0.1/sync", json=[op]).json()[0]




def test_reply_keeps_the_quoted_message(client, db):
    assert _send(client, "m1")["status"] == "accepted"
    assert _send(client, "m2", sender="TEL-02", reply_to="m1")["status"] == "accepted"
    listed = {m["id"]: m for m in client.get("/api/v0.1/events/e1/messages").json()}
    assert listed["m2"]["reply_to"] == "m1"
    assert "reply_to" not in listed["m1"]
    assert db.get(MessageRow, "m2").reply_to == "m1"


def test_unknown_or_foreign_reply_is_dropped_not_refused(client, db):
    db.add(EventRow(id="e2", name="Autre", kind="exercise", status="active",
                    created_at=datetime(2026, 10, 29, tzinfo=UTC), version=0))
    db.add(MessageRow(id="x1", event_id="e2", sender_id="T", recipient_ids=[], priority="routine",
                      body="ailleurs", created_at=datetime.now(UTC)))
    db.commit()
    assert _send(client, "m3", reply_to="nope")["status"] == "accepted"
    assert _send(client, "m4", reply_to="x1")["status"] == "accepted"
    assert _send(client, "m5", reply_to="  ")["status"] == "accepted"
    for mid in ("m3", "m4", "m5"):
        assert db.get(MessageRow, mid).reply_to is None
