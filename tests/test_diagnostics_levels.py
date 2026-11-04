# SPDX-License-Identifier: AGPL-3.0-only
# © SDUBOS Consulting et contributeurs SARCADE
"""Diagnostic in three levels, test of Brevo, /version (lot 28)."""
import hashlib
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import BigInteger, create_engine, select
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from sarcade import diagnostics as diag, roles, version
from sarcade.accounts import mailer
from sarcade.api import diagnostics as diag_api
from sarcade.api.app import app
from sarcade.api.deps import get_db
from sarcade.db.base import Base
from sarcade.db.models import SecurityEventRow, ServerSettingRow, ServerUpdateRow, WeatherCacheRow


@compiles(BigInteger, "sqlite")
def _bigint_sqlite(type_, compiler, **kw):
    return "INTEGER"


SMTP_ENV = ("SARCADE_SMTP_HOST", "SARCADE_SMTP_PORT", "SARCADE_SMTP_USER", "SARCADE_SMTP_PASSWORD", "SARCADE_SMTP_FROM")


@pytest.fixture
def db(monkeypatch):
    for k in SMTP_ENV + ("SARCADE_ADMIN_TOKEN", "SARCADE_ADMIN_EMAILS", "SARCADE_COMMIT", "SARCADE_TAG"):
        monkeypatch.delenv(k, raising=False)
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine, tables=[t.__table__ for t in (
        ServerSettingRow, ServerUpdateRow, WeatherCacheRow, SecurityEventRow)])
    session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)()
    yield session
    session.close()


@pytest.fixture
def client(db, monkeypatch):
    app.dependency_overrides[get_db] = lambda: db
    monkeypatch.setattr(diag, "resources", lambda path, sample_s=0.2: {"cpu_pct": 1.0, "memory": None, "disk": None,
                                                                        "alert": False})

    async def routing_ok():
        return True
    monkeypatch.setattr(diag_api, "_routing_ok", routing_ok)
    diag_api._probe.update(key=None)
    yield TestClient(app)
    app.dependency_overrides.pop(get_db, None)


def as_level(monkeypatch, level, email=None):
    monkeypatch.setattr(diag_api, "level_of", lambda request, db: (level, email))


def test_user_sees_only_the_state_and_the_version(client, monkeypatch):
    as_level(monkeypatch, "user", "a@x.fr")
    body = client.get("/api/v0.1/diagnostics/server").json()
    assert set(body) == {"level", "status", "version", "reference", "at"} and body["level"] == "user"


def test_organizer_sees_the_services_and_the_devices(client, monkeypatch):
    as_level(monkeypatch, "organizer", "pco@x.fr")
    body = client.get("/api/v0.1/diagnostics/server").json()
    assert body["level"] == "organizer" and body["devices_connected"] == 0
    assert set(body["services"]) == {"aprs", "weather", "wakeup", "routes"} and body["services"]["routes"] == "green"
    assert "resources" not in body and "mail" not in body and "components" not in body


def test_admin_sees_everything_with_brevo(client, monkeypatch):
    as_level(monkeypatch, "admin", "admin@x.fr")
    body = client.get("/api/v0.1/diagnostics/server").json()
    assert body["level"] == "admin" and "resources" in body and "addresses" in body
    assert body["build"]["service"] == "sarcade-server"
    assert body["mail"]["delivery"] == "simulated" and body["mail"]["probe"] is None


# --- Level computed from the rights -------------------------------------------------

def _request(host="nas.local", email=None, user_id=None):
    from sarcade.accounts.caller import Caller
    return SimpleNamespace(headers={"host": host}, state=SimpleNamespace(caller=Caller(email=email, user_id=user_id,
                                                                                         via="device" if email else None)))


