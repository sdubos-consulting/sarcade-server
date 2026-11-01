"""Smart TV display: pairing, token, what is shown, TV address (29 Oct 2026)."""
from sarcade import tv
from sarcade.api import tv as api


def test_pairing_code_approved_once():
    s = tv.Store()
    p = s.new(now=1000)
    assert len(p.code) == 6 and set(p.code) <= set(tv.CODE_ALPHABET)
    assert s.approve(p.code.lower(), "evt", now=1010).token
    assert s.approve(p.code, "evt", now=1011) is None          # once
    q = s.new(now=1000)
    assert s.approve(q.code, "evt", now=1000 + tv.CODE_TTL_S + 1) is None  # expired


def test_rate_limit_per_client():
    s = tv.Store()
    assert all(s.allowed("1.2.3.4", now=50) for _ in range(tv.REQUESTS_PER_MINUTE))
    assert not s.allowed("1.2.3.4", now=55)
    assert s.allowed("5.6.7.8", now=55)
    assert s.allowed("1.2.3.4", now=200)


def test_token_valid_until_revoked_or_expired():
    d = {"hash": tv.token_hash("tok"), "expires": 2000, "revoked": False}
    assert tv.valid_display([d], "tok", now=1000) is d
    assert tv.valid_display([d], "other", now=1000) is None
    assert tv.valid_display([d], "tok", now=3000) is None
    assert tv.valid_display([{**d, "revoked": True}], "tok", now=1000) is None


def test_tv_shows_general_messages_only():
    s = tv.snapshot({"id": "e", "name": "Exercice"}, [], [
        {"recipient_ids": [], "body": "à tous"}, {"recipient_ids": ["user:PCO"], "body": "privé"},
        {"recipient_ids": ["group:x"], "body": "canal"}])
    assert [m["body"] for m in s["messages"]] == ["à tous"]


def test_tv_address_exposes_the_display_only():
    assert api.tv_host_allows("/tv")
    assert api.tv_host_allows("/api/v0.1/tv/pair")
    assert api.tv_host_allows("/api/v0.1/tv/pair/abc")
    assert api.tv_host_allows("/api/v0.1/tv/state")
    assert not api.tv_host_allows("/api/v0.1/tv/approve")
    assert not api.tv_host_allows("/tv/approve")
    assert not api.tv_host_allows("/api/v0.1/events")
    assert not api.tv_host_allows("/")


def test_tv_no_longer_shows_the_logbook():
    """Decision of 29 Oct 2026: right column = general messages only."""
    s = tv.snapshot({"id": "e", "name": "Exercice"}, [], [])
    assert "logbook" not in s
    assert "Main courante" not in api._TV and "id=\"log\"" not in api._TV


def test_any_operator_pairs_a_tv():
    """Decision of 29 Oct 2026: approving or revoking a TV is not a PCO right."""
    import inspect
    from types import SimpleNamespace
    for fn in (api.approve, api.revoke_display, api.list_displays):
        assert "require_pco" not in inspect.getsource(fn)
    req = SimpleNamespace(state=SimpleNamespace(access=SimpleNamespace(email="op@x.fr")))
    assert api._actor(req) == "op@x.fr"
    assert api._actor(SimpleNamespace(state=SimpleNamespace())) == "opérateur"


def test_tv_logo_is_served_on_the_tv_address():
    assert api.tv_host_allows("/tv/logo.png")
    assert api._LOGO.is_file()


def test_tv_address_alone_opens_the_display():
    from fastapi.testclient import TestClient
    from sarcade.api.app import app
    c = TestClient(app)
    r = c.get("/", headers={"host": "tv.sarcade.org"})
    assert r.status_code == 200 and "SARCADE" in r.text and "sarcade_tv_token" in r.text
    assert c.get("/api/v0.1/events", headers={"host": "tv.sarcade.org"}).status_code == 404



def test_view_sent_by_a_phone_is_checked():
    v = tv.clean_view({"lat": 48.8, "lon": 2.1, "zoom": 14.333, "basemap": "ign",
                       "tile_url": "https://data.geopf.fr/wmts/{z}/{x}/{y}", "attribution": "IGN"}, now=1)
    assert v == {"lat": 48.8, "lon": 2.1, "zoom": 14.33, "basemap": "ign",
                 "tile_url": "https://data.geopf.fr/wmts/{z}/{x}/{y}", "attribution": "IGN", "rev": 1000,
                 "layers": tv.DEFAULT_LAYERS, "opacity": tv.DEFAULT_OPACITY, "messages": True, "from": "", "theme": "auto"}
    assert tv.clean_view(None) is None
    import pytest
    for bad in ({"lat": 99, "lon": 2, "zoom": 10}, {"lat": 1, "lon": 2, "zoom": 30},
                {"lat": 1, "lon": 2, "zoom": 10, "tile_url": "http://x/{z}/{x}/{y}"},
                {"lat": 1, "lon": 2, "zoom": 10, "tile_url": 'https://x/{z}/{x}/{y}"><script>'},
                {"lat": 1, "lon": 2, "zoom": 10, "tile_url": "javascript:alert(1)"}):
        with pytest.raises(ValueError):
            tv.clean_view(bad)


