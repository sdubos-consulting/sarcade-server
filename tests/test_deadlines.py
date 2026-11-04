# SPDX-License-Identifier: AGPL-3.0-only
# © SDUBOS Consulting et contributeurs SARCADE
"""« Échéances » of the Diagnostic of the administrator (lot 28)."""
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import BigInteger, create_engine, select
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from sarcade import deadlines, roles
from sarcade.accounts import mailer
from sarcade.api.app import app
from sarcade.api.deps import get_db
from sarcade.db.base import Base
from sarcade.db.models import SecurityEventRow, ServerSettingRow


@compiles(BigInteger, "sqlite")
def _bigint_sqlite(type_, compiler, **kw):
    return "INTEGER"


NOW = datetime(2027, 10, 4, 9, 0, tzinfo=UTC)
SMTP_ENV = ("SARCADE_SMTP_HOST", "SARCADE_SMTP_PORT", "SARCADE_SMTP_USER", "SARCADE_SMTP_PASSWORD", "SARCADE_SMTP_FROM")


@pytest.fixture
def db(monkeypatch):
    for k in SMTP_ENV + ("SARCADE_ADMIN_TOKEN", "SARCADE_ADMIN_EMAILS", "SARCADE_PUBLIC_URL"):
        monkeypatch.delenv(k, raising=False)
    from sarcade import licensing
    monkeypatch.setattr(licensing, "state", lambda: SimpleNamespace(status="none", licence=None))
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine, tables=[ServerSettingRow.__table__, SecurityEventRow.__table__])
    session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)()
    yield session
    session.close()


@pytest.fixture
def smtp(monkeypatch):
    monkeypatch.setenv("SARCADE_SMTP_HOST", "smtp-relay.brevo.com")
    monkeypatch.setenv("SARCADE_SMTP_USER", "login")
    monkeypatch.setenv("SARCADE_SMTP_PASSWORD", "cle-smtp")
    monkeypatch.setenv("SARCADE_SMTP_FROM", "SARCADE <no-reply@sarcade.org>")
    monkeypatch.setenv("SARCADE_ADMIN_EMAILS", "admin@x.fr")
    sent = []
    return sent


def test_colours():
    assert [deadlines.level(d) for d in (None, -1, 0, 7, 8, 30, 31)] == \
        ["unknown", "expired", "red", "red", "orange", "orange", "green"]


def test_prefilled_list_with_the_dates_of_the_card(db):
    v = deadlines.view(db, NOW)
    by = {r["id"]: r for r in v["items"]}
    assert by["brevo-smtp"]["date"] == "2027-11-03" and by["brevo-smtp"]["days_left"] == 30
    assert by["brevo-smtp"]["level"] == "orange" and by["brevo-smtp"]["mail_related"] is True
    assert by["apple-developer"]["date"] == "2027-10-22" and by["apple-developer"]["level"] == "orange"
    assert by["domain"]["level"] == "unknown"
    assert v["items"][0]["id"] == "apple-developer"  # most urgent first
    assert v["summary"] == {"worst": "orange", "alerts": 2}


def test_manual_items_checked_never_a_secret_field(db):
    clean = deadlines.save(db, [{"name": "Clé Météo-France", "date": "2028-01-15", "url": "https://portail.example",
                                 "secret": "x", "note": " rappel  "}], "admin@x.fr")
    assert clean == [{"id": "cle-meteo-france", "name": "Clé Météo-France", "date": "2028-01-15",
                      "url": "https://portail.example", "note": "rappel"}]
    for bad, why in (([{"name": ""}]), "invalid_name"), (([{"name": "a", "date": "31/12"}]), "invalid_date"), \
                     (([{"name": "a", "url": "http://x"}]), "invalid_url"), (([{"name": "a"}, {"name": "a"}]), "invalid_id"):
        with pytest.raises(deadlines.InvalidDeadlines, match=why):
            deadlines.validate(bad)
    with pytest.raises(deadlines.InvalidDeadlines):
        deadlines.validate([{"name": f"n{i}"} for i in range(51)])


def test_brevo_ninety_days_without_sending(db, smtp):
    mailer.record(db, True, NOW - timedelta(days=85))
    by = {r["id"]: r for r in deadlines.view(db, NOW)["items"]}
    assert by["mail-inactivity"]["days_left"] == 5 and by["mail-inactivity"]["level"] == "red"
    assert by["mail-inactivity"]["kind"] == "auto"


def test_https_certificate_read_once_a_day(db, monkeypatch):
    monkeypatch.setenv("SARCADE_PUBLIC_URL", "https://adrasec78.sarcade.org")
    deadlines.check(db, NOW, tls_reader=lambda host: NOW + timedelta(days=6))
    by = {r["id"]: r for r in deadlines.view(db, NOW)["items"]}
    assert by["tls"]["name"] == "Certificat HTTPS (adrasec78.sarcade.org)" and by["tls"]["level"] == "red"

    def broken(host):
        raise OSError("refused")
    deadlines.check(db, NOW + timedelta(days=1), tls_reader=broken)
    by = {r["id"]: r for r in deadlines.view(db, NOW)["items"]}
    assert by["tls"]["days_left"] == 6  # last reading kept


