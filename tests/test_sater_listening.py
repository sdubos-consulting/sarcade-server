# SPDX-License-Identifier: AGPL-3.0-only
# © SDUBOS Consulting et contributeurs SARCADE
"""Lot 24, server side of three Android Auto cards (3 Nov 2026):
« Signal entendu ici » (SATER): listening point ``kind = "listening"``,
cancelled by its author within 10 s, shown on the TV; event setting
``sater`` (button shown); « messages types en un appui »: event setting
``car_quick_messages`` (4 at most, set by the PCO)."""
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import BigInteger, create_engine, select
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from sarcade import modules, tv
from sarcade.api import operations
from sarcade.api.app import app
from sarcade.api.deps import get_db
from sarcade.db.base import Base
from sarcade.db.models import CommGroupRow, EventRow, LogbookRow
from sarcade.events import settings as s
from sarcade.features import service as f

E, FID = "e1", "li-1"
T0 = datetime(2026, 11, 3, 8, 0, tzinfo=UTC)


def listening(**kw):
    p = {"id": FID, "event_id": E, "kind": "listening", "points": [[48.75, 2.05]],
         "listening": {"heard_at": "2026-11-03T08:00:00Z", "heading_deg": 5, "speed_mps": 12.5},
         "created_by": "CAR-01", "updated_by": "CAR-01", "updated_at": "2026-11-03T08:00:01Z"}
    p.update(kw)
    return p


# --- Listening point ------------------------------------------------------------


def test_listening_point_without_style():
    d = f.validate_upsert(listening(), event_id=E, object_id=FID)
    assert d["kind"] == "listening" and d["color"] == f.LISTENING_COLOR and d["stroke_width"] == 2.0
    assert d["listening"] == {"heard_at": "2026-11-03T08:00:00Z", "heading_deg": 5.0, "speed_mps": 12.5}
    assert f.geometry_wkt(d) == "POINT(2.05 48.75)"


def test_listening_details():
    d = f.validate_upsert(listening(listening=None), event_id=E, object_id=FID)
    assert d["listening"] == {"heard_at": "2026-11-03T08:00:01Z"}, "heard at the time of the change"
    full = f.validate_listening({"heard_at": "2026-11-03T08:00:00Z", "exercise": True, "search": " 121,375 ",
                                 "frequency_mhz": 121.375, "comment": "faible", "accuracy_m": 8})
    assert full["exercise"] is True and full["search"] == "121,375" and full["frequency_mhz"] == 121.375
    assert "exercise" not in f.validate_listening({"exercise": False}, "2026-11-03T08:00:00Z")


@pytest.mark.parametrize("raw", [{"heading_deg": 400}, {"heading_deg": "nord"}, {"speed_mps": -1},
                                 {"exercise": "oui"}, {"heard_at": "hier"}, "x", {"comment": "x" * 301}])
def test_invalid_listening(raw):
    with pytest.raises(f.InvalidFeature):
        f.validate_upsert(listening(listening=raw), event_id=E, object_id=FID)


def test_cancel_window_of_the_author():
    data = {"listening": {"heard_at": "2026-11-03T08:00:00Z"}}
    assert f.listening_cancel_allowed(data, T0 + timedelta(seconds=10))
    assert f.listening_cancel_allowed(data, T0 + timedelta(seconds=15)), "5 s of margin for the network"
    assert not f.listening_cancel_allowed(data, T0 + timedelta(seconds=16))
    assert not f.listening_cancel_allowed({}, T0)


def test_logbook_lines_of_a_listening_point():
    assert f.logbook_summary("listening", "", "create", author="CAR-01", heading=134.6) == \
        "Signal de balise entendu ici par CAR-01, cap 135° (point d'écoute)"
    assert f.logbook_summary("listening", "", "create", author="CAR-01") == \
        "Signal de balise entendu ici par CAR-01 (point d'écoute)"
    assert f.logbook_summary("listening", "", "delete", author="CAR-01") == "Point d'écoute de CAR-01 annulé"
    assert f.logbook_summary("listening", "", "create", author="CAR-01", exercise=True).startswith("EXERCICE · ")
    # Other objects unchanged.
    assert f.logbook_summary("point", "A", "create") == "Objet cartographique créé : Point « A »"


def test_listening_point_on_the_tv():
    feat = dict(f.validate_upsert(listening(), event_id=E, object_id=FID), id=FID)
    li = tv.tv_listening(feat)
    assert li == {"id": FID, "lat": 48.75, "lon": 2.05, "heard_at": "2026-11-03T08:00:00Z", "exercise": False,
                  "by": "CAR-01", "heading_deg": 5.0}
    snap = {"positions": []}
    assert tv.apply_view(snap, None, T0, listening=[li])["listening"] == [li]
    off = {"layers": {"bearings": False}}
    assert tv.apply_view(snap, off, T0, listening=[li])["listening"] == []
    assert "listening" not in tv.apply_view(snap, None, T0), "unchanged layers: not sent again"


