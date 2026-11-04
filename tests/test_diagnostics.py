"""Server block of « Paramètres › Diagnostic » (lot 5, 30 Oct 2026)."""
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import BigInteger, create_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from sarcade import diagnostics as diag
from sarcade.api import roles as roles_api
from sarcade.api.app import app
from sarcade.api.deps import get_db
from sarcade.db.base import Base
from sarcade.db.models import ServerSettingRow, ServerUpdateRow, WeatherCacheRow


@compiles(BigInteger, "sqlite")
def _bigint_sqlite(type_, compiler, **kw):
    return "INTEGER"


STAT_A = "cpu  100 0 100 800 0 0 0 0 0 0\ncpu0 1 2 3 4\n"
STAT_B = "cpu  150 0 150 900 0 0 0 0 0 0\n"
MEMINFO = "MemTotal:       8000000 kB\nMemFree:         500000 kB\nMemAvailable:   1000000 kB\n"


def test_cpu_memory_parsing():
    a, b = diag.cpu_times(STAT_A), diag.cpu_times(STAT_B)
    assert a == (200, 1000) and b == (300, 1200)
    assert diag.cpu_pct(a, b) == 50.0 and diag.cpu_pct(a, a) is None and diag.cpu_times("") is None
    m = diag.memory(MEMINFO)
    assert m["pct"] == 87.5 and m["total_mb"] == 7812
    assert diag.memory("") is None


def test_resources_alert(monkeypatch, tmp_path):
    (tmp_path / "stat").write_text(STAT_A)
    (tmp_path / "meminfo").write_text(MEMINFO)
    (tmp_path / "loadavg").write_text("0.50 0.40 0.30 1/200 999\n")
    monkeypatch.setattr(diag, "PROC", tmp_path)
    r = diag.resources(str(tmp_path), sample_s=0)
    assert r["memory"]["pct"] == 87.5 and r["alert"] is True and r["load"] == [0.5, 0.4, 0.3]
    assert r["disk"]["total_gb"] > 0


def test_overall():
    assert diag.overall({"database": "green", "aprs": "off"}) == "ok"
    assert diag.overall({"database": "green", "weather": "orange"}) == "degraded"
    assert diag.overall({"database": "red"}) == "down"


@pytest.fixture
def client(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine, tables=[t.__table__ for t in (ServerSettingRow, ServerUpdateRow, WeatherCacheRow)])
    session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)()
    now = datetime.now(UTC)
    session.add(ServerUpdateRow(at=now - timedelta(days=1), from_version="0.1.0", version="0.1.1", status="succeeded",
                                detail="installed, previous 0.1.0, backup /var/backups/sarcade/x.sql.gz"))
    session.add(WeatherCacheRow(key="forecast:e1", data={}, fetched_at=now - timedelta(minutes=10)))
    session.commit()
    app.dependency_overrides[get_db] = lambda: session
    monkeypatch.setattr(diag, "resources", lambda path, sample_s=0.2: {"cpu_pct": 12.0, "memory": None, "disk": None,
                                                                        "alert": False})
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()
    session.close()


def test_route(client):
    body = client.get("/api/v0.1/diagnostics/server").json()
    assert body["components"]["database"] == "green" and body["components"]["weather"] == "green"
    assert body["status"] in ("ok", "degraded") and body["uptime_s"] >= 0 and body["version"]
    assert body["last_update"]["version"] == "0.1.1"
    assert body["last_backup"]["file"] == "/var/backups/sarcade/x.sql.gz"
    assert body["resources"]["cpu_pct"] == 12.0 and body["alert_pct"] == 80.0


def test_route_without_account_keeps_the_view_of_before(client):
    # No directory (transition): the device without account sees what it saw.
    body = client.get("/api/v0.1/diagnostics/server").json()
    assert body["level"] == "transition" and "resources" in body and "mail" not in body


def test_client_route(client):
    body = client.get("/api/v0.1/diagnostics/client", headers={"cf-connecting-ip": "203.0.113.7", "cf-ray": "x"}).json()
    assert body["ip"] == "203.0.113.7" and body["via_cloudflare"] is True and body["email"] is None
    body = client.get("/api/v0.1/diagnostics/client", headers={"x-forwarded-for": "198.51.100.2, 10.0.0.1"}).json()
    assert body["ip"] == "198.51.100.2" and body["via_cloudflare"] is False