# --- Step 2 of « Envoyer ma vue », weather in the top bar (29 Oct 2026) -----

from datetime import UTC, datetime, timedelta  # noqa: E402
from types import SimpleNamespace  # noqa: E402

import pytest  # noqa: E402

VIEW = {"lat": 48.8, "lon": 2.1, "zoom": 12}


def test_view_layers_opacity_and_messages():
    v = tv.clean_view({**VIEW, "layers": {"aprs": False, "relays": True, "nope": True},
                       "opacity": {"draw": 0.5, "brg": 1}, "messages": False, "extra": 1}, now=1)
    assert v["layers"] == {**tv.DEFAULT_LAYERS, "aprs": False, "relays": True}
    assert v["opacity"] == {"ops": 1.0, "aprs": 1.0, "draw": 0.5, "brg": 1.0, "high": 1.0, "relay": 1.0, "traces": 1.0}
    assert v["messages"] is False and "extra" not in v
    d = tv.clean_view(VIEW, now=1)
    assert d["layers"] == {"operators": True, "aprs": True, "offline": True, "drawings": True,
                           "bearings": True, "highpoints": False, "relays": False, "traces": False}
    assert d["messages"] is True
    # Automatic framing (no view) and views stored before step 2: defaults.
    assert tv.display_options(None)["layers"] == tv.DEFAULT_LAYERS
    assert tv.display_options({"lat": 1})["opacity"] == tv.DEFAULT_OPACITY


@pytest.mark.parametrize("extra", [{"layers": []}, {"layers": {"aprs": "non"}}, {"opacity": {"ops": 0}},
                                   {"opacity": {"ref": 1.5}}, {"opacity": {"draw": True}}, {"opacity": 1},
                                   {"messages": "oui"}])
def test_invalid_view_options(extra):
    with pytest.raises(ValueError):
        tv.clean_view({**VIEW, **extra})


NOW = datetime(2026, 10, 29, 14, 20, tzinfo=UTC)


def test_hidden_layers_are_respected():
    old = (NOW - timedelta(minutes=30)).isoformat()
    snap = {"positions": [{"device_id": "A", "source": "device", "time": NOW}, {"device_id": "B", "source": "device", "time": old},
                          {"device_id": "aprs:F4X", "source": "aprs", "time": NOW}],
            "messages": [{"body": "x"}]}
    drawings, bearings = [{"kind": "zone"}], [{"azimuth_deg": 10}]
    refs = [{"category": "HIGH_POINT", "name": "PH"}, {"category": "RELAY", "name": "R"}]
    out = tv.apply_view(snap, None, NOW, drawings=drawings, bearings=bearings, references=refs)
    assert [p["device_id"] for p in out["positions"]] == ["A", "B", "aprs:F4X"]
    assert out["drawings"] == drawings and out["bearings"] == bearings and out["references"] == []
    view = tv.clean_view({**VIEW, "layers": {"offline": False, "aprs": False, "drawings": False, "bearings": False,
                                             "highpoints": True}, "messages": False})
    out = tv.apply_view(snap, view, NOW, drawings=drawings, bearings=bearings, references=refs)
    assert [p["device_id"] for p in out["positions"]] == ["A"]
    assert out["drawings"] == [] and out["bearings"] == [] and [r["name"] for r in out["references"]] == ["PH"]
    assert out["options"]["messages"] is False and out["messages"] == [{"body": "x"}]   # banner kept
    view = tv.clean_view({**VIEW, "layers": {"operators": False}})
    assert [p["device_id"] for p in tv.apply_view(snap, view, NOW)["positions"]] == ["aprs:F4X"]
    assert "drawings" not in tv.apply_view(snap, view, NOW)                # unchanged: not sent again


def test_bearing_for_the_tv():
    f = {"id": "b1", "points": [[48.8, 2.1]], "label": "",
         "bearing": {"azimuth_deg": 45, "length_m": 8000, "search": "Balise Rambouillet", "exercise": True}}
    b = tv.tv_bearing(f)
    assert b["lat"] == 48.8 and b["azimuth_deg"] == 45 and b["exercise"] is True
    assert b["color"] == tv.search_color("Balise Rambouillet") in tv.SEARCH_COLORS
    assert tv.tv_bearing({**f, "bearing": {**f["bearing"], "exercise": None}})["exercise"] is False
    assert tv.tv_drawing({"id": "z", "kind": "zone", "points": [[1, 2]], "color": 1, "created_by": "x"}) == {
        "id": "z", "kind": "zone", "points": [[1, 2]], "color": 1}