def test_level_from_the_rights(db, monkeypatch):
    monkeypatch.setattr(roles, "directory_ready", lambda db: False)
    assert diag_api.level_of(_request(), db) == ("transition", None)
    monkeypatch.setattr(roles, "roles_of", lambda db, e: {"admin@x.fr": ["admin", "pco"], "pco@x.fr": ["pco"]}.get(e, []))
    assert diag_api.level_of(_request(email="admin@x.fr"), db)[0] == "admin"
    assert diag_api.level_of(_request(email="pco@x.fr"), db)[0] == "organizer"
    assert diag_api.level_of(_request(email="a@x.fr"), db)[0] == "user"


def test_without_account_after_the_transition_the_view_is_the_user_one(db, monkeypatch):
    from sarcade.directory import transition
    monkeypatch.setattr(roles, "directory_ready", lambda db: True)
    monkeypatch.setattr(transition, "active", lambda *a, **k: False)
    assert diag_api.level_of(_request(), db) == ("user", None)
    monkeypatch.setattr(transition, "active", lambda *a, **k: True)
    assert diag_api.level_of(_request(), db) == ("transition", None)


def test_auditor_of_the_server_sees_everything(db, monkeypatch):
    from sarcade.directory import rights
    monkeypatch.setattr(roles, "directory_ready", lambda db: True)
    monkeypatch.setattr(roles, "roles_of", lambda db, e: [])
    grants = [SimpleNamespace(right="auditor", scope_group_id=None)]
    monkeypatch.setattr(rights, "of", lambda db, uid, email: SimpleNamespace(grants=grants))
    assert diag_api.level_of(_request(email="audit@x.fr", user_id="u1"), db)[0] == "admin"
    grants[0] = SimpleNamespace(right="auditor", scope_group_id="g1")
    assert diag_api.level_of(_request(email="audit@x.fr", user_id="u1"), db)[0] == "user"


def test_admin_token_is_admin(db, monkeypatch):
    monkeypatch.setenv("SARCADE_ADMIN_TOKEN", "t0k")
    req = SimpleNamespace(headers={"authorization": "Bearer t0k", "host": "x"}, state=SimpleNamespace())
    assert diag_api.level_of(req, db) == ("admin", None)


# --- Brevo ---------------------------------------------------------------------------

class FakeSmtp:
    sent: list = []
    logins = 0
    fail = False

    def __init__(self, host, port, timeout=None, **kw):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def starttls(self, context=None):
        pass

    def login(self, user, password):
        FakeSmtp.logins += 1
        if FakeSmtp.fail:
            import smtplib
            raise smtplib.SMTPAuthenticationError(535, b"5.7.8 Authentication failed")

    def send_message(self, msg):
        FakeSmtp.sent.append(msg)


@pytest.fixture
def smtp(monkeypatch):
    monkeypatch.setenv("SARCADE_SMTP_HOST", "smtp-relay.brevo.com")
    monkeypatch.setenv("SARCADE_SMTP_USER", "login")
    monkeypatch.setenv("SARCADE_SMTP_PASSWORD", "cle-smtp")
    monkeypatch.setenv("SARCADE_SMTP_FROM", "SARCADE <no-reply@sarcade.org>")
    monkeypatch.setattr(mailer.smtplib, "SMTP", FakeSmtp)
    FakeSmtp.sent, FakeSmtp.logins, FakeSmtp.fail = [], 0, False
    return FakeSmtp


def test_connection_test_without_sending_kept_one_minute(client, monkeypatch, smtp):
    as_level(monkeypatch, "admin", "admin@x.fr")
    mail = client.get("/api/v0.1/diagnostics/server").json()["mail"]
    assert mail["delivery"] == "smtp" and mail["probe"]["ok"] is True and not smtp.sent
    client.get("/api/v0.1/diagnostics/server")
    assert smtp.logins == 1
    assert "cle-smtp" not in str(mail) and "login" not in str(mail.get("probe"))


def test_connection_test_failure_gives_the_answer_of_the_server(client, monkeypatch, smtp):
    as_level(monkeypatch, "admin", "admin@x.fr")
    smtp.fail = True
    probe = client.get("/api/v0.1/diagnostics/server").json()["mail"]["probe"]
    assert probe["ok"] is False and probe["error"].startswith("SMTPAuthenticationError 535")


