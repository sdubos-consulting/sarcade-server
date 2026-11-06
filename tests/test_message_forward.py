# SPDX-License-Identifier: AGPL-3.0-only
# © SDUBOS Consulting et contributeurs SARCADE
"""Forwarded message (lot 29, group 7)."""
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


def _send(client, mid, sender="TEL-01", when=None, attachments=None, reply_to=None, forwarded_from=None, body="Véhicule repéré"):
    when = when or datetime.now(UTC)
    p = {"id": mid, "event_id": "e1", "sender_id": sender, "recipient_ids": [], "priority": "routine",
         "body": body, "created_at": _iso(when), "attachments": attachments or []}
    if reply_to is not None:
        p["reply_to"] = reply_to
    if forwarded_from is not None:
        p["forwarded_from"] = forwarded_from
    op = {"operation_id": f"op-{mid}", "event_id": "e1", "object_id": mid, "object_type": "message",
          "action": "create", "client_time": _iso(when), "payload": p}
    return client.post("/api/v0.1/sync", json=[op]).json()[0]


def test_forward_keeps_author_and_time_of_the_original(client, db):
    t = datetime(2026, 11, 5, 21, 30, tzinfo=UTC)
    assert _send(client, "m1", when=t, body="Balise entendue")["status"] == "accepted"
    # The device lies about the origin: the server reads it from the original.
    r = _send(client, "m2", sender="TEL-02", body="Balise entendue",
              forwarded_from={"message_id": "m1", "sender_id": "X", "created_at": "2020-01-01T00:00:00Z"})
    assert r["status"] == "accepted"
    listed = {m["id"]: m for m in client.get("/api/v0.1/events/e1/messages").json()}
    assert listed["m2"]["forwarded_from"] == {"message_id": "m1", "sender_id": "TEL-01", "created_at": "2026-11-05T21:30:00Z"}
    assert "forwarded_from" not in listed["m1"]
    entry = db.query(LogbookRow).filter_by(object_id="m2").one()
    assert entry.summary.startswith("Transféré (de TEL-01, 2026-11-05T21:30:00Z)")


def test_forward_of_an_unknown_message_is_an_ordinary_message(client, db):
    assert _send(client, "m3", forwarded_from={"message_id": "nope"})["status"] == "accepted"
    assert db.get(MessageRow, "m3").forwarded_from is None
    assert _send(client, "m4", forwarded_from="m1")["status"] == "accepted"


def test_forward_of_a_deleted_message_is_refused(client, db):
    _send(client, "m1")
    db.get(MessageRow, "m1").deleted_at = datetime.now(UTC)
    db.commit()
    assert _send(client, "m2", forwarded_from={"message_id": "m1"})["status"] == "rejected"
