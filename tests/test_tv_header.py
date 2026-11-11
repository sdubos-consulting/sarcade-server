"""Lot 19, card « TV : température collée à « Connecté » dans l'en-tête »
(choix 1 à 3): the same space (28 px) between every element of the top bar
and a thin grey vertical line between the link and the weather, and between
the weather and the clock, in day and night."""
import re

from sarcade.api import tv

PAGE = tv._TV


def _rule(selector: str) -> str:
    m = re.search(r"(?:^|[}\n])" + re.escape(selector) + r"\{([^{}]*)\}", PAGE)
    assert m, selector
    return m.group(1)


def test_same_space_between_every_element():
    assert "header>*+*{margin-left:28px}" in PAGE and ".stats>*+*{margin-left:28px}" in PAGE
    # No element of the bar keeps its own smaller margin (the weather had 4 px).
    for sel in ("#wx", "#clock", "#sun"):
        body = _rule(sel)
        assert "margin-left" not in body or "margin-left:28px" in body, sel
    sep = _rule("#wx,#clock")
    # Line in the middle of the 28 px space.
    assert "margin-left:14px" in sep and "padding-left:14px" in sep


def test_thin_grey_separators_in_day_and_night():
    sep = _rule("#wx,#clock")
    assert "border-left:1px solid var(--sep)" in sep
    assert "#app{--sep:rgba(185,198,210,.35);" in PAGE
    assert "#app.day{--sep:rgba(44,62,82,.3);" in PAGE


def test_no_double_line_without_weather():
    assert "#wx:empty{display:none}" in PAGE


def test_order_of_the_bar():
    bar = PAGE.split("<header>", 1)[1].split("</header>", 1)[0]
    ids = re.findall(r'id="(nOps|nAprs|link|sos|wx|sun|clock)"', bar)
    assert ids == ["nOps", "nAprs", "link", "sos", "wx", "sun", "clock"]
