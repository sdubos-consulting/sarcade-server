"""Scripts of the TV pages run on old TV browsers (card « TV Samsung : ni
QR code ni code », 31 Oct 2026): the Samsung Tizen browser rejected the
whole page script because of one nullish operator, leaving « ······ » and no
QR code. Syntax of 2017 only (esprima parses ES2017), and no feature of the
later years esprima would let through."""
import re
from html.parser import HTMLParser

import esprima
import pytest

from sarcade.api import tv
from sarcade import tv as tv_core

# Accepted by esprima but newer than Chromium 56 (Tizen 4, 2018 TVs).
FORBIDDEN = {
    "object spread": re.compile(r"\{\s*\.\.\."),
    "padStart/padEnd": re.compile(r"\.pad(Start|End)\("),
    "replaceAll": re.compile(r"\.replaceAll\("),
    "Object.fromEntries": re.compile(r"Object\.fromEntries\("),
    "flat/flatMap": re.compile(r"\.flat(Map)?\("),
    "catch without binding": re.compile(r"catch\s*\{"),
}


class _Scripts(HTMLParser):
    """Inline scripts of a page (those without src)."""

    def __init__(self):
        super().__init__()
        self.scripts: list[str] = []
        self._in = False

    def handle_starttag(self, tag, attrs):
        if tag == "script" and not dict(attrs).get("src"):
            self._in = True
            self.scripts.append("")

    def handle_endtag(self, tag):
        if tag == "script":
            self._in = False

    def handle_data(self, data):
        if self._in:
            self.scripts[-1] += data


def inline_scripts(page: str) -> list[str]:
    parser = _Scripts()
    parser.feed(page)
    return [js for js in parser.scripts if js.strip()]


@pytest.mark.parametrize("name", ["_TV", "_APPROVE"])
def test_tv_scripts_parse_as_es2017(name):
    page = getattr(tv, name)
    scripts = inline_scripts(page)
    assert scripts
    for js in scripts:
        esprima.parseScript(js)  # raises on ?? or ?. and later syntax
        for what, pattern in FORBIDDEN.items():
            assert not pattern.search(js), f"{name}: {what} not supported by old TV browsers"


def test_esprima_rejects_the_operators_that_broke_the_tv():
    for js in ("var x=a??b;", "var y=a?.b;"):
        with pytest.raises(esprima.Error):
            esprima.parseScript(js)


def test_silent_screen_replaced_by_a_message():
    assert "window.sarcadeStarted=true" in tv._TV
    assert "pas pu lancer SARCADE" in tv._TV


def test_libraries_served_by_sarcade_not_by_a_cdn():
    from fastapi.testclient import TestClient

    from sarcade.api.app import app

    for page in (tv._TV, tv._APPROVE):
        assert "unpkg.com" not in page and "cdnjs" not in page
    client = TestClient(app)
    for name, kind in (("qrcode.min.js", "javascript"), ("leaflet.js", "javascript"), ("leaflet.css", "css"),
                       ("images/marker-icon.png", "image/png")):
        r = client.get(f"/tv/static/{name}")
        assert r.status_code == 200 and kind in r.headers["content-type"], name
    assert client.get("/tv/static/../tv.py").status_code == 404
    assert client.get("/tv/static/qrcodejs-LICENSE").status_code == 404


def _css_rules(page: str) -> list[str]:
    css = "".join(re.findall(r"<style>(.*?)</style>", page, flags=re.S))
    return re.findall(r"[^{}]*\{[^{}]*\}", css)


@pytest.mark.parametrize("name", ["_TV", "_APPROVE"])
def test_tv_css_has_fallbacks_for_old_tv_browsers(name):
    """1 Nov 2026, 1 h 12: on the Samsung TV the pairing screen showed the
    logo at full size, a tiny code and no spacing (min(), flex gap)."""
    for rule in _css_rules(getattr(tv, name)):
        assert "inset:" not in rule, rule
        if "display:flex" in rule:
            assert not re.search(r"(^|[;{])\s*gap:", rule), f"flex gap (Chromium 84+): {rule}"
        for m in re.finditer(r"([\w-]+):(?:min|max|clamp)\(", rule):
            prop = m.group(1)
            before = rule[: m.start()]
            assert re.search(rf"(^|[;{{]){re.escape(prop)}:(?!(min|max|clamp)\()", before), \
                f"{prop}: no fallback before min()/max()/clamp() (Chromium 79+): {rule[:120]}"


def test_traces_options_and_drawing():
    o = tv_core.clean_display_options({"layers": {"traces": True}, "traces_window": 360, "traces_ids": ["TEL-01", "TEL-01"]})
    assert o["layers"]["traces"] is True and o["traces_window"] == 360 and o["traces_ids"] == ["TEL-01"]
    assert tv_core.clean_display_options({})["layers"]["traces"] is False
    assert tv_core.clean_display_options({"traces_window": 7, "traces_ids": ["A"]})["traces_window"] == 60
    assert "function drawTraces()" in tv._TV and "weight:5" in tv._TV


def test_exercise_zone_signage():
    """Maquette validée (1 Nov 2026): orange dashed exercise zone, label
    outside the circle with « EX », legend of the three symbols."""
    page = tv._TV
    assert "dashArray:z.exercise?'14 9':null" in page and '<span class="ex">EX</span>' in page
    assert "EXERCICE" not in page
    assert "Zone d’exercice" in page and "Relevé écarté" in page