def test_tv_page_draws_the_listening_points():
    from sarcade.api import tv as tv_api
    page = tv_api._TV
    assert "lData=s.listening||[]" in page and "for(const li of lData)" in page
    assert "Point d’écoute" in page


# --- Settings: sater and car_quick_messages --------------------------------------


def test_sater_setting():
    assert s.merged(None)["sater"] is False
    assert s.apply_patch(None, {"sater": True})["sater"] is True
    with pytest.raises(s.InvalidSettings):
        s.apply_patch(None, {"sater": "oui"})
    with pytest.raises(s.InvalidSettings, match="module_inactive:adrasec"):
        s.apply_patch({"modules": []}, {"sater": True})
    # ADRASEC turned off: SATER off too.
    assert s.apply_patch({"sater": True}, {"modules": []})["sater"] is False
    assert s.logbook_summaries({}, {"sater": True}) == [
        "Mission ou exercice SATER : bouton « Signal entendu ici » proposé"]


def test_sater_kind():
    assert modules.is_sater_kind("sater") and modules.is_sater_kind(" SATER ")
    assert not modules.is_sater_kind("exercise") and not modules.is_sater_kind(None)


def test_default_car_quick_messages():
    quick = s.merged(None)["car_quick_messages"]
    assert quick == [{"text": "Bien reçu", "priority": "routine"}, {"text": "J'arrive", "priority": "routine"},
                     {"text": "Besoin d'aide", "priority": "urgent"}, {"text": "Retard", "priority": "routine"}]
    quick[0]["text"] = "changé"
    assert s.merged(None)["car_quick_messages"][0]["text"] == "Bien reçu", "defaults never changed in place"


def test_car_quick_messages_set_by_the_pco():
    new = s.apply_patch(None, {"car_quick_messages": [" En   place ", {"text": "Besoin d'aide", "priority": "urgent"}]})
    assert new["car_quick_messages"] == [{"text": "En place", "priority": "routine"},
                                         {"text": "Besoin d'aide", "priority": "urgent"}]
    assert s.apply_patch(new, {"car_quick_messages": None})["car_quick_messages"] == \
        s.DEFAULT_SETTINGS["car_quick_messages"]
    assert s.logbook_summaries(None, new) == ["Messages types de la voiture : En place, Besoin d'aide (urgent)"]


@pytest.mark.parametrize("value", [[], ["a", "b", "c", "d", "e"], ["x" * 41], [""], ["A", "a"],
                                   [{"text": "Aide", "priority": "distress"}], [{"priority": "urgent"}], [3], "Bien reçu"])
def test_invalid_car_quick_messages(value):
    with pytest.raises(s.InvalidSettings):
        s.apply_patch(None, {"car_quick_messages": value})


# --- API -------------------------------------------------------------------------


@compiles(BigInteger, "sqlite")
def _bigint_sqlite(type_, compiler, **kw):  # autoincrement keys on SQLite
    return "INTEGER"


@pytest.fixture
def client(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine, tables=[t.__table__ for t in (EventRow, LogbookRow, CommGroupRow)])
    db = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)()

    async def broadcast(event_id, msg):
        return None
    monkeypatch.setattr(operations.manager, "broadcast", broadcast)
    app.dependency_overrides[get_db] = lambda: db
    yield TestClient(app), db
    app.dependency_overrides.pop(get_db, None)
    db.close()


def test_event_of_kind_sater_offers_the_button(client):
    c, db = client
    sat = c.post("/api/v0.1/events", json={"name": "Balise", "kind": "sater"}).json()["id"]
    ex = c.post("/api/v0.1/events", json={"name": "Exercice", "kind": "exercise"}).json()["id"]
    core = c.post("/api/v0.1/events", json={"name": "Sans ADRASEC", "kind": "sater", "modules": []}).json()["id"]
    assert c.get(f"/api/v0.1/events/{sat}").json()["settings"]["sater"] is True
    assert c.get(f"/api/v0.1/events/{ex}").json()["settings"]["sater"] is False
    assert c.get(f"/api/v0.1/events/{core}").json()["settings"]["sater"] is False
    lines = [x.summary for x in db.scalars(select(LogbookRow).where(LogbookRow.event_id == sat))]
    assert "Mission ou exercice SATER : bouton « Signal entendu ici » proposé" in lines
    # Read by every participant, set by the PCO.
    r = c.patch(f"/api/v0.1/events/{ex}/settings",
                json={"actor_id": "PCO", "settings": {"sater": True, "car_quick_messages": ["Bien reçu", "Retard"]}})
    assert r.status_code == 200
    got = c.get(f"/api/v0.1/events/{ex}").json()["settings"]
    assert got["sater"] is True and [m["text"] for m in got["car_quick_messages"]] == ["Bien reçu", "Retard"]
    bad = c.patch(f"/api/v0.1/events/{ex}/settings",
                  json={"actor_id": "PCO", "settings": {"car_quick_messages": ["1", "2", "3", "4", "5"]}})
    assert bad.status_code == 422 and bad.json()["detail"] == "invalid_value:car_quick_messages"