def test_test_email_to_the_administrator_and_last_sendings(client, db, monkeypatch, smtp):
    from sarcade.api import authz
    monkeypatch.setattr(roles, "roles_of", lambda db, e: ["admin", "pco"] if e == "admin@x.fr" else [])
    monkeypatch.setattr(authz, "caller_of", lambda request: SimpleNamespace(email="admin@x.fr"))
    r = client.post("/api/v0.1/admin/mail/test")
    assert r.status_code == 200 and r.json()["to"] == "admin@x.fr"
    assert smtp.sent[0]["To"] == "admin@x.fr" and "essai" in smtp.sent[0]["Subject"]
    assert mailer.stats(db)["last_success_at"]
    smtp.fail = True
    r = client.post("/api/v0.1/admin/mail/test")
    assert r.status_code == 503 and r.json()["detail"]["error"] == "mail_failed"
    st = mailer.stats(db)
    assert st["last_failure"].startswith("SMTPAuthenticationError 535") and st["last_success_at"]
    actions = [e.outcome for e in db.scalars(select(SecurityEventRow)) if e.action == "mail_test"]
    assert actions == ["success", "failure"]


def test_test_email_refused_to_others(client, monkeypatch, smtp):
    assert client.post("/api/v0.1/admin/mail/test").status_code in (401, 403)
    monkeypatch.setenv("SARCADE_ADMIN_TOKEN", "t0k")
    r = client.post("/api/v0.1/admin/mail/test", headers={"Authorization": "Bearer t0k"})
    assert r.status_code == 422 and r.json()["detail"] == "admin_email_required"


# --- /version ------------------------------------------------------------------------

def test_version_reference_label_or_main_at_commit(client, monkeypatch, tmp_path):
    assert client.get("/version").json()["reference"] == "dev"
    monkeypatch.setenv("SARCADE_COMMIT", "0123456789abcdef0123456789abcdef01234567")
    body = client.get("/version").json()
    assert body["reference"] == "main@0123456" and body["commit_short"] == "0123456" and body["tag"] is None
    monkeypatch.setenv("SARCADE_TAG", "v0.1.0-exercice1")
    assert client.get("/api/v0.1/version").json()["reference"] == "v0.1.0-exercice1"
    r = client.get("/version")
    assert r.headers["cache-control"] == "no-store"


def test_version_digests(client, monkeypatch, tmp_path):
    (tmp_path / "sarcade-web.sha256").write_text("sha256:" + "ab" * 32 + "\n", encoding="utf-8")
    sbom = tmp_path / "sbom.json"
    sbom.write_bytes(b'{"bomFormat": "CycloneDX"}')
    monkeypatch.setenv("SARCADE_WEB_ROOT", str(tmp_path))
    monkeypatch.setenv("SARCADE_SBOM_FILE", str(sbom))
    body = client.get("/version").json()
    assert body["web_sha256"] == "sha256:" + "ab" * 32
    assert body["sbom_sha256"] == "sha256:" + hashlib.sha256(sbom.read_bytes()).hexdigest()
    monkeypatch.setenv("SARCADE_WEB_ROOT", str(tmp_path / "none"))
    monkeypatch.setenv("SARCADE_SBOM_FILE", str(tmp_path / "none.json"))
    body = client.get("/version").json()
    assert body["web_sha256"] is None and body["sbom_sha256"] is None


def test_reference_rule():
    assert version.reference("v1", "abc") == "v1"
    assert version.reference(None, "abcdef0123") == "main@abcdef0"
    assert version.reference(None, None) == "dev"


def test_mail_failure_reason_never_carries_secrets():
    import smtplib
    exc = smtplib.SMTPAuthenticationError(535, b"5.7.8   Authentication\n failed")
    assert mailer.reason(exc) == "SMTPAuthenticationError 535 5.7.8 Authentication failed"
    assert mailer.reason(OSError("x")) == "OSError"