def test_licences_detected(db, monkeypatch):
    from sarcade import licensing
    monkeypatch.setattr(licensing, "state", lambda: SimpleNamespace(
        status="valid", licence={"organisation": "ADRASEC 78", "expires_at": "2027-12-31"}))
    by = {r["id"]: r for r in deadlines.view(db, NOW)["items"]}
    assert by["licence-server"]["name"] == "Licence SARCADE (ADRASEC 78)" and by["licence-server"]["level"] == "green"


def test_alerts_at_30_7_and_0_days_once_each(db, smtp):
    sent = []
    deadlines.save(db, [{"id": "apple-developer", "name": "Adhésion Apple Developer", "date": "2027-11-03"},
                        {"id": "brevo-smtp", "name": "Clé SMTP Brevo", "date": "2027-11-03"}], "admin@x.fr")
    send = lambda cfg, msg: sent.append(msg)  # noqa: E731
    given = deadlines.check(db, NOW, sender=send)
    assert {g["id"]: g["threshold"] for g in given} == {"apple-developer": 30, "brevo-smtp": 30}
    # Brevo concerned: only in the application.
    assert [m["To"] for m in sent] == ["admin@x.fr"] and "Apple" in sent[0]["Subject"]
    assert "expire dans 30 jours, le 2027-11-03" in sent[0].get_content()
    assert deadlines.check(db, NOW + timedelta(days=1), sender=send) == []
    assert [g["threshold"] for g in deadlines.check(db, NOW + timedelta(days=24), sender=send)] == [7, 7]
    assert [g["threshold"] for g in deadlines.check(db, NOW + timedelta(days=30), sender=send)] == [0, 0]
    assert deadlines.check(db, NOW + timedelta(days=40), sender=send) == []
    assert len(sent) == 3
    journal = [e for e in db.scalars(select(SecurityEventRow)) if e.action == "deadline_alert"]
    assert len(journal) == 6
    # New date typed: the alerts start again.
    deadlines.save(db, [{"id": "apple-developer", "name": "Adhésion Apple Developer", "date": "2028-11-03"}], "a")
    assert deadlines.check(db, NOW + timedelta(days=40), sender=send) == []
    again = deadlines.check(db, datetime(2028, 10, 30, tzinfo=UTC), sender=send)
    assert [g["threshold"] for g in again if g["id"] == "apple-developer"] == [7]


def test_without_smtp_alerts_stay_in_the_application(db):
    deadlines.save(db, [{"name": "Domaine sarcade.org", "date": "2027-10-05"}], "a")
    given = deadlines.check(db, NOW, sender=lambda c, m: pytest.fail("no sending"))
    assert given[0]["threshold"] == 7 and given[0]["emailed"] == 0


def test_due_threshold():
    assert deadlines.due_threshold(45, []) is None
    assert deadlines.due_threshold(20, []) == 30
    assert deadlines.due_threshold(20, [30]) is None
    assert deadlines.due_threshold(-3, [30, 7]) == 0
    assert deadlines.due_threshold(None, []) is None


# --- API -------------------------------------------------------------------------

@pytest.fixture
def client(db):
    app.dependency_overrides[get_db] = lambda: db
    yield TestClient(app)
    app.dependency_overrides.pop(get_db, None)


def test_api_read_and_write_for_the_administrator(client, db, monkeypatch):
    monkeypatch.setenv("SARCADE_ADMIN_TOKEN", "t0k")
    h = {"Authorization": "Bearer t0k"}
    body = client.get("/api/v0.1/admin/deadlines", headers=h).json()
    assert body["summary"]["worst"] in ("green", "orange", "red", "expired") and len(body["manual"]) == 7
    r = client.put("/api/v0.1/admin/deadlines", headers=h, json={"items": [{"name": "Domaine sarcade.org", "date": "2030-01-01"}]})
    assert r.status_code == 200 and [m["name"] for m in r.json()["manual"]] == ["Domaine sarcade.org"]
    r = client.put("/api/v0.1/admin/deadlines", headers=h, json={"items": [{"name": "x", "url": "ftp://y"}]})
    assert r.status_code == 422 and r.json()["detail"] == "invalid_url"
    assert client.get("/api/v0.1/admin/deadlines").status_code == 401
    assert client.put("/api/v0.1/admin/deadlines", json={"items": []}).status_code in (401, 403)


def test_me_gives_the_summary_to_the_administrators(client, monkeypatch):
    monkeypatch.setattr(roles, "roles_of", lambda db, e: ["admin", "pco"] if e == "admin@x.fr" else [])
    from sarcade.api import roles as roles_api
    who = {"email": "admin@x.fr"}
    monkeypatch.setattr(roles_api, "caller_of", lambda request: SimpleNamespace(
        email=who["email"], via="device", user_id=None, auth_device_id=None))
    assert client.get("/api/v0.1/me").json()["deadlines"]["worst"] in ("green", "orange", "red", "expired")
    who["email"] = "a@x.fr"
    assert "deadlines" not in client.get("/api/v0.1/me").json()
