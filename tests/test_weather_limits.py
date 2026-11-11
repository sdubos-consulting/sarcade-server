# SPDX-License-Identifier: AGPL-3.0-only
# © SDUBOS Consulting et contributeurs SARCADE
"""Card « Météo : limiter les appels à Météo-France (et aux autres sources
météo) », validated on 10 Nov 2026: choices 1 to 5 and 7 to 11 (choice 1
changed: vigilance every 5 minutes, once for all the events)."""
import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import BigInteger, create_engine, select
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from sarcade import deadlines
from sarcade.api.app import app
from sarcade.api.deps import get_db
from sarcade.db.base import Base
from sarcade.db.models import EventRow, LogbookRow, ServerSettingRow, WeatherCacheRow
from sarcade.events import settings as event_settings
from sarcade.weather import calls as calls_mod
from sarcade.weather import radar as radar_mod
from sarcade.weather import service, vigilance as vg
from sarcade.weather.calls import calls

from test_weather import VIGILANCE, open_meteo


@compiles(BigInteger, "sqlite")
def _bigint_sqlite(type_, compiler, **kw):
    return "INTEGER"


# Midnight in Paris: the simulated day is one day of the counters.
T0 = datetime(2026, 11, 9, 23, 0, 30, tzinfo=UTC)


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    calls.reset()
    service.national.data = service.national.tried_at = service.national.ok_at = None
    service._failed_at.clear()
    service.followed.clear()
    service._department_cell.cache_clear()
    monkeypatch.setenv("SARCADE_METEOFRANCE_API_KEY", "k")
    monkeypatch.delenv("SARCADE_DEPARTMENT", raising=False)
    monkeypatch.delenv("SARCADE_METEOFRANCE_KEY_EXPIRES", raising=False)

    async def quiet(event_id, message):
        return None
    monkeypatch.setattr(service.manager, "broadcast", quiet)
    yield
    calls.reset()


@pytest.fixture
def db(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine, tables=[t.__table__ for t in (WeatherCacheRow, LogbookRow, EventRow,
                                                                    ServerSettingRow)])
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    monkeypatch.setattr(service, "SessionLocal", factory)
    return factory


class Source:
    """Mock of Open-Meteo and of the vigilance of Météo-France."""

    def __init__(self, vigilance=200, forecast=200, headers=None):
        self.vigilance, self.forecast, self.headers = vigilance, forecast, headers or {}
        self.seen = []

    def __call__(self, request):
        path = request.url.path
        self.seen.append(path)
        if "DPVigilance" in str(request.url):
            if self.vigilance != 200:
                return httpx.Response(self.vigilance, headers=self.headers)
            return httpx.Response(200, json=VIGILANCE)
        if self.forecast != 200:
            return httpx.Response(self.forecast, headers=self.headers)
        return httpx.Response(200, json=open_meteo())

    def count(self, kind):
        return sum(1 for p in self.seen if (kind == "vigilance") == ("DPVigilance" in p))


def run_ticks(targets, source, start, minutes, step=1, monkeypatch=None):
    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(source)) as client:
            for m in range(0, minutes, step):
                await service.tick(start + timedelta(minutes=m), client)
    monkeypatch.setattr(service, "load_targets", lambda now: targets)
    asyncio.run(go())


def T(event_id, point=(48.80, 2.13), deps=("78",), source="auto"):
    return service.Target(event_id, point, list(deps), source)


# --- Choice 1 (changed): vigilance every 5 minutes, once for all the events -------------

