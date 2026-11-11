from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from sarcade import licensing
from sarcade.security import journal, siem
from sarcade.security.watch import REFUSED_THRESHOLD, Watch, admin_action
from sarcade.updates import policy


def entry(**kw):
    base = dict(seq=1, at=datetime(2026, 10, 10, 6, 0, tzinfo=UTC), category="auth", action="login",
                outcome="failure", actor="F4ABC", device_id="TEL-01", event_id=None, source_ip="10.0.0.5",
                details={"reason": "bad_password"}, prev_hash=journal.GENESIS, hash="")
    base.update(kw)
    row = SimpleNamespace(**base)
    row.hash = journal.chain_hash(row.prev_hash, journal.fields(row))
    return row


def test_chain_detects_modification():
    first = entry()
    second = entry(seq=2, action="lockout", prev_hash=first.hash)
    assert journal.chain_hash(second.prev_hash, journal.fields(second)) == second.hash
    first.outcome = "success"
    assert journal.chain_hash(first.prev_hash, journal.fields(first)) != first.hash


def test_journal_never_keeps_message_content_nor_positions():
    clean = journal.sanitize({"body": "secret", "lat": 48.8, "lon": 2.1, "token": "x", "reason": "ok",
                              "recipients": ["PCO", "EQ-1"], "n": 3})
    assert clean == {"reason": "ok", "recipients": ["PCO", "EQ-1"], "n": 3}


def test_syslog_rfc5424_line():
    line = journal.to_syslog(entry(details={"reason": 'bad "pwd"]'}))
    assert line.startswith("<108>1 2026-10-10T06:00:00.000000Z ")  # facility 13, warning
    assert " sarcade - auth.login [sarcade@32473 seq=\"1\" category=\"auth\"" in line
    assert 'reason="bad \\"pwd\\"\\]"' in line
    assert journal.to_syslog(entry(category="suspicious", outcome="info")).startswith("<106>1 ")


def test_octet_counting_frame():
    assert siem.frame("<14>1 é") == b"8 <14>1 \xc3\xa9"


def test_siem_requires_the_pro_module(monkeypatch, tmp_path):
    # Without licence, Core only; the environment switch of v0.1 is gone.
    monkeypatch.setenv("SARCADE_LICENCE_FILE", str(tmp_path / "none.json"))
    monkeypatch.setenv("SARCADE_PRO_MODULES", "siem")
    licensing._state = None
    assert not licensing.pro_enabled("siem")
    licensing._state = None


def test_versions():
    assert policy.is_older("1.2.3", "1.10.0")
    assert not policy.is_older("v1.10.0+42", "1.10")
    assert policy.is_older("1.2.0-rc1", "1.2.0")
    assert policy.is_older(None, "1.0.0") and not policy.is_older("0.1", None)


def test_settings_validation():
    s = policy.validate({})
    assert s["journal_retention_days"] == 365 and s["maintenance"]["windows"][0]["days"] == [1]
    with pytest.raises(policy.InvalidSettings, match="invalid_window_hours"):
        policy.validate({"maintenance": {"windows": [{"days": [1], "start": "25:00", "end": "05:00"}]}})
    with pytest.raises(policy.InvalidSettings, match="invalid_journal_retention_days"):
        policy.validate({"journal_retention_days": 7})
    with pytest.raises(policy.InvalidSettings, match="invalid_timezone"):
        policy.validate({"maintenance": {"timezone": "Mars/Olympus"}})


def test_maintenance_windows_local_time_and_midnight():
    m = policy.validate({})["maintenance"]  # Tuesday 03:00-05:00 Europe/Paris
    tue_4h_paris = datetime(2026, 10, 13, 2, 0, tzinfo=UTC)  # CEST = UTC+2
    assert policy.in_window(m, tue_4h_paris)
    assert not policy.in_window(m, datetime(2026, 10, 13, 3, 30, tzinfo=UTC))  # 05:30 local
    assert policy.next_window(m, datetime(2026, 10, 10, 12, 0, tzinfo=UTC)) == datetime(2026, 10, 13, 1, 0, tzinfo=UTC)
    night = {"timezone": "UTC", "windows": [{"days": [5], "start": "23:00", "end": "01:00"}]}
    assert policy.in_window(night, datetime(2026, 10, 10, 23, 30, tzinfo=UTC))  # Saturday
    assert policy.in_window(night, datetime(2026, 10, 11, 0, 30, tzinfo=UTC))  # Sunday after midnight
    assert not policy.in_window(night, datetime(2026, 10, 12, 0, 30, tzinfo=UTC))


