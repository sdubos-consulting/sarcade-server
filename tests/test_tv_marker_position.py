"""Lot 18 (1 Nov 2026, card « ADS-B : avions de la TV 5 à 10 km plus au sud
que sur l'application web »): the rule ``.ac{position:relative}`` of the TV
page replaced the absolute placement Leaflet gives every marker icon
(``.leaflet-marker-icon{position:absolute}``), so each aircraft was pushed
down by the height of every aircraft drawn before it. No rule of the page
may change the ``position`` of a marker icon class again."""
import re

from sarcade.api import tv


def _css(page: str) -> str:
    return "".join(re.findall(r"<style>(.*?)</style>", page, flags=re.S))


def _rules(page: str) -> list[tuple[str, str]]:
    """(selector, declarations) of every plain rule, @keyframes left out."""
    css = re.sub(r"/\*.*?\*/", "", _css(page), flags=re.S)
    out = []
    for sel, body in re.findall(r"([^{}]*)\{([^{}]*)\}", css):
        sel = sel.strip()
        if sel.startswith("@") or re.fullmatch(r"[\d.%]+|from|to", sel):
            continue
        out.append((sel, body))
    return out


def _icon_classes(page: str) -> set[str]:
    """Classes given to L.divIcon in the page script (the marker icon itself)."""
    return set(re.findall(r"L\.divIcon\(\{className:'([\w-]+)", page))


def test_the_page_draws_these_marker_icons():
    assert {"ac", "sosm", "zlab", "txt"} <= _icon_classes(tv._TV)


def test_no_rule_changes_the_position_of_a_marker_icon():
    icons = _icon_classes(tv._TV)
    bad = []
    for sel, body in _rules(tv._TV):
        if not re.search(r"(^|;)\s*position\s*:", body):
            continue
        for one in sel.split(","):
            # The element styled is the last compound of the selector.
            last = re.split(r"[\s>+~]+", one.strip())[-1]
            if set(re.findall(r"\.([\w-]+)", last)) & icons:
                bad.append(f"{one.strip()}{{{body}}}")
    assert bad == [], f"rule overriding the absolute placement of Leaflet: {bad}"


def test_the_check_catches_the_former_rule():
    page = tv._TV.replace(".ac svg{display:block}", ".ac{position:relative}.ac svg{display:block}", 1)
    icons = _icon_classes(page)
    hits = [s for s, b in _rules(page) if "position:" in b and re.split(r"[\s>+~]+", s)[-1] == ".ac"]
    assert "ac" in icons and hits == [".ac"]


def test_aircraft_label_stays_on_its_icon():
    """The label is absolute inside the icon (itself placed by Leaflet)."""
    page = tv._TV
    assert re.search(r"\.acl\{position:absolute", page)
    assert "className:'ac',iconSize:null,iconAnchor:[half,half]" in page
    assert '<span class="acl"' in page


def test_diagnostic_mode_only_with_diag_1():
    page = tv._TV
    assert "#diag{display:none;" in page and '<div id="diag"></div>' in page
    assert "const DIAG=/[?&]diag=1(&|$)/.test(location.search)" in page
    assert "function acDiag(){if(!DIAG)return;" in page
    for text in ("Horloge serveur − TV", "Dernière réception", "Indicatif", "âge", "avance"):
        assert text in page
    # The aircraft time (last reception) is read from the state.
    assert "acTime=s.aircraft_time||null" in page


def test_diagnostic_survives_the_reloads_of_the_page():
    """The only reload keeps the address (and ?diag=1); nothing navigates away."""
    page = tv._TV
    assert "location.reload()" in page
    assert not re.search(r"location\.(href|search|pathname)\s*=|location\.(assign|replace)\(", page)