def test_one_day_two_events_at_most_288_vigilance_and_24_forecasts(db, monkeypatch):
    src = Source()
    # A pass every 5 minutes (the loop runs every minute: see the next test).
    run_ticks([T("e1"), T("e2", (48.83, 2.11), ("92",))], src, T0, 24 * 60, step=5, monkeypatch=monkeypatch)
    assert src.count("vigilance") == 288, "one national map every 5 minutes, whatever the events"
    assert src.count("forecast") == 24, "two events on the same rounded point: one forecast per hour"
    snap = calls.snapshot(T0 + timedelta(hours=23))
    assert snap["sources"]["vigilance"]["calls_today"] == 288 and snap["sources"]["forecast"]["calls_today"] == 24
    s = service.SessionLocal()
    assert s.get(WeatherCacheRow, "vigilance:e1").data["color"] == "orange"
    assert s.get(WeatherCacheRow, "vigilance:e2").data["color"] == "verte"
    assert s.get(WeatherCacheRow, "event:e1").data == s.get(WeatherCacheRow, "event:e2").data


def test_vigilance_every_5_minutes_and_orange_logged_once(db, monkeypatch):
    src = Source()
    run_ticks([T("e1")], src, T0, 30, monkeypatch=monkeypatch)
    assert src.count("vigilance") == 6, "a pass every minute, the map every 5 minutes"
    rows = service.SessionLocal().scalars(select(LogbookRow)).all()
    assert [r.summary for r in rows] == [
        "Vigilance orange Météo-France sur le département 78 (Yvelines) : orages, vent violent"]


# --- Choice 2 and precision of the porteur: no active event, no call -----------------------

def test_no_active_event_no_call_for_an_hour(db, monkeypatch):
    src = Source()
    run_ticks([], src, T0, 60, monkeypatch=monkeypatch)
    assert src.seen == []
    assert all(v["calls_today"] == 0 for v in calls.snapshot()["sources"].values())


def test_active_events():
    now = T0
    old = now - timedelta(days=3)
    assert service.is_active("active", None, old, None, now)
    assert not service.is_active("active", now, old, None, now), "ended"
    assert not service.is_active("closed", None, old, None, now)
    assert not service.is_active("draft", None, old, None, now), "draft without any position"
    assert not service.is_active("draft", None, old, now - timedelta(hours=25), now)
    assert service.is_active("draft", None, old, now - timedelta(hours=2), now)
    assert service.is_active("draft", None, now - timedelta(hours=1), None, now), "draft just created"


def test_no_point_no_department_nothing_fetched(db, monkeypatch):
    src = Source()
    run_ticks([service.Target("e1", None, [], "none")], src, T0, 10, monkeypatch=monkeypatch)
    assert src.seen == []


# --- Choice 3: forecasts shared on the point rounded to 0.1° ---------------------------

def test_shared_point():
    assert service.shared_point((48.8049, 2.1351)) == (48.8, 2.1)
    assert service.point_key((48.83, 2.12)) == service.point_key((48.79, 2.08)) == "forecast:48.8,2.1"
    assert service.point_key((48.86, 2.12)) == "forecast:48.9,2.1"


def test_two_far_points_two_forecasts(db, monkeypatch):
    src = Source()
    run_ticks([T("e1"), T("e2", (45.76, 4.84), ("69",))], src, T0, 60, monkeypatch=monkeypatch)
    assert src.count("forecast") == 2


def test_failed_forecast_retried_after_15_minutes_only(db, monkeypatch):
    src = Source(forecast=500)
    run_ticks([T("e1")], src, T0, 30, monkeypatch=monkeypatch)
    # Météo-France endpoint then Open-Meteo, at 0 and 15 minutes.
    assert src.count("forecast") == 4


# --- Choice 4: pauses after a refusal or a limit ---------------------------------------

def test_refused_vigilance_paused_6_hours(db, monkeypatch):
    src = Source(vigilance=403)
    run_ticks([T("e1")], src, T0, 6 * 60, monkeypatch=monkeypatch)
    assert src.count("vigilance") == 1, "no repeated call after a refusal"
    assert calls.snapshot(T0 + timedelta(hours=1))["sources"]["vigilance"]["state"] == "refused"
    run_ticks([T("e1")], src, T0 + timedelta(hours=6, minutes=1), 5, monkeypatch=monkeypatch)
    assert src.count("vigilance") == 2


