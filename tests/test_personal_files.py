"""« Personnel » folder: owner only, quota (28 Oct 2026)."""
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from sarcade.api import personal_files as api


def _req(email):
    st = SimpleNamespace(access=SimpleNamespace(email=email)) if email else SimpleNamespace()
    return SimpleNamespace(state=st)


def test_without_checked_email_no_personal_folder():
    with pytest.raises(HTTPException) as exc:
        api._owner(_req(None))
    assert exc.value.status_code == 409
    assert api._owner(_req(" Steph@X.fr ")) == "steph@x.fr"


def test_quota_default_and_setting(monkeypatch):
    monkeypatch.setattr(api, "get_value", lambda db, key, default: default)
    assert api.quota_bytes(None) == 100 * 1024 * 1024
    monkeypatch.setattr(api, "get_value", lambda db, key, default: 250)
    assert api.quota_bytes(None) == 250 * 1024 * 1024
    monkeypatch.setattr(api, "get_value", lambda db, key, default: "beaucoup")
    assert api.quota_bytes(None) == 100 * 1024 * 1024


def test_someone_else_s_file_is_not_found():
    row = SimpleNamespace(owner_email="a@x.fr")
    db = SimpleNamespace(get=lambda model, key: row)
    with pytest.raises(HTTPException) as exc:
        api._mine(db, "b@x.fr", "f1")
    assert exc.value.status_code == 404
    assert api._mine(db, "a@x.fr", "f1") is row
