"""Lot 18, card « TV : aucune échelle de distance » (choix 1 à 4): metric
scale bottom left, just above the legend of the bearings (never covered),
readable from afar, following the zoom of the TV (Leaflet control)."""
import re

from sarcade.api import tv


def _rule(page: str, selector: str) -> str:
    m = re.search(re.escape(selector) + r"\{([^{}]*)\}", page)
    assert m, selector
    return m.group(1)


def test_metric_scale_bottom_left():
    page = tv._TV
    assert page.count("L.control.scale(") == 1
    call = re.search(r"L\.control\.scale\((\{[^}]*\})\)", page).group(1)
    assert "imperial:false" in call and "position:'bottomleft'" in call
    assert "metric:false" not in call


def test_scale_always_above_the_legend():
    page = tv._TV
    # Placed again each time the legend changes (shown, hidden, grown), at start and on resize.
    assert "$('legend').style.display=keys.length?'block':'none';placeScale();}" in page
    assert "addTo(map);placeScale();map.on('resize',placeScale);" in page
    body = page.split("function placeScale(){", 1)[1].split("\n", 2)
    code = body[0] + body[1]
    assert ".leaflet-bottom.leaflet-left" in code
    assert "lg.style.display==='block'?10+lg.offsetHeight+6:10" in code
    # The legend keeps its place, bottom left at 10 px.
    legend = _rule(page, "#legend")
    assert "left:10px" in legend and "bottom:10px" in legend


def test_scale_drawn_as_the_web_application():
    """Lot 19, card « TV : échelle au même style que l'application web »
    (choix 1 à 4): white rounded label, dark text, thin black line with a
    small square end at each side (``_scaleBar`` of the application:
    0xF2FFFFFF background, 0xFF1F1F1F ink, radius 6, 11 px text, 2 px line,
    6 px ends), enlarged to 15 px with the same proportions."""
    page = tv._TV
    box = _rule(page, "#map .leaflet-control-scale")
    assert "background:rgba(255,255,255,.95)" in box and "border-radius:8px" in box
    line = _rule(page, "#map .leaflet-control-scale-line")
    assert "font-size:15px" in line and "color:#1f1f1f" in line
    assert "border:0" in line and "background:none" in line  # text above the line, as on the web
    ends = _rule(page, "#map .leaflet-control-scale-line:after")
    assert "border:3px solid #1f1f1f" in ends and "border-top:0" in ends and "height:8px" in ends
    # Former TV style gone (dark background, thick white line).
    assert "border:4px solid #fff" not in page


def test_scale_same_in_day_and_night():
    page = tv._TV
    assert not re.search(r"\.(day|night)[^{}]*leaflet-control-scale", page)