def test_limit_waits_for_retry_after(db, monkeypatch):
    src = Source(vigilance=429, headers={"Retry-After": "1200"})
    run_ticks([T("e1")], src, T0, 60, monkeypatch=monkeypatch)
    assert src.count("vigilance") == 3, "at 0, 20 and 40 minutes"
    snap = calls.snapshot(T0 + timedelta(minutes=41))["sources"]["vigilance"]
    assert snap["state"] == "rate_limited" and snap["last_status"] == 429


def test_retry_after_values():
    now = datetime(2026, 11, 10, 0, 0, 30, tzinfo=UTC)
    assert calls_mod.retry_after("120", now) == timedelta(seconds=120)
    assert calls_mod.retry_after(None, now) == timedelta(minutes=15)
    assert calls_mod.retry_after("nonsense", now) == timedelta(minutes=15)
    assert calls_mod.retry_after("Tue, 10 Nov 2026 01:00:30 GMT", now) == timedelta(hours=1)
    assert calls_mod.retry_after("999999", now) == timedelta(hours=24)


def test_forecast_limit_pauses_open_meteo_too(db, monkeypatch):
    src = Source(forecast=429)
    run_ticks([T("e1")], src, T0, 14, monkeypatch=monkeypatch)
    assert src.count("forecast") == 1, "the fallback is not called during the pause"


def test_radar_paused_after_a_refusal(monkeypatch):
    r = radar_mod.Radar()

    async def go(status):
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda req: httpx.Response(status))) as client:
            await r.fetch(client)
    r.watch(1000.0)
    assert r.due(1000.0)
    asyncio.run(go(401))
    assert not r.due(1000.0 + radar_mod.FETCH_EVERY_S + 1), "6 hours of pause after a 401"
    assert r.status()["paused_until"] is not None
    assert calls.snapshot()["sources"]["radar"]["state"] == "refused"
    calls.reset()
    r.watch(5000.0)
    assert r.due(5000.0)


def test_refused_reason_tells_an_unsubscribed_api():
    """Card « Météo-France : vigilance refusée (clé non abonnée) », 11 Nov 2026, choice 4."""
    now = datetime(2026, 11, 11, 20, 51, tzinfo=UTC)
    c = calls_mod.Calls()
    c.record("vigilance", 403, now)
    assert c.snapshot(now)["sources"]["vigilance"]["refused_reason"] == "not_subscribed"
    c = calls_mod.Calls()
    c.record("vigilance", 401, now)
    assert c.snapshot(now)["sources"]["vigilance"]["refused_reason"] == "key_refused"
    c.record("radar", 200, now)
    snap = c.snapshot(now)["sources"]
    assert snap["vigilance"]["refused_reason"] == "not_subscribed", "the radar works with the same key"
    assert snap["radar"]["refused_reason"] is None
    c = calls_mod.Calls()
    c.record("forecast", 401, now)
    c.record("radar", 200, now)
    assert c.snapshot(now)["sources"]["forecast"]["refused_reason"] == "key_refused", "Open-Meteo has no portal key"


# --- Choice 5: counters of the day, in the Diagnostic of the administrator ----------------

def test_counters_reset_each_day_in_paris():
    c = calls_mod.Calls()
    c.record("radar", 200, datetime(2026, 11, 10, 22, 30, tzinfo=UTC))   # 23 h 30 in Paris
    c.record("radar", 200, datetime(2026, 11, 10, 22, 50, tzinfo=UTC))
    snap = c.snapshot(datetime(2026, 11, 10, 23, 10, tzinfo=UTC))         # 0 h 10 on the 11th
    assert snap["day"] == "2026-11-11"
    assert snap["sources"]["radar"]["calls_today"] == 0 and snap["sources"]["radar"]["calls_yesterday"] == 2
    assert snap["sources"]["vigilance"]["state"] == "idle"


