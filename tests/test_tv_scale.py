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


def test_scale_readable_from_afar():
    line = _rule(tv._TV, "#map .leaflet-control-scale-line")
    assert "font-size:15px" in line and "color:#fff" in line
    assert re.search(r"border:[34]px solid", line)
    assert "background:rgba(3,21,43,.88)" in line  # dark, semi-opaque, as the legend at night