def forecast(code=0, cloud=10, temp=12.4):
    hours = [{"time": f"2026-10-29T{h:02d}:00:00Z", "temperature_c": temp + h / 100, "weather_code": code,
              "cloud_cover_pct": cloud} for h in range(24)]
    return {"hourly": hours, "daily": [{"date": "2026-10-29", "sunrise": "2026-10-29T06:30:00Z",
                                        "sunset": "2026-10-29T16:50:00Z"}]}


def test_weather_of_the_current_hour():
    w = tv.weather_now(forecast(), None, NOW)
    assert w == {"temp_c": 12.5, "icon": "sun", "vigilance": None,
                 "sunrise": "2026-10-29T06:30:00+00:00", "sunset": "2026-10-29T16:50:00+00:00"}
    night = NOW.replace(hour=21)
    assert tv.weather_now(forecast(), None, night)["icon"] == "moon"
    assert tv.weather_now(forecast(code=2), None, night)["icon"] == "moon_cloud"
    assert tv.weather_now(forecast(code=2), None, NOW)["icon"] == "sun_cloud"
    for code, icon in ((3, "cloud"), (61, "rain"), (81, "rain"), (95, "storm"), (73, "snow"), (45, "fog")):
        assert tv.weather_now(forecast(code=code), None, NOW)["icon"] == icon
    assert tv.weather_now(forecast(code=None, cloud=90), None, NOW)["icon"] == "cloud"
    assert tv.weather_now(forecast(), {"color_id": 3}, NOW)["vigilance"] == "orange"
    assert tv.weather_now(forecast(), {"color_id": 4}, NOW)["vigilance"] == "red"
    assert tv.weather_now(forecast(), {"color_id": 2}, NOW)["vigilance"] == "yellow"
    assert tv.weather_now(forecast(), {"color_id": 1}, NOW)["vigilance"] is None
    assert tv.weather_now(None, None, NOW) is None
    no_sun = forecast()
    no_sun["daily"] = []
    assert "sunrise" not in tv.weather_now(no_sun, None, NOW)
    assert tv.weather_now(forecast(), None, NOW + timedelta(days=3)) is None   # outdated forecast


def test_snapshot_weather_from_the_weather_cache(monkeypatch):
    cache = {"event:e1": SimpleNamespace(data=forecast(code=61)), "vigilance:e1": SimpleNamespace(data={"color_id": 3})}
    calls = []

    def fake_get(db, key):
        calls.append(key)
        return cache.get(key)
    monkeypatch.setattr(api.weather_service, "cache_get", fake_get)
    monkeypatch.setattr(tv, "weather_cache", tv.WeatherCache())
    monkeypatch.setattr(api, "datetime", SimpleNamespace(now=lambda tz=None: NOW))
    w = api._weather(None, "e1")
    assert {k: w[k] for k in ("temp_c", "icon", "vigilance")} == {"temp_c": 12.5, "icon": "rain", "vigilance": "orange"}
    assert w["sunrise"] and w["sunset"]                                   # sun times instead of the icon on the TV
    api._weather(None, "e1")
    assert calls == ["event:e1", "vigilance:e1"]                 # kept 10 minutes
    assert api._weather(None, "no-point") is None                # event without weather point


def test_weather_cache_expires_after_ten_minutes():
    c = tv.WeatherCache()
    values = iter([1, 2])
    assert c.get("e", lambda: next(values), now=0) == 1
    assert c.get("e", lambda: next(values), now=599) == 1
    assert c.get("e", lambda: next(values), now=601) == 2


def test_tv_page_draws_the_layers_with_escaped_texts():
    for needle in ("drawFeatures", "drawBearings", "drawRefs", "EXERCICE", "msgBox", 'id="wx"',
                   "esc(f.label)", "esc(z.label)", "esc(r.callsign||r.name)"):
        assert needle in api._TV


# --- SOS banner of a screen (29 Oct 2026) -----------------------------------

T_SOS = datetime(2026, 10, 29, 20, 0, tzinfo=UTC)


def _m(who, body, minutes_ago, priority="immediate", to=()):
    return {"sender_id": who, "body": body, "priority": priority, "recipient_ids": list(to),
            "created_at": T_SOS - timedelta(minutes=minutes_ago)}


SOS = "🆘 DÉTRESSE — TEL-01 — position 48.8, 2.1"


