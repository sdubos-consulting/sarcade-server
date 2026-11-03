# SPDX-License-Identifier: AGPL-3.0-only
# © SDUBOS Consulting et contributeurs SARCADE
"""Accounts, connection by a code sent by email, enrolled devices (lot 26)."""
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import BigInteger, create_engine, select
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from sarcade import accounts
from sarcade.accounts import mailer
from sarcade.api.app import app
from sarcade.api.deps import get_db
from sarcade.db.base import Base
from sarcade.db.models import AuthDeviceRow, LoginCodeRow, SecurityEventRow, ServerSettingRow, UserRow


@compiles(BigInteger, "sqlite")
def _bigint_sqlite(type_, compiler, **kw):
    return "INTEGER"


T0 = datetime(2026, 11, 3, 14, 0, tzinfo=UTC)
SMTP_ENV = ("SARCADE_SMTP_HOST", "SARCADE_SMTP_PORT", "SARCADE_SMTP_USER", "SARCADE_SMTP_PASSWORD",
            "SARCADE_SMTP_FROM")


@pytest.fixture
def db(monkeypatch):
    for k in SMTP_ENV + ("SARCADE_ADMIN_TOKEN", "SARCADE_ADMIN_EMAILS"):
        monkeypatch.delenv(k, raising=False)
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine, tables=[t.__table__ for t in (
        UserRow, LoginCodeRow, AuthDeviceRow, SecurityEventRow, ServerSettingRow)])
    session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)()
    yield session
    session.close()


@pytest.fixture
def client(db):
    app.dependency_overrides[get_db] = lambda: db
    yield TestClient(app)
    app.dependency_overrides.pop(get_db, None)


def journal(db, action=None):
    rows = db.scalars(select(SecurityEventRow).order_by(SecurityEventRow.seq)).all()
    return [r for r in rows if action is None or r.action == action]


def simulated_code(db, email):
    rows = [r for r in journal(db, "login_code_simulated") if r.details.get("email") == email]
    return rows[-1].details["code"]


def login(client, db, email="Chef@ADRASEC78.fr", **device):
    r = client.post("/api/v0.1/auth/code/request", json={"email": email})
    assert r.status_code == 202, r.text
    code = simulated_code(db, email.strip().lower())
    r = client.post("/api/v0.1/auth/code/verify", json={"email": email, "code": code, **device})
    assert r.status_code == 200, r.text
    return r.json()


# --- Unit ---------------------------------------------------------------------------

def test_email_is_the_key_lower_case_without_spaces():
    assert accounts.normal_email("  Chef@ADRASEC78.fr ") == "chef@adrasec78.fr"
    for bad in ("pas un mail", "a@b", "a b@c.fr", "", None, "x" * 400 + "@a.fr"):
        with pytest.raises(accounts.AccountError, match="invalid_email"):
            accounts.normal_email(bad)


def test_code_is_six_digits_hashed_and_single_use(db):
    issued = accounts.issue_code(db, "a@x.fr", T0)
    assert len(issued.code) == 6 and issued.code.isdigit()
    assert issued.code not in issued.row.code_hash
    accounts.check_code(db, "a@x.fr", issued.code, T0 + timedelta(minutes=1))
    with pytest.raises(accounts.AccountError, match="no_code"):
        accounts.check_code(db, "a@x.fr", issued.code, T0 + timedelta(minutes=2))


def test_code_expires_after_ten_minutes(db):
    issued = accounts.issue_code(db, "a@x.fr", T0)
    with pytest.raises(accounts.AccountError, match="code_expired"):
        accounts.check_code(db, "a@x.fr", issued.code, T0 + timedelta(minutes=10))


def test_five_attempts_then_a_new_code_is_needed(db):
    issued = accounts.issue_code(db, "a@x.fr", T0)
    wrong = "000000" if issued.code != "000000" else "111111"
    for left in (4, 3, 2, 1):
        with pytest.raises(accounts.AccountError) as exc:
            accounts.check_code(db, "a@x.fr", wrong, T0)
        assert exc.value.detail == "invalid_code" and exc.value.extra["attempts_left"] == left
    with pytest.raises(accounts.AccountError, match="too_many_attempts"):
        accounts.check_code(db, "a@x.fr", wrong, T0)
    with pytest.raises(accounts.AccountError, match="too_many_attempts"):
        accounts.check_code(db, "a@x.fr", issued.code, T0)


def test_resend_delay_and_hourly_limit(db):
    accounts.issue_code(db, "a@x.fr", T0)
    with pytest.raises(accounts.AccountError) as exc:
        accounts.issue_code(db, "a@x.fr", T0 + timedelta(seconds=30))
    assert exc.value.status == 429 and exc.value.detail == "resend_too_soon"
    assert exc.value.extra["retry_after_s"] == 31
    for i in range(1, 5):
        accounts.issue_code(db, "a@x.fr", T0 + timedelta(minutes=i))
    with pytest.raises(accounts.AccountError, match="too_many_codes"):
        accounts.issue_code(db, "a@x.fr", T0 + timedelta(minutes=6))
    accounts.issue_code(db, "a@x.fr", T0 + timedelta(minutes=61))