def test_diagnostic_of_the_administrator(db, monkeypatch):
    from sarcade.api import diagnostics as diag_api
    session = db()
    monkeypatch.setattr(diag_api, "level_of", lambda request, db: ("admin", "admin@x.fr"))
    monkeypatch.setattr(diag_api.diag, "resources", lambda path, sample_s=0.2: {"cpu_pct": 1.0, "memory": None,
                                                                                "disk": None, "alert": False})

    async def no_mail(db):
        return {}
    monkeypatch.setattr(diag_api, "mail_block", no_mail)
    monkeypatch.setattr(diag_api.siem, "status", lambda db, now=None: {})
    from sarcade import licensing
    monkeypatch.setattr(licensing, "state", lambda: SimpleNamespace(status="none", licence=None))
    calls.record("vigilance", 403)
    app.dependency_overrides[get_db] = lambda: session
    try:
        body = TestClient(app).get("/api/v0.1/diagnostics/server").json()
    finally:
        app.dependency_overrides.clear()
    assert body["level"] == "admin"
    ws = body["weather_sources"]
    assert ws["sources"]["vigilance"]["state"] == "refused" and ws["sources"]["vigilance"]["calls_today"] == 1
    assert ws["sources"]["vigilance"]["refused_reason"] == "not_subscribed"
    assert ws["key"] is True and set(ws["sources"]) == {"radar", "vigilance", "forecast"}


# --- Choice 7: radar unchanged ---------------------------------------------------------

def test_radar_rhythm_unchanged():
    assert radar_mod.FETCH_EVERY_S == 300 and radar_mod.WATCH_S == 900


# --- Choice 8: deadline of the key -----------------------------------------------------

def test_key_deadline_from_the_env(db, monkeypatch):
    session = db()
    now = datetime(2026, 11, 10, 9, 0, tzinfo=UTC)
    row = next(i for i in deadlines.view(session, now)["items"] if i["id"] == "meteo-france")
    assert row["date"] is None, "no date without SARCADE_METEOFRANCE_KEY_EXPIRES"
    monkeypatch.setenv("SARCADE_METEOFRANCE_KEY_EXPIRES", "2026-12-01")
    row = next(i for i in deadlines.view(session, now)["items"] if i["id"] == "meteo-france")
    assert row["date"] == "2026-12-01" and row["days_left"] == 21 and row["level"] == "orange"
    assert row["kind"] == "auto" and row["url"].startswith("https://portail-api.meteofrance.fr")
    # A date typed by the administrator wins.
    items = [dict(d, date="2027-06-01") if d["id"] == "meteo-france" else d for d in deadlines.manual(session)]
    deadlines.save(session, items, "admin@x.fr")
    session.commit()
    row = next(i for i in deadlines.view(session, now)["items"] if i["id"] == "meteo-france")
    assert row["date"] == "2027-06-01" and row["kind"] == "manual"
    # Removed by the administrator: added back from the .env.
    deadlines.save(session, [d for d in deadlines.manual(session) if d["id"] != "meteo-france"], "admin@x.fr")
    session.commit()
    assert [i["date"] for i in deadlines.view(session, now)["items"] if i["id"] == "meteo-france"] == ["2026-12-01"]
    monkeypatch.setenv("SARCADE_METEOFRANCE_KEY_EXPIRES", "pas une date")
    assert deadlines.meteofrance_expires() is None


# --- Choices 9 to 11: department deduced, set by the PCO, several departments -------------

def test_department_deduced_offline_and_cached(monkeypatch):
    from sarcade import places
    assert service.department_at(48.8049, 2.1204) == "78"     # Versailles
    assert service.department_at(48.8924, 2.2069) == "92"     # Nanterre
    seen = []
    real = places.nearest_commune
    monkeypatch.setattr(places, "nearest_commune", lambda lat, lon: seen.append((lat, lon)) or real(lat, lon))
    service._department_cell.cache_clear()
    for _ in range(50):
        service.department_at(48.80491, 2.12041)
        service.department_at(48.80493, 2.12039)
    assert len(seen) == 1, "one lookup per rounded point"