def test_distress_shows_banner_and_pill():
    st = tv.alert_state([_m("TEL-01", SOS, 2)], T_SOS)
    assert st["alert"]["body"] == SOS and st["sos"] == ["TEL-01"]


def test_end_of_distress_clears_banner_and_pill():
    msgs = [_m("TEL-01", SOS, 3), _m("TEL-01", "Fin de détresse — TEL-01", 1, "urgent")]
    assert tv.alert_state(msgs, T_SOS) == {"alert": None, "sos": []}
    # The end of another sender closes nothing.
    msgs = [_m("TEL-01", SOS, 3), _m("TEL-02", "Fin de détresse — TEL-02", 1, "urgent")]
    assert tv.alert_state(msgs, T_SOS)["sos"] == ["TEL-01"]
    # English app.
    msgs = [_m("TEL-01", "🆘 DISTRESS — TEL-01 — position x", 3), _m("TEL-01", "End of distress — TEL-01", 1, "urgent")]
    assert tv.alert_state(msgs, T_SOS)["sos"] == []


def test_cleared_banner_keeps_the_pill_and_comes_back_on_a_new_message():
    msgs = [_m("TEL-01", SOS, 3)]
    cleared = (T_SOS - timedelta(minutes=1)).timestamp()
    assert tv.alert_state(msgs, T_SOS, cleared) == {"alert": None, "sos": ["TEL-01"]}
    msgs.append(_m("TEL-02", "Évacuez le secteur", 0))
    assert tv.alert_state(msgs, T_SOS, cleared)["alert"]["body"] == "Évacuez le secteur"


def test_banner_ignores_old_and_private_messages():
    assert tv.alert_state([_m("A", "vieux", 11)], T_SOS)["alert"] is None
    assert tv.alert_state([_m("A", SOS, 1, to=["user:PCO"])], T_SOS) == {"alert": None, "sos": []}


@pytest.fixture
def sos_client(monkeypatch):
    from fastapi.testclient import TestClient
    from sqlalchemy import BigInteger, create_engine
    from sqlalchemy.ext.compiler import compiles
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from sarcade.api.app import app
    from sarcade.api.deps import get_db
    from sarcade.db.base import Base
    from sarcade.db.models import EventRow, LogbookRow, MessageRow, ServerSettingRow
    from sarcade.updates.service import set_value

    @compiles(BigInteger, "sqlite")
    def _bigint(type_, compiler, **kw):
        return "INTEGER"
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine, tables=[t.__table__ for t in (EventRow, LogbookRow, MessageRow, ServerSettingRow)])
    db = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)()
    db.add(EventRow(id="e1", name="Exercice", kind="exercise", status="active", created_at=T_SOS, version=0, settings={}))
    db.add(MessageRow(id="m1", event_id="e1", sender_id="TEL-01", recipient_ids=[], priority="immediate",
                      body=SOS, created_at=datetime.now(UTC)))
    set_value(db, api.KEY, [{"id": "d1", "hash": "h", "event_id": "e1", "created": 0, "expires": 9e12,
                             "revoked": False, "code": "ABCDEF", "owner": "op@x.fr"}])
    db.commit()
    who = {"email": None, "roles": []}
    monkeypatch.setattr(api, "checked_email", lambda request: who["email"])
    monkeypatch.setattr(api.roles, "roles_of", lambda db, email: who["roles"])
    app.dependency_overrides[get_db] = lambda: db
    yield TestClient(app), who, db
    app.dependency_overrides.pop(get_db, None)


def test_clear_alert_route_rights(sos_client):
    c, who, db = sos_client
    url = "/api/v0.1/events/e1/displays/d1/alert/clear"
    [d] = c.get("/api/v0.1/events/e1/displays").json()
    assert d["alert"] is True and d["owner"] == "op@x.fr"
    who["email"] = "autre@x.fr"
    assert c.get("/api/v0.1/events/e1/displays").json()[0]["can_clear_alert"] is False
    assert c.post(url).status_code == 403
    who["email"] = "op@x.fr"
    assert c.post(url).status_code == 200
    [d] = c.get("/api/v0.1/events/e1/displays").json()
    assert d["alert"] is False
    disp = api._displays(db)[0]
    assert api._alert(db, "e1", disp) == {"alert": None, "sos": ["TEL-01"]}
    who["email"], who["roles"] = "admin@x.fr", ["admin"]
    assert c.post(url).status_code == 200
    assert c.post("/api/v0.1/events/e1/displays/zz/alert/clear").status_code == 404


def test_may_clear_owner_or_admin():
    d = {"owner": "op@x.fr"}
    assert api._may_clear(d, "op@x.fr", False)
    assert not api._may_clear(d, "autre@x.fr", False)
    assert api._may_clear(d, "autre@x.fr", True)
    assert api._may_clear({"owner": None}, None, False)       # local network without Access
    assert not api._may_clear({"owner": None}, "autre@x.fr", False)