def test_a_new_code_replaces_the_previous_one(db):
    first = accounts.issue_code(db, "a@x.fr", T0)
    second = accounts.issue_code(db, "a@x.fr", T0 + timedelta(minutes=2))
    assert first.row.used_at is not None and second.row.used_at is None
    accounts.check_code(db, "a@x.fr", second.code, T0 + timedelta(minutes=3))


def test_requests_per_ip_are_limited(db):
    for i in range(accounts.MAX_REQUESTS_PER_IP_PER_HOUR):
        accounts.issue_code(db, f"u{i}@x.fr", T0, "10.0.0.9")
    with pytest.raises(accounts.AccountError, match="too_many_requests"):
        accounts.issue_code(db, "other@x.fr", T0, "10.0.0.9")
    accounts.issue_code(db, "other@x.fr", T0, "10.0.0.10")


def test_device_token_is_stored_hashed(db):
    user, created = accounts.ensure_user(db, "a@x.fr", T0, "test")
    assert created and accounts.ensure_user(db, "A@x.fr", T0, "test") == (user, False)
    row, token = accounts.enroll(db, user, T0, "email_code", device_id="TEL-01")
    assert token.startswith("sdt_") and len(token) > 40
    assert row.token_hash != token and accounts.device_by_token(db, token) is row
    assert accounts.device_by_token(db, "sdt_unknown") is None


def test_disabled_account_is_not_enrolled(db):
    user, _ = accounts.ensure_user(db, "a@x.fr", T0, "test")
    user.status = "disabled"
    with pytest.raises(accounts.AccountError, match="account_disabled"):
        accounts.enroll(db, user, T0, "email_code")


# --- Mail ---------------------------------------------------------------------------

def test_simulated_until_every_smtp_setting_is_given():
    assert mailer.config({}) is None
    full = {"SARCADE_SMTP_HOST": "smtp-relay.brevo.com", "SARCADE_SMTP_USER": "u", "SARCADE_SMTP_PASSWORD": "p",
            "SARCADE_SMTP_FROM": "SARCADE <no-reply@sarcade.org>"}
    for missing in full:
        assert mailer.config({k: v for k, v in full.items() if k != missing}) is None
    cfg = mailer.config(full)
    assert cfg.port == 587 and cfg.sender_address == "no-reply@sarcade.org"
    assert mailer.delivery(full) == "smtp" and mailer.delivery({}) == "simulated"


def test_code_message_in_french():
    cfg = mailer.config({"SARCADE_SMTP_HOST": "h", "SARCADE_SMTP_USER": "u", "SARCADE_SMTP_PASSWORD": "p",
                         "SARCADE_SMTP_FROM": "SARCADE <no-reply@sarcade.org>"})
    msg = mailer.code_message(cfg, "a@x.fr", "123456", 10)
    assert msg["From"] == "SARCADE <no-reply@sarcade.org>" and msg["To"] == "a@x.fr"
    assert "123456" in msg.get_content() and "10 minutes" in msg.get_content()


class FakeSmtp:
    sent: list = []
    fail = False

    def __init__(self, host, port, timeout=None, **kw):
        self.host, self.port = host, port

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def starttls(self, context=None):
        pass

    def login(self, user, password):
        if FakeSmtp.fail:
            import smtplib
            raise smtplib.SMTPAuthenticationError(535, b"bad")

    def send_message(self, msg):
        FakeSmtp.sent.append(msg)


@pytest.fixture
def smtp(monkeypatch):
    monkeypatch.setenv("SARCADE_SMTP_HOST", "smtp-relay.brevo.com")
    monkeypatch.setenv("SARCADE_SMTP_USER", "login")
    monkeypatch.setenv("SARCADE_SMTP_PASSWORD", "cle-smtp")
    monkeypatch.setenv("SARCADE_SMTP_FROM", "SARCADE <no-reply@sarcade.org>")
    monkeypatch.setattr(mailer.smtplib, "SMTP", FakeSmtp)
    FakeSmtp.sent, FakeSmtp.fail = [], False
    return FakeSmtp


def test_code_sent_by_smtp_never_written_in_the_journal(client, db, smtp):
    r = client.post("/api/v0.1/auth/code/request", json={"email": "a@x.fr"})
    assert r.status_code == 202 and r.json()["delivery"] == "smtp"
    assert len(smtp.sent) == 1 and smtp.sent[0]["To"] == "a@x.fr"
    assert not journal(db, "login_code_simulated")
    assert all("code" not in (e.details or {}) for e in journal(db))
    assert journal(db, "login_code_sent")[0].outcome == "success"


