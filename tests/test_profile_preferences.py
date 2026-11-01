"""Personal settings by email (favourites, preferred colour), 29 Oct 2026."""
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from sarcade.api import profile as api
from sarcade.profile.preferences import InvalidPreferences, apply_patch, normal_email


def test_patch_keeps_known_keys_and_drops_duplicates():
    p = apply_patch(None, {"favourites": ["F4JPO", "PCO", "F4JPO"]})
    assert p == {"favourites": ["F4JPO", "PCO"]}


def test_colour_is_remembered_with_its_history():
    p = apply_patch(None, {"color": 0xFF1E88E5})
    p = apply_patch(p, {"color": 0xFFE53935})
    p = apply_patch(p, {"color": 0xFF1E88E5})
    assert p["color"] == 0xFF1E88E5
    assert p["color_history"] == [0xFF1E88E5, 0xFFE53935]


def test_none_removes_a_setting():
    assert apply_patch({"color": 1, "favourites": ["A"]}, {"favourites": None}) == {"color": 1}


@pytest.mark.parametrize("patch,reason", [
    ({}, "empty_patch"),
    ({"theme": "dark"}, "unknown_setting"),
    ({"color": "red"}, "invalid_value"),
    ({"color": True}, "invalid_value"),
    ({"color": -1}, "invalid_value"),
    ({"favourites": "F4JPO"}, "invalid_value"),
    ({"favourites": [""]}, "invalid_value"),
    ({"favourites": ["x" * 65]}, "invalid_value"),
    ({"favourites": ["a"] * 501}, "invalid_value"),
])
def test_invalid_patches_are_refused(patch, reason):
    with pytest.raises(InvalidPreferences, match=reason):
        apply_patch(None, patch)


def test_email_is_normalised():
    assert normal_email("  Steph@Example.ORG ") == "steph@example.org"


class _Db:
    def __init__(self):
        self.rows = {}

    def get(self, _model, key):
        return self.rows.get(key)

    def add(self, row):
        self.rows[row.email] = row

    def commit(self):
        pass


def _request(email):
    access = SimpleNamespace(email=email) if email else None
    return SimpleNamespace(state=SimpleNamespace(access=access) if access else SimpleNamespace())


def test_without_checked_email_settings_stay_on_the_device():
    db = _Db()
    assert api.read_preferences(_request(None), db)["available"] is False
    with pytest.raises(HTTPException) as exc:
        api.change_preferences(_request(None), {"color": 1}, db)
    assert exc.value.status_code == 409


def test_settings_follow_the_email_from_one_device_to_another():
    db = _Db()
    api.change_preferences(_request("Steph@Example.org"), {"favourites": ["F4JPO"]}, db)
    out = api.read_preferences(_request("steph@example.org"), db)
    assert out["available"] is True
    assert out["preferences"] == {"favourites": ["F4JPO"]}
    assert api.read_preferences(_request("other@example.org"), db)["preferences"] == {}


def test_rail_pins_and_operator_icon():
    out = apply_patch(None, {"rail_pins": ["files", "weather", "files"], "operator_icon": "car"})
    assert out == {"rail_pins": ["files", "weather"], "operator_icon": "car"}
    for bad in ({"rail_pins": ["a"] * 7}, {"rail_pins": ["Files!"]}, {"operator_icon": "rocket"}):
        try:
            apply_patch(None, bad)
        except InvalidPreferences:
            continue
        raise AssertionError(bad)
    assert apply_patch(out, {"operator_icon": None}) == {"rail_pins": ["files", "weather"]}