def test_approve_records_the_owner():
    s = tv.Store()
    p = s.new(now=1000)
    assert s.approve(p.code, "evt", now=1001, approved_by="op@x.fr").approved_by == "op@x.fr"


# --- Lot 4, TV screen (30 Oct 2026) ------------------------------------------

def test_opacity_per_layer_and_former_format():
    v = tv.clean_view({**VIEW, "opacity": {"ops": 0.8, "aprs": 0.4, "high": 0.3, "relay": 0.6, "brg": 0.5}}, now=1)
    assert v["opacity"] == {"ops": 0.8, "aprs": 0.4, "draw": 1.0, "brg": 0.5, "high": 0.3, "relay": 0.6, "traces": 1.0}
    old = tv.clean_view({**VIEW, "opacity": {"ops": 0.7, "ref": 0.2}}, now=1)
    assert old["opacity"]["aprs"] == 0.7 and old["opacity"]["high"] == 0.2 and old["opacity"]["relay"] == 0.2
    with pytest.raises(ValueError):
        tv.clean_view({**VIEW, "opacity": {"aprs": 0}})


def test_view_names_the_sender():
    assert tv.clean_view({**VIEW, "from": " PCO-Web "}, now=1)["from"] == "PCO-Web"
    for bad in (1, "x" * 65):
        with pytest.raises(ValueError):
            tv.clean_view({**VIEW, "from": bad})


def test_search_colours_as_the_application():
    assert tv.search_color("406,028 MHz · Balise") == "#9B4DFF"
    assert tv.search_color("121,500 MHz · Détresse") == "#E53935"
    assert tv.search_color("121,375 MHz · Balise d'exercice") == "#1E88E5"
    assert tv.search_color("") == tv.SEARCH_COLORS[0]
    # Same hash as Dart (h*31 + code unit): « ab » = 97*31+98 = 3105, 3105 % 5 = 0.
    assert tv.search_color("ab") == tv.SEARCH_COLORS[3105 % 5]


def _b(lat, lon, az, search="S", **kw):
    return {"lat": lat, "lon": lon, "azimuth_deg": az, "length_m": 10000, "search": search, **kw}


def test_probable_zone_as_the_application():
    # Three bearings towards ≈ (48.80, 2.10).
    bs = [_b(48.75, 2.10, 0), _b(48.80, 2.03, 90), _b(48.75, 2.04, 38, exercise=True)]
    [z] = tv.probable_zones(bs)
    assert z["bearings"] == 3 and len(z["crossings"]) == 3 and z["exercise"] is True
    assert abs(z["lat"] - 48.80) < 0.01 and abs(z["lon"] - 2.10) < 0.01
    assert z["radius_m"] >= 50 and z["label"].endswith("· 3 relevés") and z["label"].startswith("±")
    c = tv.crossing(bs[0], bs[1])
    assert abs(c[0] - 48.80) < 0.002 and abs(c[1] - 2.10) < 0.001
    # Parallel or apart: no crossing, no zone.
    [z] = tv.probable_zones([_b(48.7, 2.1, 0), _b(48.7, 2.2, 0)])
    assert z["crossings"] == [] and "lat" not in z
    assert tv.format_distance(426.2) == "426 m" and tv.format_distance(1250) == "1,25 km"


def test_zones_follow_the_bearings_layer():
    snap = {"positions": [], "messages": []}
    bs = [_b(48.75, 2.10, 0), _b(48.80, 2.03, 90)]
    out = tv.apply_view(snap, None, NOW, bearings=bs)
    assert out["zones"][0]["lat"] is not None
    off = tv.clean_view({**VIEW, "layers": {"bearings": False}})
    assert tv.apply_view(snap, off, NOW, bearings=bs)["zones"] == []


def test_tv_bearing_has_its_cone():
    f = {"id": "b", "points": [[48.8, 2.1]], "bearing": {"azimuth_deg": 10, "cone_deg": 3, "search": "121,375"}}
    b = tv.tv_bearing(f)
    assert b["cone_deg"] == 3 and b["color"] == "#1E88E5"
    assert tv.tv_bearing({**f, "bearing": {"azimuth_deg": 10}})["cone_deg"] == 5


def test_tv_page_lot4():
    t = api._TV
    assert "Outils › Afficher sur une TV" in t and "Paramètres › Affichage" not in t
    assert "padding:0 20px 0 10px" in t
    assert "blab" not in t                         # no label at the end of the bearings
    for needle in ('id="legend"', "esc(k||'Sans signal')", "esc(z.label)", "Zone probable", "'Vue de '+v.from",
                   "textContent=v.from", "o.aprs", "o.high", "o.relay", "cone_deg"):
        assert needle in t


