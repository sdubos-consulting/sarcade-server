"""Lot 19, card « APRS : vrais symboles des stations sur la carte », TV
part (decision of the porteur, 1 Nov 2026, 22 h 13): SARCADE icons kept,
except the gateways in a diamond (symbol « ?& »): red diamond for LoRa (L&),
black for the others (I&, R&, &…), white overlay letter in the centre,
drawn in SVG by SARCADE (no image taken). Test: L&, I&, &, />."""
import json
import re
import shutil
import subprocess

import pytest

from sarcade.api import tv as api

PAGE = api._TV


def _js(name: str) -> str:
    return f"function {name}(" + PAGE.split(f"function {name}(", 1)[1].split("\nfunction ", 1)[0]


def test_symbol_sent_to_the_tv():
    assert "aprs_symbol" in api.STATE_POSITION_KEYS
    # Nothing more than the TV needs (no path, no via, no battery).
    assert set(api.STATE_POSITION_KEYS) == {"device_id", "lat", "lon", "time", "source", "callsign", "aprs_symbol"}
    row = {"device_id": "aprs:F4GVN-14", "lat": 48.9, "lon": 1.8, "time": "t", "source": "aprs",
           "callsign": "F4GVN-14", "aprs_symbol": "L&", "aprs_path": "WIDE1-1", "battery_pct": None}
    assert {k: v for k, v in row.items() if k in api.STATE_POSITION_KEYS}["aprs_symbol"] == "L&"


def test_gateway_drawn_in_svg_without_image():
    icon = _js("gwIcon")
    assert "className:'aprd'" in icon and "<svg" in icon and "<path d=\"M12 1L23 12L12 23L1 12Z\"" in icon
    assert "c==='L'?'#d50000':'#111'" in icon  # red for LoRa, black otherwise
    assert 'fill="#fff">' in icon  # white overlay letter
    assert "<img" not in icon and ".png" not in icon


def test_other_stations_keep_their_round():
    assert "gw!==null?L.marker(ll,{icon:gwIcon(gw),keyboard:false}):L.circleMarker(ll," in PAGE
    # A symbol that changes kind gives a new marker.
    assert "markers[k]._gw!==gw" in PAGE


def test_gateway_follows_the_opacity_of_the_aprs_layer():
    assert "if(m.setStyle){m.setStyle({opacity:op,fillOpacity:.9*op});m.bringToFront();}else m.setOpacity(op);" in PAGE


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_gateway_rule_and_icons_run():
    script = (_js("gateway") + "\n" + _js("gwIcon") + "\n"
              "var L={divIcon:function(o){return o;}};var out={};"
              "['L&','I&','R&','/&','\\\\&','1&','/>','/[','\\\\#','a&','&','',null,'L&x'].forEach(function(s){"
              "var g=gateway(s);out[String(s)]={g:g,html:g===null?null:gwIcon(g).html};});"
              "console.log(JSON.stringify(out));")
    out = json.loads(subprocess.run(["node", "-e", script], capture_output=True, text=True, check=True).stdout)
    assert out["L&"]["g"] == "L" and 'fill="#d50000"' in out["L&"]["html"] and ">L</text>" in out["L&"]["html"]
    for s, letter in (("I&", "I"), ("R&", "R"), ("1&", "1")):
        assert out[s]["g"] == letter and 'fill="#111"' in out[s]["html"] and f">{letter}</text>" in out[s]["html"]
    # « & » without letter (primary or secondary table): black diamond, no letter.
    for s in ("/&", "\\&"):
        assert 'fill="#111"' in out[s]["html"] and "<text" not in out[s]["html"]
    # Car, other symbols, lower case table, malformed: no diamond.
    for s in ("/>", "/[", "\\#", "a&", "&", "", "null", "L&x"):
        assert out[s]["g"] is None, s
    assert re.search(r"viewBox=\"0 0 24 24\"", out["L&"]["html"])