def test_smtp_failure_answers_503_and_the_code_is_dropped(client, db, smtp):
    smtp.fail = True
    r = client.post("/api/v0.1/auth/code/request", json={"email": "a@x.fr"})
    assert r.status_code == 503 and r.json()["detail"] == "mail_failed"
    assert db.scalars(select(LoginCodeRow)).one().used_at is not None
    assert journal(db, "login_code_sent")[0].outcome == "failure"


def test_admin_sees_the_mail_state_without_secret(client, db, smtp, monkeypatch):
    monkeypatch.setenv("SARCADE_ADMIN_TOKEN", "t0k")
    r = client.get("/api/v0.1/admin/mail", headers={"Authorization": "Bearer t0k"})
    assert r.json() == {"delivery": "smtp", "host": "smtp-relay.brevo.com", "port": 587,
                        "sender": "no-reply@sarcade.org", "missing": []}
    assert "cle-smtp" not in r.text and "login" not in r.text


# --- API ----------------------------------------------------------------------------

def test_first_connection_creates_the_account_and_enrols_the_device(client, db):
    out = login(client, db, device_id="TEL-01", label="Téléphone de Paul", platform="android")
    assert out["created"] is True and out["account"]["email"] == "chef@adrasec78.fr"
    assert out["device"]["device_id"] == "TEL-01" and out["device"]["method"] == "email_code"
    token = out["token"]
    me = client.get("/api/v0.1/me", headers={"Authorization": f"Bearer {token}"}).json()
    assert me["email"] == "chef@adrasec78.fr" and me["checked"] is True and me["via"] == "device"
    assert me["account"]["id"] == out["account"]["id"] and me["device"]["id"] == out["device"]["id"]
    assert [e.action for e in journal(db)][-2:] == ["account_created", "device_enrolled"]
    # Second connection, another device: same account, two devices.
    for row in db.scalars(select(LoginCodeRow)):
        row.created_at -= timedelta(minutes=2)
    db.commit()
    again = login(client, db, email="chef@adrasec78.fr", device_id="PC-02")
    assert again["created"] is False and again["account"]["id"] == out["account"]["id"]
    assert len(client.get("/api/v0.1/me/devices", headers={"Authorization": f"Bearer {token}"}).json()) == 2


def test_same_answer_whether_the_account_exists_or_not(client, db):
    a = client.post("/api/v0.1/auth/code/request", json={"email": "inconnu@x.fr"}).json()
    login(client, db, email="connu@x.fr")
    b = client.post("/api/v0.1/auth/code/request", json={"email": "autre@x.fr"}).json()
    assert a == b == {"status": "sent", "delivery": "simulated", "expires_in_s": 600, "resend_after_s": 60,
                      "code_length": 6}


def test_simulated_code_is_in_the_security_journal_only(client, db, caplog):
    client.post("/api/v0.1/auth/code/request", json={"email": "a@x.fr"})
    code = simulated_code(db, "a@x.fr")
    assert code not in caplog.text
    e = journal(db, "login_code_simulated")[0]
    assert e.category == "auth" and e.details["email"] == "a@x.fr"


def test_wrong_code_answers_401_with_attempts_left(client, db):
    client.post("/api/v0.1/auth/code/request", json={"email": "a@x.fr"})
    code = simulated_code(db, "a@x.fr")
    wrong = "000000" if code != "000000" else "111111"
    r = client.post("/api/v0.1/auth/code/verify", json={"email": "a@x.fr", "code": wrong})
    assert r.status_code == 401 and r.json()["detail"] == {"error": "invalid_code", "attempts_left": 4}
    assert db.scalars(select(LoginCodeRow)).one().attempts == 1  # counted although refused


def test_resend_too_soon_answers_429_with_retry_after(client, db):
    client.post("/api/v0.1/auth/code/request", json={"email": "a@x.fr"})
    r = client.post("/api/v0.1/auth/code/request", json={"email": "a@x.fr"})
    assert r.status_code == 429 and r.json()["detail"]["error"] == "resend_too_soon"
    assert int(r.headers["retry-after"]) >= 1


def test_invalid_email_422(client):
    r = client.post("/api/v0.1/auth/code/request", json={"email": "pas un mail"})
    assert r.status_code == 422 and r.json()["detail"] == "invalid_email"


