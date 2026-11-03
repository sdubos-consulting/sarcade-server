"""Rights from the email checked by Cloudflare Access (28 Oct 2026)."""
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from sarcade import roles
from sarcade.api import roles as api


class _Db:
    def __init__(self):
        self.rows = {}

    def get(self, _model, key):
        return self.rows.get(key)

    def add(self, row):
        self.rows[row.key] = row

    def commit(self):
        pass


def _req(email):
    st = SimpleNamespace(access=SimpleNamespace(email=email)) if email else SimpleNamespace()
    return SimpleNamespace(state=st, url=SimpleNamespace(path="/x"))


@pytest.fixture(autouse=True)
def _no_journal(monkeypatch):
    monkeypatch.setattr(api.journal, "record", lambda *a, **k: None)
    monkeypatch.delenv("SARCADE_ADMIN_EMAILS", raising=False)


def test_roles_from_the_lists():
    db = _Db()
    roles.save(db, ["Chef@Adrasec78.fr"], ["pco@adrasec78.fr"])
    assert roles.roles_of(db, "chef@adrasec78.fr") == ["admin", "pco"]
    assert roles.roles_of(db, "PCO@adrasec78.fr") == ["pco"]
    assert roles.roles_of(db, "op@adrasec78.fr") == []
    assert roles.roles_of(db, None) == []


def test_always_one_administrator():
    db = _Db()
    with pytest.raises(roles.InvalidRoles, match="last_admin"):
        roles.save(db, [], ["pco@x.fr"])


def test_env_admin_is_the_safety_net(monkeypatch):
    monkeypatch.setenv("SARCADE_ADMIN_EMAILS", "secours@x.fr")
    db = _Db()
    roles.save(db, [], [])
    assert roles.roles_of(db, "secours@x.fr") == ["admin", "pco"]


def test_bad_email_refused():
    with pytest.raises(roles.InvalidRoles, match="invalid_email"):
        roles.save(_Db(), ["pas un mail"], [])


def test_pco_required_only_with_a_checked_email():
    db = _Db()
    roles.save(db, ["a@x.fr"], ["p@x.fr"])
    assert api.require_pco(_req(None), db) is None          # local network: kept as before
    assert api.require_pco(_req("p@x.fr"), db) == "p@x.fr"
    with pytest.raises(HTTPException) as exc:
        api.require_pco(_req("op@x.fr"), db)
    assert exc.value.status_code == 403


def test_me_gives_the_roles():
    db = _Db()
    roles.save(db, ["a@x.fr"], [])
    got = api.me(_req("A@x.fr"), db)
    assert {k: got[k] for k in ("email", "checked", "roles")} == {"email": "a@x.fr", "checked": True,
                                                                  "roles": ["admin", "pco"]}
    assert api.me(_req(None), db)["checked"] is False
