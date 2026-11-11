"""Lot 18, card « TV : « Vue de … » caché en bas à droite » (choix 1 à 3):
the sender of the view sits above the Leaflet / OpenStreetMap mention, never
under it; dark opaque background, white text of 15 px; text unchanged."""
import re

from sarcade.api import tv


def test_view_from_is_a_control_of_the_bottom_right_corner():
    """Leaflet stacks the bottom controls upwards: a control added after the
    attribution is drawn above it, whatever the height of the mention."""
    page = tv._TV
    assert "const fromCtl=L.control({position:'bottomright'});fromCtl.onAdd=function(){return $('from');};fromCtl.addTo(map);" in page
    # Added after the map (and its attribution control) is created.
    assert page.index("map=L.map('map'") < page.index("fromCtl.addTo(map)")
    assert "attributionControl:false" not in page


def test_view_from_no_longer_placed_under_the_mention():
    page = tv._TV
    rule = re.search(r"#map #from\{([^{}]*)\}", page).group(1)
    assert "position:absolute" not in rule and "bottom:10px" not in rule
    assert not re.search(r"(?<!#map )#from\{", page)
    assert "background:#08111c" in rule and "color:#fff" in rule and "font-size:15px" in rule


def test_view_from_text_unchanged():
    assert "$('from').textContent=v.from?'Vue de '+v.from:''" in tv._TV
