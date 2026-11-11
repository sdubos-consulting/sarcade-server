"""SOS on the map of the TV (card « SOS : voir sur la carte où est
l'opérateur en détresse », choices validated 1 Nov 2026, 12 h 04; lot 17):
operator, last known position and its age for each open distress, whatever
the layers of the view."""
from datetime import UTC, datetime, timedelta

from sarcade import tv
from sarcade.api import tv as api

NOW = datetime(2026, 11, 1, 14, 0, tzinfo=UTC)


def _m(who, body, minutes_ago, priority="immediate", to=()):
    return {"sender_id": who, "body": body, "priority": priority, "recipient_ids": list(to),
            "created_at": NOW - timedelta(minutes=minutes_ago)}


def _p(who, lat, lon, minutes_ago, source="device", callsign=None):
    return {"device_id": who, "lat": lat, "lon": lon, "time": NOW - timedelta(minutes=minutes_ago),
            "source": source, "callsign": callsign}


def sos(who, pos="inconnue"):
    return f"🆘 DÉTRESSE — {who} — position {pos}"


def end(who):
    return f"Fin de détresse — {who}"


def test_distress_with_a_recent_position():
    [mark] = tv.distress_marks([_m("TEL-01", sos("TEL-01"), 1)], [_p("TEL-01", 48.8, 2.1, 2)], NOW)
    assert mark["device_id"] == "TEL-01" and mark["label"] == "TEL-01" and mark["known"] is True
    assert (mark["lat"], mark["lon"]) == (48.8, 2.1)
    assert mark["age_s"] == 120 and mark["stale"] is False
    assert mark["since"] == (NOW - timedelta(minutes=1)).isoformat()


def test_distress_without_any_position():
    [mark] = tv.distress_marks([_m("TEL-01", sos("TEL-01"), 1)], [_p("TEL-02", 48.8, 2.1, 1)], NOW)
    assert mark["known"] is False and "lat" not in mark and "stale" not in mark


def test_old_position_is_marked_stale():
    [mark] = tv.distress_marks([_m("TEL-01", sos("TEL-01"), 1)], [_p("TEL-01", 48.8, 2.1, 25)], NOW)
    assert mark["known"] is True and mark["stale"] is True and mark["age_s"] == 25 * 60
    # Exactly 10 minutes: still recent.
    [mark] = tv.distress_marks([_m("TEL-01", sos("TEL-01"), 1)], [_p("TEL-01", 48.8, 2.1, 10)], NOW)
    assert mark["stale"] is False


def test_position_written_in_the_sos_message():
    # No report reached the server: the position of the message counts.
    [mark] = tv.distress_marks([_m("TEL-01", sos("TEL-01", "48.81234, 2.05678"), 3)], [], NOW)
    assert mark["known"] and (mark["lat"], mark["lon"]) == (48.81234, 2.05678) and mark["age_s"] == 180
    # A more recent report wins; an older one loses to the message.
    msgs = [_m("TEL-01", sos("TEL-01", "48.81234, 2.05678"), 3)]
    assert tv.distress_marks(msgs, [_p("TEL-01", 48.9, 2.2, 1)], NOW)[0]["lat"] == 48.9
    assert tv.distress_marks(msgs, [_p("TEL-01", 48.9, 2.2, 30)], NOW)[0]["lat"] == 48.81234
    assert tv.body_position(sos("TEL-01")) is None
    assert tv.body_position("position 123.0, 2.0") is None


def test_end_of_distress_removes_the_mark():
    msgs = [_m("TEL-01", sos("TEL-01"), 3), _m("TEL-01", end("TEL-01"), 1, "urgent")]
    assert tv.distress_marks(msgs, [_p("TEL-01", 48.8, 2.1, 1)], NOW) == []
    # The end of another operator closes nothing; a private SOS is not shown.
    msgs = [_m("TEL-01", sos("TEL-01"), 3), _m("TEL-02", end("TEL-02"), 1, "urgent"),
            _m("TEL-03", sos("TEL-03"), 2, to=["user:PCO"])]
    assert [m["device_id"] for m in tv.distress_marks(msgs, [], NOW)] == ["TEL-01"]


def test_two_distresses_one_mark_each():
    msgs = [_m("TEL-02", sos("TEL-02"), 2), _m("TEL-01", sos("TEL-01"), 5)]
    marks = tv.distress_marks(msgs, [_p("TEL-01", 48.8, 2.1, 1), _p("TEL-02", 48.7, 2.0, 30)], NOW)
    assert [m["device_id"] for m in marks] == ["TEL-01", "TEL-02"]          # oldest SOS first
    assert [m["stale"] for m in marks] == [False, True]
    assert tv.alert_state(msgs, NOW)["sos"] == ["TEL-01", "TEL-02"]


def test_mark_stays_when_the_operator_is_hidden_by_the_view():
    """The halo depends on no filter nor layer: the operators layer off and
    offline operators hidden remove the position, not the mark."""
    positions = [_p("TEL-01", 48.8, 2.1, 30)]
    snap = tv.snapshot({"id": "e", "name": "E"}, positions, [])
    snap["distress"] = tv.distress_marks([_m("TEL-01", sos("TEL-01"), 1)], positions, NOW)
    for layers in ({"operators": False}, {"offline": False}):
        out = tv.apply_view(snap, {"layers": layers}, NOW)
        assert out["positions"] == [] and out["distress"][0]["lat"] == 48.8


def test_tv_page_draws_halo_label_and_edge_arrow():
    page = api._TV
    assert "createPane('sos').style.zIndex=680" in page            # above every other object
    assert "SOS · '+esc(d.label)" in page and "position ancienne" in page
    assert ".sosm.old .sosh{border-style:dashed" in page
    assert "function sosArrows()" in page and "map.distance(c,ll)" in page
    assert "' SOS'" in page and "position inconnue" in page
    # The view of the TV never follows a distress by itself.
    sos_js = page.split("function drawSos()")[1].split("// Top bar")[0]
    assert "setView" not in sos_js and "fitBounds" not in sos_js and "panTo" not in sos_js
