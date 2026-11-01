"""ADS-B step 1 (OpenSky), module ADRASEC (1 Nov 2026)."""
import asyncio
import time

import httpx
import pytest

from sarcade.modules.adrasec.adsb import opensky, service


def state(icao="3c6444", callsign="SAMU75  ", lon=2.1, lat=48.8, alt=900.0, squawk="1000", category=8):
    #        0     1        2        3     4           5    6    7    8      9     10    11   12    13   14      15     16 17
    return [icao, callsign, "France", 1, 1761960000, lon, lat, alt, False, 51.4, 271.6, -1.2, None, 950.0, squawk, False, 0, category]


def test_parse_states_units_and_flags():
    a = opensky.parse_states({"states": [state(), state(icao="abc", lon=None)]})
    assert len(a) == 1, "no position, dropped"
    a = a[0]
    assert a["callsign"] == "SAMU75" and a["alt_m"] == 950 and a["alt_ft"] == 3117
    assert a["speed_kt"] == 100 and a["track_deg"] == 272 and a["rotorcraft"] is True
    assert a["emergency"] is None
    assert a["category"] == 8 and a["position_time"] == 1, "OpenSky time of the position"
    other = opensky.parse_states({"states": [[*state()[:3], None, *state()[4:17], None]]})[0]
    assert other["category"] is None and other["position_time"] == 1761960000, "no position time: last contact"
    assert opensky.parse_states({"states": [state()[:17]]})[0]["category"] is None
    assert opensky.parse_states({"states": [state(squawk="7700")]})[0]["emergency"] == "emergency"
    assert opensky.parse_states({"states": None}) == []


def test_bbox_is_clamped_to_one_credit():
    lamin, lomin, lamax, lomax = opensky.clamp_bbox(40, -5, 52, 12)
    assert lamax - lamin <= 5 and lomax - lomin <= 5
    assert (lamin + lamax) / 2 == 46 and (lomin + lomax) / 2 == 3.5


def test_watchers_expire_and_boxes_union():
    service.runtime.watchers.clear()
    service.watch("e", "A", (48.7, 1.9, 48.9, 2.2), now=0)
    service.watch("e", "B", (48.8, 2.0, 49.0, 2.4), now=0)
    box = service.watched_boxes(now=10)["e"]
    assert box == (48.5, 1.7, 49.2, 2.6)
    assert service.watched_boxes(now=service.WATCH_TTL_S + 1) == {}, "nobody shows the layer: no request"
    service.watch("e", "A", (48.7, 1.9, 48.9, 2.2), now=0)
    service.unwatch("e", "A")
    assert service.watched_boxes(now=1) == {}


def test_emergency_alerted_once_and_trails():
    service.runtime.alerted.clear(); service.runtime.trails.clear()
    a = opensky.parse_states({"states": [state(squawk="7600")]})
    assert len(service.new_emergencies("e", a)) == 1
    assert service.new_emergencies("e", a) == [], "once per aircraft and code"
    assert "panne radio (7600)" in service.emergency_summary(a[0])
    service.with_trails("e", a)
    b = opensky.parse_states({"states": [state(lon=2.2)]})
    assert service.with_trails("e", b)[0]["trail"] == [[48.8, 2.1], [48.8, 2.2]]
    assert service.with_trails("e", [])  == [] and service.runtime.trails["e"] == {}


def test_client_token_reuse_and_quota():
    calls = {"token": 0, "states": 0}

    def handler(request: httpx.Request):
        if request.url.host.startswith("auth."):
            calls["token"] += 1
            assert b"client_secret=s3" in request.content
            return httpx.Response(200, json={"access_token": f"t{calls['token']}", "expires_in": 1800})
        calls["states"] += 1
        assert request.headers["Authorization"] == "Bearer t1"
        assert request.url.params["extended"] == "1"
        if calls["states"] == 3:
            return httpx.Response(429, headers={"X-Rate-Limit-Retry-After-Seconds": "120"})
        return httpx.Response(200, json={"time": 1, "states": [state()]})

    client = opensky.OpenSkyClient("id", "s3", http=httpx.AsyncClient(transport=httpx.MockTransport(handler)))

    async def run():
        assert len(await client.states((48, 2, 49, 3))) == 1
        await client.states((48, 2, 49, 3))
        with pytest.raises(opensky.QuotaExceeded) as e:
            await client.states((48, 2, 49, 3))
        assert e.value.retry_after_s == 120

    asyncio.run(run())
    assert calls["token"] == 1, "token reused while valid"