def test_event_departments(monkeypatch):
    versailles, nanterre, lyon = (48.8049, 2.1204), (48.8924, 2.2069), (45.7640, 4.8357)
    assert service.event_departments({"department": "91"}, versailles, [nanterre]) == (["91"], "event")
    assert service.event_departments(None, versailles, [nanterre, versailles, lyon]) == (["78", "92", "69"], "auto")
    assert service.event_departments(None, None, [nanterre]) == (["92"], "auto")
    assert service.event_departments(None, None, []) == ([], "none")
    monkeypatch.setenv("SARCADE_DEPARTMENT", "78")
    assert service.event_departments(None, None, []) == (["78"], "server")
    assert service.event_departments(None, (10.0, -40.0), []) == (["78"], "server"), "at sea: the server default"


def test_highest_vigilance_of_several_departments():
    v = vg.highest(VIGILANCE, ["92", "78"])
    assert v["department"] == "78" and v["department_name"] == "Yvelines" and v["color"] == "orange"
    assert v["departments"] == ["92", "78"]
    v = vg.highest(VIGILANCE, ["92"])
    assert v["color"] == "verte" and v["department_name"] == "Hauts-de-Seine"
    assert vg.highest(VIGILANCE, ["01"]) is None
    assert vg.name_of("2a") == "Corse-du-Sud" and vg.name_of("7") == "Ardèche" and vg.name_of("99") is None


def test_department_setting_logged_both_ways():
    before = event_settings.merged(None)
    after = event_settings.apply_patch(None, {"department": "78"})
    assert event_settings.logbook_summaries(before, after) == ["Météo : vigilance suivie pour le département 78"]
    back = event_settings.apply_patch(after, {"department": None})
    assert back["department"] is None
    assert event_settings.logbook_summaries(after, back) == ["Météo : département de la vigilance déduit des positions"]


def test_event_weather_tells_the_departments_followed(db):
    session = db()
    session.add(EventRow(id="e1", name="Test", kind="exercise", status="draft", created_at=T0, version=0,
                         settings={"weather_lat": 48.8049, "weather_lon": 2.1204}))
    session.add(EventRow(id="e2", name="Test 2", kind="exercise", status="draft", created_at=T0, version=0,
                         settings={"department": "91"}))
    session.commit()
    app.dependency_overrides[get_db] = lambda: session
    try:
        c = TestClient(app)
        assert c.get("/api/v0.1/events/e1/weather").json()["vigilance_departments"] == \
            {"departments": ["78"], "source": "auto"}
        assert c.get("/api/v0.1/events/e2/weather").json()["vigilance_departments"] == \
            {"departments": ["91"], "source": "event"}
        service.followed["e1"] = {"departments": ["78", "92"], "source": "auto"}
        body = c.get("/api/v0.1/events/e1/weather").json()
        assert body["vigilance_departments"]["departments"] == ["78", "92"]
        assert set(body) >= {"forecast", "stale", "age_minutes", "vigilance"}, "older applications: same keys"
    finally:
        app.dependency_overrides.clear()


def test_operators_in_two_departments_get_the_highest(db, monkeypatch):
    src = Source()
    deps, source = service.event_departments(None, (48.8924, 2.2069), [(48.8049, 2.1204)])
    run_ticks([service.Target("e1", (48.8924, 2.2069), deps, source)], src, T0, 1, monkeypatch=monkeypatch)
    v = service.SessionLocal().get(WeatherCacheRow, "vigilance:e1").data
    assert v["department"] == "78" and v["departments"] == ["92", "78"] and v["color"] == "orange"