def test_install_never_during_an_active_event():
    m = {"timezone": "UTC", "auto_updates_suspended": False, "windows": [{"days": list(range(7)), "start": "00:00",
                                                                           "end": "23:59"}]}
    now = datetime(2026, 10, 10, 12, tzinfo=UTC)
    kw = dict(available="0.2.0", installed="0.1.0", maintenance=m, now=now)
    assert policy.install_decision(active_events=1, install_requested=True, **kw) == (False, "active_event")
    assert policy.install_decision(active_events=0, install_requested=False, **kw) == (True, "maintenance_window")
    assert policy.install_decision(active_events=0, install_requested=False,
                                   **dict(kw, maintenance=dict(m, auto_updates_suspended=True))) == (False, "suspended")
    assert policy.install_decision(active_events=0, install_requested=True,
                                   **dict(kw, maintenance=dict(m, windows=[]))) == (True, "requested_by_administrator")
    assert policy.install_decision(active_events=0, install_requested=False,
                                   **dict(kw, available="0.1.0")) == (False, "up_to_date")


def test_client_obligation_two_hours_after_invitation_deferred_by_active_event():
    t0 = datetime(2026, 10, 10, 6, tzinfo=UTC)
    kw = dict(app_version="0.9.0", min_version="1.0.0", invited_at=t0)
    assert policy.client_status(now=t0 + timedelta(minutes=119), in_active_event=False, **kw)[0] == "invited"
    assert policy.client_status(now=t0 + timedelta(hours=2), in_active_event=True, **kw)[0] == "deferred"
    assert policy.client_status(now=t0 + timedelta(hours=2), in_active_event=False, **kw)[0] == "required"
    assert policy.client_status(now=t0, in_active_event=False, **dict(kw, app_version="1.0.0"))[0] == "ok"


def test_forgotten_open_event_does_not_block_updates():
    now = datetime(2026, 10, 10, 12, tzinfo=UTC)
    assert policy.event_is_active(None, now - timedelta(hours=3), now)
    assert not policy.event_is_active(None, now - timedelta(days=3), now)
    assert not policy.event_is_active(now, now, now)


def test_watch_alerts_once_on_refused_burst():
    w = Watch()
    alerts = [a for i in range(REFUSED_THRESHOLD + 10) for a in w.observe("1.2.3.4", 404, now=100 + i * 0.1)]
    assert alerts == [("refused_burst", REFUSED_THRESHOLD)]
    assert w.observe("1.2.3.4", 200, now=200) == []


def test_admin_actions_are_recognised():
    assert admin_action("PATCH", "/api/v0.1/events/e1/settings") == ("event_settings_changed", "e1")
    assert admin_action("DELETE", "/api/v0.1/basemaps/pref") == ("basemap_changed", None)
    assert admin_action("GET", "/api/v0.1/events/e1/routes/r1.gpx") == ("data_exported", "e1")
    assert admin_action("GET", "/api/v0.1/events/e1") is None


def test_store_releases_validated_and_read_from_environment(monkeypatch):
    s = policy.validate({"clients": {"releases": {"android": {"version": "0.2.0", "build": 415}}}})
    assert s["clients"]["releases"] == {"android": {"version": "0.2.0", "build": 415, "url": None}}
    assert policy.validate({})["clients"]["releases"] == {}
    for bad in ({"windows": {"version": "1.0"}}, {"ios": {"version": "x"}}, {"ios": {"version": "1.0", "build": -1}},
                {"ios": {"version": "1.0", "url": "http://x"}}):
        with pytest.raises(policy.InvalidSettings):
            policy.validate({"clients": {"releases": bad}})
    assert policy.parse_release("0.3.0+512") == {"version": "0.3.0", "build": 512, "url": None}
    assert policy.parse_release("0.3.0") == {"version": "0.3.0", "build": None, "url": None}
    assert policy.parse_release("abc+x") is None and policy.parse_release("") is None


def test_client_check_carries_the_store_release(monkeypatch):
    from sqlalchemy import BigInteger, create_engine
    from sqlalchemy.ext.compiler import compiles
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from sarcade.db.base import Base
    from sarcade.db.models import ClientDeviceRow, SecurityEventRow, ServerSettingRow
    from sarcade.updates import service

    @compiles(BigInteger, "sqlite")
    def _bigint(type_, compiler, **kw):
        return "INTEGER"

    engine = create_engine("sqlite://", poolclass=StaticPool)
    Base.metadata.create_all(engine, tables=[t.__table__ for t in (ServerSettingRow, ClientDeviceRow, SecurityEventRow)])
    db = sessionmaker(bind=engine)()
    now = datetime(2026, 10, 29, 12, 0, tzinfo=UTC)
    monkeypatch.setenv("SARCADE_CLIENT_RELEASE_ANDROID", "0.4.0+600")
    monkeypatch.delenv("SARCADE_CLIENT_RELEASE_IOS", raising=False)
    assert service.evaluate_client(db, "TEL", "android", "0.1.0", now)["latest"] == \
        {"version": "0.4.0", "build": 600, "url": None}
    assert service.evaluate_client(db, "IPH", "ios", "0.1.0", now)["latest"] is None
    assert service.evaluate_client(db, "PC", "windows", "0.1.0", now)["latest"] is None
    service.set_value(db, "admin", {"clients": {"releases": {"android": {"version": "0.5.0", "build": 700}}}})
    assert service.evaluate_client(db, "TEL", "android", "0.1.0", now)["latest"]["build"] == 700