def test_without_credentials_nothing_starts(monkeypatch):
    monkeypatch.delenv("SARCADE_OPENSKY_CLIENT_ID", raising=False)
    monkeypatch.delenv("SARCADE_OPENSKY_CLIENT_SECRET", raising=False)
    tasks = []
    service.start(tasks)
    assert tasks == [] and service.runtime.enabled is False


def test_interval_never_under_30_s(monkeypatch):
    monkeypatch.setenv("SARCADE_ADSB_INTERVAL_S", "5")
    assert service.interval_s() == 30
    monkeypatch.setenv("SARCADE_ADSB_INTERVAL_S", "45")
    assert service.interval_s() == 45


def test_track_keeps_30_minutes_and_broadcast_stays_short():
    service.runtime.trails.clear()
    for k in range(80):
        a = opensky.parse_states({"states": [state(lon=2.0 + k / 1000)]})
        out = service.with_trails("e", a, now=1000 + 30 * k)
    assert len(out[0]["trail"]) == service.TRAIL_POINTS
    whole = service.track("e", "3C6444")
    assert len(whole) == service.TRACK_S // 30 + 1 and whole[-1] == [48.8, 2.079]


def test_tv_counts_as_a_watcher_and_is_filtered():
    from sarcade.db.models import EventRow
    from sarcade import tv as tv_core
    service.runtime.watchers.clear()
    service.runtime.latest["e"] = {"time": "t", "aircraft": opensky.parse_states({"states": [state(), [*state(icao="abc")[:13], 11000.0, *state()[14:]]]})}
    event = EventRow(id="e", settings={"modules": ["adrasec"]})
    opts = tv_core.clean_display_options({"layers": {"aircraft": True}, "aircraft_max_ft": 10000})
    out = service.tv_state(event, {"id": "d1", "view": {"lat": 48.8, "lon": 2.1}}, opts)
    assert [a["icao24"] for a in out["aircraft"]] == ["3c6444"]
    assert "trail" not in out["aircraft"][0], "no trace on the TV"
    assert out["aircraft"][0]["category"] == 8 and out["aircraft_tracks"] == {}
    assert abs(out["aircraft_now"] - time.time()) < 5, "server clock for the estimated positions"
    assert "e" in service.watched_boxes()
    assert service.tv_state(event, {"id": "d1"}, tv_core.clean_display_options({})) == {}
    assert service.tv_state(EventRow(id="f", settings={"modules": []}), {"id": "d1"}, opts) == {}


def test_tv_shows_the_routes_of_the_followed_aircraft_only():
    """« Envoyer ma vue » with aircraft followed (1 Nov 2026, 11 h 34): their
    route (30 min) on the TV, no trace for the others."""
    from sarcade.db.models import EventRow
    from sarcade import tv as tv_core
    service.runtime.trails.clear()
    for k in range(3):
        a = opensky.parse_states({"states": [state(lon=2.0 + k / 100), state(icao="abc123", lon=2.5 + k / 100)]})
        service.runtime.latest["e"] = {"time": "t", "aircraft": service.with_trails("e", a, now=1000 + 30 * k)}
    event = EventRow(id="e", settings={"modules": ["adrasec"]})
    opts = tv_core.clean_display_options({"layers": {"aircraft": True}, "aircraft_followed": ["3c6444", "dddddd"]})
    out = service.tv_state(event, {"id": "d1"}, opts)
    assert list(out["aircraft_tracks"]) == ["3c6444"], "absent aircraft: no route"
    assert out["aircraft_tracks"]["3c6444"] == [[48.8, 2.0], [48.8, 2.01], [48.8, 2.02]]


def test_latest_and_watch_carry_the_server_clock():
    from sarcade.modules.adrasec import api_adsb
    service.runtime.latest["e"] = {"time": "t", "aircraft": []}
    assert abs(api_adsb._latest("e")["now"] - time.time()) < 5
    assert api_adsb._latest("none")["aircraft"] == []
