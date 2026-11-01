"""Lot 19, card « TV : étiquettes des stations plus jolies » (choix A, 1 Nov
2026, 21 h 48; compléments 4 et 5 confirmés à 22 h 27): dark rounded pill
with white bold text and a thin frame of the station colour, round of the
station framed in white, same style for operators and APRS stations, light
pill in day mode, label shifted when two labels touch."""
import re

from sarcade.api import tv


def _rule(page: str, selector: str) -> str:
    m = re.search(re.escape(selector) + r"\{([^{}]*)\}", page)
    assert m, selector
    return m.group(1)


def test_label_is_a_dark_rounded_pill():
    r = _rule(tv._TV, ".leaflet-tooltip.stl")
    assert "background:rgba(3,21,43,.82)" in r  # dark, semi-transparent, as the legend
    assert "color:#fff" in r and "font-weight:700" in r and "font-size:14px" in r
    assert "border-radius:7px" in r and "border:1.5px solid" in r
    # No arrow of the Leaflet tooltip.
    assert "display:none" in _rule(tv._TV, ".leaflet-tooltip.stl:before")


def test_frame_of_the_station_colour_for_operators_and_aprs():
    page = tv._TV
    assert "border-color:#3dd6f5" in _rule(page, ".leaflet-tooltip.stl.op")
    assert "border-color:#ffb020" in _rule(page, ".leaflet-tooltip.stl.ap")
    # Every station (operators, APRS mobile or fixed) gets the pill.
    assert page.count("className:'stl '+(aprs?'ap':'op')") == 1
    assert "direction:'right'}).addTo(map)" not in page  # no plain white label left


def test_round_of_the_station_framed_in_white():
    page = tv._TV
    assert "L.circleMarker(ll,{radius:aprs?6:8,color:'#fff',weight:2,fillColor:aprs?'#ffb020':'#3dd6f5',fillOpacity:.9})" in page


def test_day_mode_light_pill_dark_text():
    r = _rule(tv._TV, "#app.day .leaflet-tooltip.stl")
    assert "background:rgba(255,255,255,.88)" in r and "color:#0b1a2b" in r


def test_touching_labels_are_shifted_not_hidden_first():
    page = tv._TV
    body = page.split("function declutter(){", 1)[1].split("\n// ", 1)[0]
    # Tries in place, then one line down, then one line up; hidden only after.
    assert "[0,LBL_H,-LBL_H]" in body
    assert body.index("t.options.offset=L.point(0,dy)") > body.index("m.closeTooltip()")
    # No DOM read per station (performance with many stations).
    assert "offsetWidth" not in body and "getBoundingClientRect" not in body
    assert "markers[k]._lbl=label(p)" in page