def test_revoked_token_is_refused_with_401(client, db):
    out = login(client, db)
    h = {"Authorization": f"Bearer {out['token']}"}
    devices = client.get("/api/v0.1/me/devices", headers=h).json()
    assert [d["current"] for d in devices] == [True]
    assert client.delete(f"/api/v0.1/me/devices/{out['device']['id']}", headers=h).json()["active"] is False
    r = client.get("/api/v0.1/me", headers=h)
    assert r.status_code == 401 and r.json()["detail"] == "device_revoked"
    assert journal(db, "device_token_refused")[-1].details["reason"] == "device_revoked"
    r = client.get("/api/v0.1/me", headers={"Authorization": "Bearer sdt_nimporte"})
    assert r.status_code == 401 and r.json()["detail"] == "device_token_invalid"


def test_logout_revokes_the_token(client, db):
    out = login(client, db)
    h = {"Authorization": f"Bearer {out['token']}"}
    assert client.post("/api/v0.1/auth/logout", headers=h).json() == {"revoked": True}
    assert client.get("/api/v0.1/me", headers=h).status_code == 401
    assert client.post("/api/v0.1/auth/logout").status_code == 409


def test_disabled_account_token_refused(client, db):
    out = login(client, db)
    db.get(UserRow, out["account"]["id"]).status = "disabled"
    db.commit()
    assert client.get("/api/v0.1/me", headers={"Authorization": f"Bearer {out['token']}"}).status_code == 401


def test_without_token_the_device_has_no_account(client):
    me = client.get("/api/v0.1/me").json()
    assert me["email"] is None and me["checked"] is False and me["account"] is None and me["via"] is None
    assert client.get("/api/v0.1/me/devices").status_code == 401


def test_admin_lists_and_revokes_devices(client, db, monkeypatch):
    monkeypatch.setenv("SARCADE_ADMIN_TOKEN", "t0k")
    out = login(client, db, device_id="TEL-07")
    admin = {"Authorization": "Bearer t0k"}
    users = client.get("/api/v0.1/admin/users", headers=admin).json()
    assert [u["email"] for u in users] == ["chef@adrasec78.fr"]
    devices = client.get("/api/v0.1/admin/devices", headers=admin).json()
    assert devices[0]["device_id"] == "TEL-07" and devices[0]["email"] == "chef@adrasec78.fr"
    r = client.post(f"/api/v0.1/admin/devices/{out['device']['id']}/revoke", headers=admin)
    assert r.json()["revoked_by"] == "jeton-admin (.env)"
    assert client.get("/api/v0.1/admin/devices", headers=admin).json() == []
    assert len(client.get("/api/v0.1/admin/devices?include_revoked=true", headers=admin).json()) == 1


def test_admin_routes_refused_without_admin(client, db, monkeypatch):
    monkeypatch.setenv("SARCADE_ADMIN_TOKEN", "t0k")
    assert client.get("/api/v0.1/admin/users").status_code == 401
    assert client.get("/api/v0.1/admin/users", headers={"Authorization": "Bearer faux"}).status_code == 401
    out = login(client, db)
    r = client.get("/api/v0.1/admin/users", headers={"Authorization": f"Bearer {out['token']}"})
    assert r.status_code == 403 and r.json()["detail"] == "admin_required"


def test_env_admin_email_with_a_device_token_is_admin(client, db, monkeypatch):
    monkeypatch.setenv("SARCADE_ADMIN_EMAILS", "chef@adrasec78.fr")
    out = login(client, db)
    r = client.get("/api/v0.1/admin/users", headers={"Authorization": f"Bearer {out['token']}"})
    assert r.status_code == 200
    me = client.get("/api/v0.1/me", headers={"Authorization": f"Bearer {out['token']}"}).json()
    assert "admin" in me["roles"]


def test_cloudflare_access_email_enrols_a_device(db):
    from sarcade.accounts.caller import Caller
    from sarcade.api import auth as auth_api
    req = SimpleNamespace(state=SimpleNamespace(caller=Caller(email="a@x.fr", via="access")),
                          client=SimpleNamespace(host="1.2.3.4"))
    out = auth_api.access_enroll(auth_api.DeviceInfo(device_id="PC-1"), req, db)
    assert out["created"] and out["device"]["method"] == "access" and out["token"].startswith("sdt_")
    with pytest.raises(Exception) as exc:
        auth_api.access_enroll(auth_api.DeviceInfo(), SimpleNamespace(state=SimpleNamespace()), db)
    assert exc.value.status_code == 403


def test_auth_state(client):
    s = client.get("/api/v0.1/auth/state").json()
    assert s == {"methods": ["email_code", "access"], "delivery": "simulated", "code_length": 6,
                 "code_ttl_s": 600, "resend_after_s": 60, "max_attempts": 5}


def test_preferences_follow_the_account_of_the_device(client, db, monkeypatch):
    from sarcade.db.models import UserPreferencesRow
    UserPreferencesRow.__table__.create(db.get_bind())
    out = login(client, db)
    h = {"Authorization": f"Bearer {out['token']}"}
    assert client.get("/api/v0.1/me/preferences", headers=h).json()["available"] is True
