# SPDX-License-Identifier: AGPL-3.0-only
# © SDUBOS Consulting et contributeurs SARCADE
"""Section « SIEM » of the administrator Diagnostic (lot 29, group 8)."""
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import BigInteger, create_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from sarcade import roles
from sarcade.api import diagnostics as diag_api
from sarcade.api.app import app
from sarcade.api.deps import get_db
from sarcade.db.base import Base
from sarcade.db.models import SecurityEventRow, ServerSettingRow, ServerUpdateRow, WeatherCacheRow
from sarcade.security import journal, siem


@compiles(BigInteger, "sqlite")
def _bigint_sqlite(type_, compiler, **kw):  # autoincrement keys on SQLite
    return "INTEGER"


@pytest.fixture
def db(monkeypatch):
    for k in ("SARCADE_SIEM_HOST", "SARCADE_SIEM_FILE", "SARCADE_SIEM_FORMAT", "SARCADE_ADMIN_TOKEN"):
        monkeypatch.delenv(k, raising=False)
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine, tables=[t.__table__ for t in (ServerSettingRow, ServerUpdateRow, WeatherCacheRow, SecurityEventRow)])
    s = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)()
    siem.state.update(connected=False, last_error=None, last_error_at=None, sent=0)
    yield s
    s.close()


def test_not_configured(db):
    st = siem.status(db)
    assert st["state"] == "off" and st["tone"] is None and st["host"] is None


def test_waiting_entries_turn_orange_then_red(db, monkeypatch):
    monkeypatch.setenv("SARCADE_SIEM_HOST", "logs.example.org")
    monkeypatch.setattr(siem.licensing, "pro_enabled", lambda m: True)
    now = datetime(2026, 11, 5, 22, tzinfo=UTC)
    journal.record(db, "admin", "x", "info", actor="a", now=now - timedelta(minutes=5)); db.commit()
    siem.state.update(connected=True)
    st = siem.status(db, now)
    assert st["state"] == "connected" and st["waiting"] == 1 and st["tone"] == "green"
    assert siem.status(db, now + timedelta(minutes=20))["tone"] == "orange"
    assert siem.status(db, now + timedelta(hours=25))["tone"] == "red"
    siem.state.update(connected=False, last_error="ConnectionRefusedError")
    assert siem.status(db, now)["state"] == "unreachable"


def test_without_licence(db, monkeypatch):
    monkeypatch.setenv("SARCADE_SIEM_HOST", "logs.example.org")
    monkeypatch.setattr(siem.licensing, "pro_enabled", lambda m: False)
    assert siem.status(db)["state"] == "unlicensed"


def test_admin_sees_the_section_and_sends_a_test_event(db, monkeypatch):
    from sarcade.api import authz
    app.dependency_overrides[get_db] = lambda: db
    monkeypatch.setattr(diag_api, "level_of", lambda request, d: ("admin", "admin@x.fr"))
    monkeypatch.setattr(diag_api.diag, "resources", lambda path, sample_s=0.2: {"cpu_pct": 1.0, "memory": None, "disk": None, "alert": False})

    async def routing_ok():
        return True
    monkeypatch.setattr(diag_api, "_routing_ok", routing_ok)
    monkeypatch.setattr(roles, "roles_of", lambda d, e: ["admin"] if e == "admin@x.fr" else [])
    monkeypatch.setattr(authz, "caller_of", lambda request: SimpleNamespace(email="admin@x.fr"))
    try:
        c = TestClient(app)
        assert c.get("/api/v0.1/diagnostics/server").json()["siem"]["state"] == "off"
        r = c.post("/api/v0.1/admin/siem/test")
        assert r.status_code == 200 and r.json()["status"] == "queued"
        assert r.json()["siem"]["waiting"] == 1
    finally:
        app.dependency_overrides.pop(get_db, None)