def test_nearby_codes_same_public_address():
    s = tv.Store()
    a = s.new(now=1000, ip="1.2.3.4")
    s.new(now=1000, ip="5.6.7.8")
    assert [p.code for p in s.nearby("1.2.3.4", now=1001)] == [a.code]
    assert s.nearby(None, now=1001) == []
    s.approve(a.code, "e", now=1002)
    assert s.nearby("1.2.3.4", now=1003) == []      # approved: no longer offered
    b = s.new(now=1000, ip="9.9.9.9")
    assert s.nearby("9.9.9.9", now=1000 + tv.CODE_TTL_S + 1) == [] and b


def test_nearby_route_every_participant_and_messages_per_screen(sos_client, monkeypatch):
    c, who, db = sos_client
    monkeypatch.setattr(tv, "store", tv.Store())
    h = {"cf-connecting-ip": "81.1.2.3"}
    code = c.post("/api/v0.1/tv/pair", headers=h).json()["code"]
    assert tv.store.pairings[next(iter(tv.store.pairings))].ip == "81.1.2.3"
    # Every participant (30 Oct 2026, 13 h 19): local network, operator, PCO.
    assert [n["code"] for n in c.get("/api/v0.1/tv/nearby", headers=h).json()] == [code]
    who["email"], who["roles"] = "op@x.fr", []
    assert [n["code"] for n in c.get("/api/v0.1/tv/nearby", headers=h).json()] == [code]
    who["roles"] = ["pco"]
    [n] = c.get("/api/v0.1/tv/nearby", headers=h).json()
    assert n["code"] == code and n["expires_in"] > 0
    assert not api.tv_host_allows("/api/v0.1/tv/nearby")                # never on the TV address
    assert c.get("/api/v0.1/tv/nearby", headers={"cf-connecting-ip": "90.0.0.1"}).json() == []
    # Messages column of one screen, at once, without a view.
    url = "/api/v0.1/events/e1/displays/d1/messages"
    assert c.put(url, json={"messages": False}).json() == {"messages": False}
    [d] = c.get("/api/v0.1/events/e1/displays").json()
    assert d["messages"] is False and d["revoked"] is False and d["view_from"] is None
    assert c.put("/api/v0.1/events/e1/displays/zz/messages", json={"messages": True}).status_code == 404
    v = c.put("/api/v0.1/events/e1/displays/d1/view", json={"view": {**VIEW, "from": "PCO-Web", "messages": True}}).json()
    assert v["view"]["from"] == "PCO-Web"
    [d] = c.get("/api/v0.1/events/e1/displays").json()
    # 1 Nov 2026: a view sent (or followed) keeps the screen's choice...
    assert d["messages"] is False and d["view_from"] == "PCO-Web"
    c.put("/api/v0.1/events/e1/displays/d1/view", json={"view": {**VIEW, "messages": True}, "live": True})
    [d] = c.get("/api/v0.1/events/e1/displays").json()
    assert d["messages"] is False
    # ...unless « Colonne des messages » is ticked on purpose when sending.
    c.put("/api/v0.1/events/e1/displays/d1/view", json={"view": VIEW, "messages": True})
    [d] = c.get("/api/v0.1/events/e1/displays").json()
    assert d["messages"] is True
    assert c.put("/api/v0.1/events/e1/displays/d1/view", json={"view": None}).json() == {"view": None}


# --- Lot 7, TV screen (30 Oct 2026) ------------------------------------------

def test_screen_name_cleaned():
    assert tv.clean_name("  TV  Samsung\n· 1920×1080 ") == "TV Samsung · 1920×1080"
    assert tv.clean_name(None) == "" and tv.clean_name(3) == ""
    assert len(tv.clean_name("x" * 300)) == tv.NAME_MAX


def test_theme_of_the_view_and_auto_mode():
    assert tv.clean_view(VIEW, now=1)["theme"] == "auto"
    assert tv.clean_view({**VIEW, "theme": "day"}, now=1)["theme"] == "day"
    with pytest.raises(ValueError):
        tv.clean_view({**VIEW, "theme": "blue"})
    w = {"sunrise": "2026-10-30T06:20:00+00:00", "sunset": "2026-10-30T16:30:00+00:00"}
    noon, evening = datetime(2026, 10, 30, 12, tzinfo=UTC), datetime(2026, 10, 30, 18, tzinfo=UTC)
    assert not tv.theme_night("auto", w, noon) and tv.theme_night("auto", w, evening)
    assert tv.theme_night("auto", None, noon)                    # no sun times: former dark look
    assert not tv.theme_night("day", w, evening) and tv.theme_night("night", w, noon)


