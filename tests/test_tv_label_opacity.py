"""Lot 19, card « Opacité des couches : l'appliquer aussi aux étiquettes »
(TV part, choix 1, 3 et 5): every label follows the opacity of its layer
(operators, APRS stations, high points and relays, texts of the drawings,
labels of the bearings' zones) and is never more opaque than its drawing."""
import re

from sarcade.api import tv

PAGE = tv._TV


def _function(name: str) -> str:
    return PAGE.split(f"function {name}(", 1)[1].split("\nfunction ", 1)[0]


def test_station_labels_follow_operators_and_aprs_opacity():
    body = PAGE.split("// One opacity per layer", 1)[1].split("look(s);", 1)[0]
    assert "const op=nz(m._aprs?o.aprs:o.ops,o.ops,1),t=m.getTooltip()" in body
    # Round stroke at op, fill at .9 × op, label at op: never above the round.
    assert "m.setStyle({opacity:op,fillOpacity:.9*op})" in body and "if(t)t.setOpacity(op)" in body


def test_reference_site_labels_follow_their_layer():
    body = _function("drawRefs")
    assert "op=nz(hp?o.high:o.relay,o.ref,1)" in body
    assert "{radius:5,color:c,weight:2,opacity:op," in body
    assert "className:'rlab',opacity:op}" in body


def test_drawing_labels_and_texts_follow_the_drawing():
    body = _function("drawFeatures")
    assert "o=nz(f.opacity,1)*op*c.a" in body
    # Label of a line / zone / circle: the opacity of the drawing itself.
    assert "{permanent:true,direction:'center',opacity:o}" in body
    # Text objects: the opacity of the text drawing.
    assert "'<span style=\"color:'+c.col+';opacity:'+o+'\">'" in body


def test_zone_labels_follow_the_bearings_layer():
    body = _function("drawBearings")
    assert "opacity:'+op+'" in body and "drawBearings(nz(o.brg,1))" in PAGE


def test_no_label_left_with_a_fixed_opacity():
    """Every permanent label of the page passes an opacity (Leaflet default
    0.9 would stay opaque on a faded layer), except the station labels whose
    opacity is set in the layer loop."""
    calls = re.findall(r"bindTooltip\([^;]*?\{permanent:true[^}]*\}", PAGE)
    assert calls
    for c in calls:
        assert "opacity:" in c or "label(p)" in c, c
