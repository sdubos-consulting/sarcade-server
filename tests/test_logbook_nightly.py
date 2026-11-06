# SPDX-License-Identifier: AGPL-3.0-only
# © SDUBOS Consulting et contributeurs SARCADE
"""Nightly check of the Main courante and fingerprint of the day (lot 29, group 8)."""
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from sqlalchemy import BigInteger, create_engine, select, update
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from sarcade.db.base import Base
from sarcade.db.models import EventRow, LogbookRow, SecurityEventRow, ServerSettingRow
from sarcade.logbook import chain, nightly


@compiles(BigInteger, "sqlite")
def _bigint_sqlite(type_, compiler, **kw):  # autoincrement keys on SQLite
    return "INTEGER"


@pytest.fixture
def db(monkeypatch):
    chain.install()
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine, tables=[EventRow.__table__, LogbookRow.__table__, SecurityEventRow.__table__,
                                             ServerSettingRow.__table__])
    session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)()
    for e in ("e1", "e2"):
        session.add(EventRow(id=e, name=e, kind="exercise", status="active", created_at=datetime(2026, 11, 5, tzinfo=UTC), version=0))
    session.commit()
    for i, (e, text) in enumerate([("e1", "SOS équipe 2"), ("e1", "Fin de détresse"), ("e2", "RAS")]):
        session.add(LogbookRow(event_id=e, kind="note", actor_id="PCO", summary=text, time=datetime(2026, 11, 5, 20, i, tzinfo=UTC)))
        session.flush()
    session.commit()
    monkeypatch.setattr("sarcade.deadlines.admin_emails", lambda db: ["admin@x.fr"])
    yield session
    session.close()


NOW = datetime(2026, 11, 6, 3, 30, tzinfo=UTC)


def _journal(db, action):
    return db.scalars(select(SecurityEventRow).where(SecurityEventRow.action == action).order_by(SecurityEventRow.seq)).all()


def test_due_once_a_day_after_3_h():
    assert not nightly.due(None, datetime(2026, 11, 6, 2, 59, tzinfo=UTC))
    assert nightly.due(None, NOW)
    assert nightly.due("2026-11-05", NOW)
    assert not nightly.due("2026-11-06", NOW)


def test_fingerprint_of_the_day_in_the_security_journal(db, monkeypatch):
    monkeypatch.setattr("sarcade.accounts.mailer.config", lambda: None)
    out = nightly.run(db, NOW)
    db.commit()
    assert [(r["event_id"], r["ok"], r["entries"]) for r in out] == [("e1", True, 2), ("e2", True, 1)]
    rows = _journal(db, "logbook_daily_hash")
    assert [r.event_id for r in rows] == ["e1", "e2"]
    last = db.scalars(select(LogbookRow).where(LogbookRow.event_id == "e1").order_by(LogbookRow.seq.desc())).first()
    assert rows[0].details["last_hash"] == last.hash and rows[0].details["intact"] is True
    assert _journal(db, "logbook_chain_broken") == []


def test_a_change_is_reported_and_the_administrators_get_an_email(db, monkeypatch):
    sent = []
    monkeypatch.setattr("sarcade.accounts.mailer.config", lambda: SimpleNamespace(sender="SARCADE <noreply@x.fr>"))
    monkeypatch.setattr("sarcade.accounts.mailer.record", lambda *a, **k: None)
    db.execute(update(LogbookRow).where(LogbookRow.summary == "Fin de détresse").values(summary="Rien"))
    db.commit()
    out = nightly.run(db, NOW, sender=lambda cfg, msg: sent.append(msg))
    db.commit()
    broken = [r for r in out if not r["ok"]]
    assert [r["event_id"] for r in broken] == ["e1"] and broken[0]["reason"] == "content"
    assert len(_journal(db, "logbook_chain_broken")) == 1
    assert len(sent) == 1 and sent[0]["To"] == "admin@x.fr" and "e1" in sent[0]["Subject"]