def test_tv_page_lot7():
    t = api._TV
    for needle in ("#app.day{", "#app.nomsg{grid-template-columns:1fr}", "invalidateSize", ".night .leaflet-tile-pane",
                   "function tvName()", "name:tvName()", "id=\"ident\"", "$('ident').textContent=i.name", "TV Samsung"):
        assert needle in t
    assert "$('msgBox').style.display" not in t
    assert "#legend{display:none;position:absolute;left:10px;bottom:10px" in t
    a = api._APPROVE
    assert 'id="n"' in a and "/api/v0.1/tv/code/" in a and "name:document.getElementById('n').value" in a


def test_name_rename_identify_and_theme_routes(sos_client, monkeypatch):
    c, who, db = sos_client
    monkeypatch.setattr(tv, "store", tv.Store())
    monkeypatch.setattr(tv, "seen", {})
    p = c.post("/api/v0.1/tv/pair", json={"name": " TV <b>LG</b> · 3840×2160 "}).json()
    assert c.post("/api/v0.1/tv/pair").status_code == 200          # former TV page, no body
    assert c.get(f"/api/v0.1/tv/code/{p['code'].lower()}").json()["name"] == "TV <b>LG</b> · 3840×2160"
    assert c.get("/api/v0.1/tv/code/ZZZZZZ").status_code == 404
    assert c.post("/api/v0.1/tv/approve", json={"code": p["code"], "event_id": "e1", "name": "Salle PCO"}).status_code == 200
    tok = c.get(f"/api/v0.1/tv/pair/{p['pair_id']}").json()["token"]
    d = next(x for x in c.get("/api/v0.1/events/e1/displays").json() if x["code"] == p["code"])
    assert d["name"] == "Salle PCO" and d["last_seen"] is None
    url = f"/api/v0.1/events/e1/displays/{d['id']}"
    assert tok
    disp = lambda: next(x for x in api._displays(db) if x["id"] == d["id"])  # noqa: E731
    opts = lambda: {"options": tv.display_options(disp().get("view")), "weather": None}  # noqa: E731
    s = api._screen(disp(), opts())
    assert s["name"] == "Salle PCO" and s["identify"] is None and s["night"] is True and opts()["options"]["theme"] == "auto"
    tv.seen[d["id"]] = 1234.0
    d = next(x for x in c.get("/api/v0.1/events/e1/displays").json() if x["id"] == d["id"])
    assert d["last_seen"] == 1234.0
    assert c.put(url + "/name", json={"name": "  Mur   d'images "}).json() == {"name": "Mur d'images"}
    assert c.put(url + "/name", json={"name": "   "}).status_code == 422
    assert c.put("/api/v0.1/events/e1/displays/zz/name", json={"name": "x"}).status_code == 404
    assert c.post(url + "/identify").json() == {"identify": True, "seconds": 5}
    s = api._screen(disp(), opts())
    assert api._screen(disp(), opts(), now=disp()["identify"] + 20)["identify"] is None
    assert s["identify"]["name"] == "Mur d'images" and s["identify"]["seconds"] == 5
    assert c.post("/api/v0.1/events/e1/displays/zz/identify").status_code == 404
    c.put(url + "/view", json={"view": {**VIEW, "theme": "day"}})
    assert api._screen(disp(), opts())["night"] is False
    c.put(url + "/view", json={"view": {**VIEW, "theme": "night"}})
    assert api._screen(disp(), opts())["night"] is True
    # Pairing without a chosen name keeps the default name of the TV.
    q = c.post("/api/v0.1/tv/pair", json={"name": "TV Sony · 1920×1080"}).json()
    c.post("/api/v0.1/tv/approve", json={"code": q["code"], "event_id": "e1"})
    c.get(f"/api/v0.1/tv/pair/{q['pair_id']}")
    assert any(x["name"] == "TV Sony · 1920×1080" for x in c.get("/api/v0.1/events/e1/displays").json())


# --- Lot 11, « Écrans » button of the map (30 Oct 2026) -----------------------

