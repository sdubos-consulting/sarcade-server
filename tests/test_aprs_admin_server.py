"""APRS-IS server set in the administration (decision of 26 Oct 2026)."""
import pytest

from sarcade.modules.adrasec.aprs import links
from sarcade.updates import policy


def test_default_is_empty_and_valid():
    assert policy.validate({})["aprs"] == {"is_host": None, "is_port": None}


def test_valid_server_kept_blank_cleared():
    out = policy.validate({"aprs": {"is_host": " euro.aprs2.net ", "is_port": 14580}})
    assert out["aprs"] == {"is_host": "euro.aprs2.net", "is_port": 14580}
    assert policy.validate({"aprs": {"is_host": "  "}})["aprs"]["is_host"] is None


@pytest.mark.parametrize("aprs, error", [
    ({"is_host": "bad host"}, "invalid_aprs_is_host"),
    ({"is_host": "-x.org"}, "invalid_aprs_is_host"),
    ({"is_host": 12}, "invalid_aprs_is_host"),
    ({"is_port": 0}, "invalid_aprs_is_port"),
    ({"is_port": 70000}, "invalid_aprs_is_port"),
    ({"is_port": True}, "invalid_aprs_is_port"),
    ("x", "invalid_aprs"),
])
def test_invalid_server_refused(aprs, error):
    with pytest.raises(policy.InvalidSettings, match=error):
        policy.validate({"aprs": aprs})


def test_server_precedence(monkeypatch):
    monkeypatch.delenv("SARCADE_APRS_IS_HOST", raising=False)
    monkeypatch.delenv("SARCADE_APRS_IS_PORT", raising=False)
    empty = {"aprs": {"is_host": None, "is_port": None}}
    assert links.is_server(empty) == ("euro.aprs2.net", 14580)
    monkeypatch.setenv("SARCADE_APRS_IS_HOST", "rotate.aprs2.net")
    monkeypatch.setenv("SARCADE_APRS_IS_PORT", "10152")
    assert links.is_server(empty) == ("rotate.aprs2.net", 10152)
    admin = {"aprs": {"is_host": "france.aprs2.net", "is_port": None}}
    assert links.is_server(admin) == ("france.aprs2.net", 10152)
