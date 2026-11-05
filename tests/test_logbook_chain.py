# SPDX-License-Identifier: AGPL-3.0-only
# © SDUBOS Consulting et contributeurs SARCADE
"""Tamper-evident Main courante (lot 29, group 8)."""
from datetime import UTC, datetime

import pytest
from sqlalchemy import BigInteger, create_engine, update
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from sarcade.db.base import Base
from sarcade.db.models import EventRow, LogbookRow
from sarcade.logbook import chain


@compiles(BigInteger, "sqlite")
def _bigint_sqlite(type_, compiler, **kw):  # autoincrement keys on SQLite
    return "INTEGER"


@pytest.fixture
def db():
    chain.install()
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine, tables=[EventRow.__table__, LogbookRow.__table__])
    session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)()
    for e in ("e1", "e2"):
        session.add(EventRow(id=e, name=e, kind="exercise", status="active", created_at=datetime(2026, 11, 5, tzinfo=UTC), version=0))
    session.commit()
    yield session
    session.close()


def _add(db, event, summary, n=0):
    db.add(LogbookRow(event_id=event, kind="note", actor_id="PCO", summary=summary, time=datetime(2026, 11, 5, 20, n, tzinfo=UTC)))


def test_entries_are_chained_per_event(db):
    _add(db, "e1", "un", 0); _add(db, "e2", "autre", 1); _add(db, "e1", "deux", 2)
    db.commit()
    _add(db, "e1", "trois", 3)
    db.commit()
    rows = db.query(LogbookRow).filter_by(event_id="e1").order_by(LogbookRow.seq).all()
    assert rows[0].prev_hash == chain.GENESIS
    assert [r.prev_hash for r in rows[1:]] == [r.hash for r in rows[:-1]]
    assert chain.verify(db, "e1") == {"ok": True, "chained": 3, "before_chaining": 0, "broken_at": None, "reason": None}
    assert chain.verify(db, "e2")["chained"] == 1


def test_a_change_in_the_database_is_found(db):
    for i in range(3):
        _add(db, "e1", f"n{i}", i)
    db.commit()
    second = db.query(LogbookRow).filter_by(event_id="e1").order_by(LogbookRow.seq).all()[1]
    db.execute(update(LogbookRow).where(LogbookRow.seq == second.seq).values(summary="falsifié"))
    db.commit()
    r = chain.verify(db, "e1")
    assert r["ok"] is False and r["broken_at"] == second.seq and r["reason"] == "content"


def test_a_deletion_is_found_and_old_entries_are_before_chaining(db):
    db.execute(LogbookRow.__table__.insert().values(event_id="e1", kind="note", summary="ancienne",
                                                    time=datetime(2026, 11, 1, tzinfo=UTC)))
    db.commit()
    for i in range(3):
        _add(db, "e1", f"n{i}", i)
    db.commit()
    assert chain.verify(db, "e1")["before_chaining"] == 1
    middle = db.query(LogbookRow).filter_by(event_id="e1").order_by(LogbookRow.seq).all()[2]
    db.delete(middle)
    db.commit()
    r = chain.verify(db, "e1")
    assert r["ok"] is False and r["reason"] == "link"