def test_screens_mine_and_live_view(sos_client, monkeypatch):
    c, who, db = sos_client
    monkeypatch.setattr(tv, "store", tv.Store())
    who["email"], who["roles"] = "op@x.fr", []
    p = c.post("/api/v0.1/tv/pair").json()
    c.post("/api/v0.1/tv/approve", json={"code": p["code"], "event_id": "e1", "name": "Salle PCO"})
    c.get(f"/api/v0.1/tv/pair/{p['pair_id']}")
    d = next(x for x in c.get("/api/v0.1/events/e1/displays").json() if x["code"] == p["code"])
    assert d["mine"] is True
    who["email"] = "autre@x.fr"
    d = next(x for x in c.get("/api/v0.1/events/e1/displays").json() if x["code"] == p["code"])
    assert d["mine"] is False
    who["email"] = None
    assert next(x for x in c.get("/api/v0.1/events/e1/displays").json() if x["code"] == p["code"])["mine"] is True
    # « Suivre ma vue »: one logbook line for the whole follow.
    url = f"/api/v0.1/events/e1/displays/{d['id']}/view"
    count = lambda: db.query(api.LogbookRow).filter(api.LogbookRow.summary.like("Vue suivie%")).count()  # noqa: E731
    for z in (12, 13, 14):
        assert c.put(url, json={"view": {**VIEW, "zoom": z}, "live": True}).status_code == 200
    assert count() == 1
    assert next(x for x in api._displays(db) if x["id"] == d["id"])["view"]["zoom"] == 14
    c.put(url, json={"view": None})
    c.put(url, json={"view": VIEW, "live": True})
    assert count() == 2


# --- Robust probable zone (card « Zone probable », 31 Oct 2026) ----------

def _towards(lat, lon, target, **kw):
    """Bearing from (lat, lon) towards target (lat, lon)."""
    dy = (target[0] - lat) * 110_540.0
    dx = (target[1] - lon) * 111_320.0 * tv.math.cos(tv.math.radians(lat))
    return _b(lat, lon, (tv.math.degrees(tv.math.atan2(dx, dy)) + 360) % 360, id=f"b{lat}{lon}", **kw)


TARGET = (48.80, 2.10)
GOOD = [_towards(48.75, 2.10, TARGET), _towards(48.80, 2.03, TARGET), _towards(48.75, 2.04, TARGET),
        _towards(48.85, 2.16, TARGET)]


def test_wrong_bearing_is_set_aside():
    wild = _towards(48.78, 2.20, (48.70, 2.05))  # crosses the others far from the target
    [z] = tv.probable_zones([*GOOD, wild])
    assert z["excluded"] == [wild["id"]] and z["bearings"] == 4
    assert abs(z["lat"] - 48.80) < 0.003 and abs(z["lon"] - 2.10) < 0.004
    assert z["radius_m"] < 400
    assert z["label"].endswith("· 4 relevés · 1 écarté")


def test_without_the_wrong_one_the_zone_is_the_same():
    [clean] = tv.probable_zones(GOOD)
    assert clean["excluded"] == [] and clean["label"].startswith("±") and clean["label"].endswith("· 4 relevés")


def test_set_aside_or_kept_by_hand():
    wild = _towards(48.78, 2.20, (48.70, 2.05), zone="in")
    [z] = tv.probable_zones([*GOOD, wild])
    assert z["excluded"] == [] and z["bearings"] == 5
    out = {**GOOD[0], "zone": "out"}
    [z] = tv.probable_zones([out, *GOOD[1:]])
    assert z["excluded"] == [out["id"]] and z["bearings"] == 3


def test_nearly_parallel_bearings_do_not_cross():
    [z] = tv.probable_zones([_b(48.7, 2.1, 10), _b(48.7, 2.12, 355)])
    assert z["crossings"] == [] and "lat" not in z


def test_rounded_labels():
    assert tv.round_distance(699) == "700 m" and tv.round_distance(1260) == "1,3 km" and tv.round_distance(12400) == "12 km"
    assert tv.zone_label(699, 3, 0) == "±700 m · 3 relevés"
    assert tv.zone_label(50, 2, 2) == "zone approximative · 2 relevés · 2 écartés"


def test_never_below_three_bearings_kept():
    """31 Oct 2026, 23 h 47: 2 bearings set aside out of 4 left a zone on a
    single crossing (« ±50 m · 2 relevés · 2 écartés »)."""
    wild1 = _towards(48.78, 2.20, (48.70, 2.05))
    wild2 = _towards(48.70, 2.15, (48.90, 2.20))
    [z] = tv.probable_zones([*GOOD[:2], wild1, wild2])
    assert z["bearings"] >= 3 and len(z["excluded"]) <= 1
    [z] = tv.probable_zones([*GOOD[:2], wild1])
    assert z["excluded"] == [] and z["bearings"] == 3


def test_tv_marks_bearings_set_aside():
    """1 Nov 2026: the TV drew a bearing set aside like the others. The ids
    of `excluded` are those of the bearings sent, and the page uses them."""
    wild = _towards(48.78, 2.20, (48.70, 2.05))
    bearings = [*GOOD, wild]
    [z] = tv.probable_zones(bearings)
    assert set(z["excluded"]) <= {b["id"] for b in bearings}
    from sarcade.api import tv as tv_page
    assert "z.excluded" in tv_page._TV and "b.zone==='out'" in tv_page._TV
