# SPDX-License-Identifier: AGPL-3.0-only
# © SDUBOS Consulting et contributeurs SARCADE
"""Changing a sent message within 15 min (lot 29, group 7)."""
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
                               ServerSettingRow, SharedFileRow, SyncOperationRow)
from sarcade.messages import deletion


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


def _send(client, mid, sender="TEL-01", when=None, attachments=None):
    when = when or datetime.now(UTC)
    p = {"id": mid, "event_id": "e1", "sender_id": sender, "recipient_ids": [], "priority": "routine",
         "body": "Véhicule repéré", "created_at": _iso(when), "attachments": attachments or []}
    op = {"operation_id": f"op-{mid}", "event_id": "e1", "object_id": mid, "object_type": "message",
          "action": "create", "client_time": _iso(when), "payload": p}
    return client.post("/api/v0.1/sync", json=[op]).json()[0]


def _edit(client, mid, actor, body, n=1, when=None):
    when = when or datetime.now(UTC)
    op = {"operation_id": f"edit-{mid}-{n}", "event_id": "e1", "object_id": mid, "object_type": "message",
          "action": "update", "client_time": _iso(when), "payload": {"edited_by": actor, "body": body}}
    return client.post("/api/v0.1/sync", json=[op]).json()[0]


def test_author_changes_within_15_min_and_versions_are_kept(client, db):
    assert _send(client, "m1")["status"] == "accepted"
    r = _edit(client, "m1", "TEL-01", "Véhicule repéré au carrefour")
    assert r["status"] == "accepted"
    msg = client.get("/api/v0.1/events/e1/messages").json()[0]
    assert msg["body"] == "Véhicule repéré au carrefour" and msg["edited"] is True
    row = db.get(MessageRow, "m1")
    assert [v["body"] for v in row.versions] == ["Véhicule repéré"]
    kinds = [x.kind for x in db.query(LogbookRow).order_by(LogbookRow.seq)]
    assert kinds == ["message", "message_edited"], "the Main courante keeps both versions"


def test_refused_to_others_too_late_and_on_distress(client, db):
    _send(client, "m2")
    assert _edit(client, "m2", "PCO", "autre")["status"] == "rejected"
    _send(client, "m3", when=datetime.now(UTC) - timedelta(minutes=20))
    assert _edit(client, "m3", "TEL-01", "trop tard")["status"] == "rejected"
    p = {"id": "m4", "event_id": "e1", "sender_id": "TEL-01", "recipient_ids": [], "priority": "distress",
         "body": "🆘 SOS", "created_at": _iso(datetime.now(UTC)), "attachments": []}
    client.post("/api/v0.1/sync", json=[{"operation_id": "op-m4", "event_id": "e1", "object_id": "m4", "object_type": "message",
                                         "action": "create", "client_time": _iso(datetime.now(UTC)), "payload": p}])
    assert _edit(client, "m4", "TEL-01", "pas de SOS")["status"] == "rejected"
