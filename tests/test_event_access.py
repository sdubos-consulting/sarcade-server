# SPDX-License-Identifier: AGPL-3.0-only
# © SDUBOS Consulting et contributeurs SARCADE
"""Events and the directory, transition period, lists of 28 October
(lot 26)."""
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import BigInteger, create_engine, select
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from sarcade import accounts, licensing
from sarcade.accounts.caller import Caller
from sarcade.api import event_access
from sarcade.api.app import app
from sarcade.api.deps import get_db
from sarcade.db.base import Base
from sarcade.db.models import (AuthDeviceRow, CommGroupRow, EventInvitedGroupRow, EventRow, HabilitationRow,
                               LogbookRow, LoginCodeRow, OrgGroupGroupRow, OrgGroupRow, OrgGroupUserRow,
                               OrgInvitationRow, SecurityEventRow, ServerSettingRow, UserRow)
from sarcade.directory import DirectoryError, PUBLIC_ID, SERVER_ADMINS_ID, events as dir_events, rights as R, \
    service, transition
from sarcade.groups import service as comm_groups
from sarcade.licensing import core, issue
from sarcade.updates.service import set_value


@compiles(BigInteger, "sqlite")
def _bigint_sqlite(type_, compiler, **kw):
    return "INTEGER"


NOW = datetime(2026, 11, 3, 14, 0, tzinfo=UTC)
TABLES = (UserRow, LoginCodeRow, AuthDeviceRow, SecurityEventRow, ServerSettingRow, OrgGroupRow, OrgGroupUserRow,
          OrgGroupGroupRow, OrgInvitationRow, HabilitationRow, EventRow, EventInvitedGroupRow, LogbookRow,
          CommGroupRow)
ROOT_ACTOR = R.Actor(label="jeton-admin (.env)", token=True)
ROOT, KEY = Ed25519PrivateKey.generate(), Ed25519PrivateKey.generate()
ADMIN = {"Authorization": "Bearer t0k"}


def group(db, gid, short, parent=None):
    db.add(OrgGroupRow(id=gid, short_name=short, name=short, parent_id=parent, system=gid in (PUBLIC_ID,
                       SERVER_ADMINS_ID), created_at=NOW, updated_at=NOW))
    if parent:
        db.add(OrgGroupGroupRow(group_id=parent, member_id=gid, added_at=NOW))


@pytest.fixture
def db(monkeypatch, tmp_path):
    """The state left by the migration 0031."""
    for k in ("SARCADE_ADMIN_EMAILS", "SARCADE_SMTP_HOST"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("SARCADE_ADMIN_TOKEN", "t0k")
    monkeypatch.setenv("SARCADE_LICENCE_FILE", str(tmp_path / "none.json"))
    monkeypatch.setitem(core.ROOT_KEYS, "R-TEST", issue.public_b64(ROOT))
    licensing._state = None
    R.clear_cache()
    transition.reset_cache()
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine, tables=[t.__table__ for t in TABLES])
    s = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)()
    for gid, short, parent in ((PUBLIC_ID, "Public", None), (SERVER_ADMINS_ID, "Administrateurs", None),
                               ("fnrasec", "FNRASEC", None), ("adrasec-78", "ADRASEC 78", "fnrasec"),
                               ("pco-adrasec-78", "PCO ADRASEC 78", "adrasec-78"), ("vcmb", "VCMB", None),
                               ("lga", "LGA", None)):
        group(s, gid, short, parent)
    s.flush()
    for hid, gid, right, scope in (("server-admins-right", SERVER_ADMINS_ID, "server_admin", None),
                                   ("adrasec-78-module", "adrasec-78", "module.adrasec", None),
                                   ("pco-adrasec-78-events", "pco-adrasec-78", "event_organizer", "adrasec-78")):
        s.add(HabilitationRow(id=hid, group_id=gid, right=right, scope_group_id=scope, granted_at=NOW))
    set_value(s, "directory", {"default_organizer_group_id": "adrasec-78", "pco_group_id": "pco-adrasec-78"})
    s.add(EventRow(id="e78", name="Exercice 78", kind="exercise", status="active", created_at=NOW, version=0,
                   settings={"modules": ["adrasec"]}, organizer_group_id="adrasec-78"))
    s.add(EventRow(id="ecourse", name="Course VCMB", kind="race", status="active", created_at=NOW, version=0,
                   settings={"modules": ["race"]}, organizer_group_id="vcmb"))
    s.commit()
    yield s
    s.close()
    licensing._state = None
    transition.reset_cache()


@pytest.fixture
def client(db):
    app.dependency_overrides[get_db] = lambda: db
    yield TestClient(app)
    app.dependency_overrides.pop(get_db, None)


def member(db, gid, email, device_id="TEL"):
    u, _ = accounts.ensure_user(db, email, NOW, "test")
    if db.get(OrgGroupUserRow, (gid, u.id)) is None:
        db.add(OrgGroupUserRow(group_id=gid, user_id=u.id, added_at=NOW))
    R.bump(db)
    _, token = accounts.enroll(db, u, NOW, "email_code", device_id=device_id)
    db.commit()
    return {"Authorization": f"Bearer {token}"}


def end_transition(client):
    r = client.put("/api/v0.1/admin/transition", json={"until": "2026-01-01T00:00:00Z"}, headers=ADMIN)
    assert r.status_code == 200 and r.json()["active"] is False


def names(client, headers=None):
    return sorted(e["name"] for e in client.get("/api/v0.1/events", headers=headers or {}).json())


# --- Visibility ----------------------------------------------------------------------------------------

def test_events_visible_to_the_invited_groups_only(client, db):
    h78 = member(db, "adrasec-78", "op@adrasec78.fr")
    hv = member(db, "vcmb", "signaleur@vcmb.fr")
    assert names(client, h78) == ["Exercice 78"]
    assert names(client, hv) == ["Course VCMB"]
    assert client.get("/api/v0.1/events/e78", headers=hv).status_code == 404
    assert client.get("/api/v0.1/events/e78", headers=h78).json()["organizer_group_id"] == "adrasec-78"
    # ADRASEC 78 invited to the race of the VCMB (transmissions).
    db.add(EventInvitedGroupRow(event_id="ecourse", group_id="adrasec-78", invited_at=NOW))
    db.commit()
    assert names(client, h78) == ["Course VCMB", "Exercice 78"]


def test_nested_groups_see_the_events_of_their_parents(client, db):
    h = member(db, "pco-adrasec-78", "chef@adrasec78.fr")
    assert names(client, h) == ["Exercice 78"]


def test_device_without_account_sees_everything_during_the_transition_only(client, db):
    assert names(client) == ["Course VCMB", "Exercice 78"]
    end_transition(client)
    assert names(client) == []
    assert client.get("/api/v0.1/events/e78").status_code == 404
    db.add(EventInvitedGroupRow(event_id="e78", group_id=PUBLIC_ID, invited_at=NOW))
    db.commit()
    assert names(client) == ["Exercice 78"]


def test_server_administrator_sees_every_event(client, db):
    h = member(db, SERVER_ADMINS_ID, "stephane@sdubos.com")
    assert names(client, h) == ["Course VCMB", "Exercice 78"]


def test_websocket_of_an_invisible_event_is_closed(client, db):
    hv = member(db, "vcmb", "signaleur@vcmb.fr")
    from starlette.websockets import WebSocketDisconnect
    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect("/api/v0.1/events/e78/ws", headers=hv) as ws:
            ws.receive_json()
    assert exc.value.code == 1008
    with client.websocket_connect("/api/v0.1/events/ecourse/ws", headers=hv) as ws:
        ws.send_text("ping")
        assert ws.receive_json()["type"] == "pong"


# --- Organiser, PCO ----------------------------------------------------------------------------------------

def test_pco_of_the_organising_group_changes_the_invited_groups(client, db, monkeypatch):
    sent = []

    async def fake(event_id, payload):
        sent.append(payload)
    monkeypatch.setattr(event_access.manager, "broadcast", fake)
    h = member(db, "pco-adrasec-78", "chef@adrasec78.fr")
    r = client.put("/api/v0.1/events/e78/access", json={"invited_group_ids": ["vcmb", "lga"]}, headers=h)
    assert r.status_code == 200
    assert [g["short_name"] for g in r.json()["invited_groups"]] == ["VCMB", "LGA"] and r.json()["can_organize"]
    assert sent[-1]["type"] == "event.access.updated"
    r = client.put("/api/v0.1/events/e78/access", json={"invited_group_ids": ["lga"]}, headers=h)
    assert sent[-1]["data"]["revocation"] is True
    lines = db.scalars(select(LogbookRow.summary).where(LogbookRow.event_id == "e78")).all()
    assert "Groupes invités : VCMB, LGA" in lines and "Groupes retirés de l'événement : VCMB" in lines
    hl = member(db, "lga", "x@lga.fr")
    r = client.put("/api/v0.1/events/e78/access", json={"invited_group_ids": []}, headers=hl)
    assert r.status_code == 403 and r.json()["detail"] == "event_organizer_required"


def test_organiser_cannot_move_the_event_to_another_organisation(client, db):
    h = member(db, "pco-adrasec-78", "chef@adrasec78.fr")
    r = client.put("/api/v0.1/events/e78/access", json={"organizer_group_id": "vcmb"}, headers=h)
    assert r.status_code == 403 and r.json()["detail"] == "event_organizer_required"
    r = client.put("/api/v0.1/events/e78/access", json={"organizer_group_id": "public"}, headers=h)
    assert r.status_code == 422


def test_create_event_in_the_group_the_caller_organises(client, db):
    h = member(db, "pco-adrasec-78", "chef@adrasec78.fr")
    r = client.post("/api/v0.1/events", json={"name": "SATER", "kind": "sater", "invited_group_ids": ["lga"]},
                    headers=h)
    assert r.status_code == 201, r.text
    row = db.get(EventRow, r.json()["id"])
    assert row.organizer_group_id == "adrasec-78" and dir_events.invited(db, row.id) == ["lga"]
    r = client.post("/api/v0.1/events", json={"name": "x", "kind": "race", "organizer_group_id": "vcmb"}, headers=h)
    assert r.status_code == 403 and r.json()["detail"] == "event_organizer_required"
    hv = member(db, "vcmb", "v@vcmb.fr")
    assert client.post("/api/v0.1/events", json={"name": "x", "kind": "race"}, headers=hv).status_code == 403


def test_create_event_without_account_during_the_transition(client, db):
    r = client.post("/api/v0.1/events", json={"name": "Démo", "kind": "exercise"})
    assert r.status_code == 201 and db.get(EventRow, r.json()["id"]).organizer_group_id == "adrasec-78"
    end_transition(client)
    assert client.post("/api/v0.1/events", json={"name": "Démo", "kind": "exercise"}).status_code == 403


def test_pco_of_another_event_is_not_pco_here(client, db):
    service.grant(db, ROOT_ACTOR, "vcmb", {"right": "event_organizer"}, NOW)
    db.add(EventInvitedGroupRow(event_id="e78", group_id="vcmb", invited_at=NOW))
    db.commit()
    hv = member(db, "vcmb", "bureau@vcmb.fr")
    r = client.post("/api/v0.1/events/e78/close", json={"actor_id": "PCO-VCMB"}, headers=hv)
    assert r.status_code == 403 and r.json()["detail"] == "pco_required"
    assert client.post("/api/v0.1/events/ecourse/close", json={"actor_id": "PCO-VCMB"}, headers=hv).status_code == 200


def test_directory_groups_for_organisers(client, db):
    assert client.get("/api/v0.1/directory/groups").status_code == 401
    assert client.get("/api/v0.1/directory/groups", headers=member(db, "lga", "a@lga.fr")).status_code == 403
    h = member(db, "pco-adrasec-78", "chef@adrasec78.fr")
    rows = {g["short_name"]: g for g in client.get("/api/v0.1/directory/groups", headers=h).json()}
    assert rows["ADRASEC 78"]["can_organize"] and not rows["VCMB"]["can_organize"] and "LGA" in rows


# --- Communication group from an organisation group --------------------------------------------------------

def test_comm_group_copies_the_enrolled_devices_of_the_members(client, db):
    h = member(db, "pco-adrasec-78", "chef@adrasec78.fr", device_id="PCO-1")
    member(db, "adrasec-78", "op1@adrasec78.fr", device_id="TEL-01")
    member(db, "adrasec-78", "op2@adrasec78.fr", device_id="TEL-02")
    member(db, "vcmb", "v@vcmb.fr", device_id="TEL-V")
    r = client.post("/api/v0.1/events/e78/groups/from-directory", json={"group_id": "adrasec-78"}, headers=h)
    assert r.status_code == 201, r.text
    out = r.json()
    assert out["name"] == "ADRASEC 78" and set(out["members"]) == {"PCO-1", "TEL-01", "TEL-02"}
    assert out["people"] == 3 and out["source_org_group_id"] == "adrasec-78"
    stored = db.get(CommGroupRow, out["id"])
    assert comm_groups.validate_upsert(dict(stored.data, id=stored.id, event_id="e78", updated_by="x",
                                            updated_at="2026-11-03T14:00:00Z"), event_id="e78", object_id=stored.id)
    hv = member(db, "vcmb", "v2@vcmb.fr")
    assert client.post("/api/v0.1/events/ecourse/groups/from-directory", json={"group_id": "public"},
                       headers=hv).status_code == 403


# --- Transition ------------------------------------------------------------------------------------------

def test_transition_state_and_pco_by_name(client, db):
    assert client.get("/api/v0.1/transition").json() == {"active": True, "until": None, "pco_by_name": True,
                                                         "anonymous_rights": "legacy"}
    assert comm_groups.is_pco("PCO-PAUL")
    assert client.put("/api/v0.1/admin/transition", json={"until": "2026-12-31T23:00:00Z"}).status_code == 401
    r = client.put("/api/v0.1/admin/transition", json={"until": "2026-12-31T23:00:00Z"}, headers=ADMIN)
    assert r.json()["active"] is True and r.json()["until"] == "2026-12-31T23:00:00Z"
    end_transition(client)
    assert not comm_groups.is_pco("PCO-PAUL") and not comm_groups.is_pco("PCO")
    me = client.get("/api/v0.1/me").json()
    assert me["transition"]["active"] is False and me["transition"]["pco_by_name"] is False
    actions = db.scalars(select(SecurityEventRow.action).where(SecurityEventRow.category == "admin")).all()
    assert actions.count("transition_changed") == 2


def test_module_adrasec_checked_after_the_transition(client, db):
    op = SimpleNamespace(event_id="e78", object_type="map_feature", action="create", payload={"kind": "bearing"})
    other = SimpleNamespace(event_id="e78", object_type="map_feature", action="create", payload={"kind": "line"})
    member(db, "adrasec-78", "op@adrasec78.fr")
    caller = Caller(email="op@adrasec78.fr", user_id=accounts.user_by_email(db, "op@adrasec78.fr").id, via="device")
    req = SimpleNamespace(state=SimpleNamespace(caller=caller))
    assert event_access.SyncAccess(db, req).refusal(op) is None  # transition: as before
    transition.set_until(db, NOW - timedelta(days=1), "test")
    db.commit()
    assert event_access.SyncAccess(db, req).refusal(op) == "module ADRASEC non autorisé pour ce compte"
    assert event_access.SyncAccess(db, req).refusal(other) is None
    doc = issue.make_licence(KEY, issue.make_key_certificate(ROOT, "R-TEST", KEY, "K-1", date(2026, 1, 1),
                                                            date(2029, 1, 1)),
                             "FNRASEC", "association", ["adrasec"], date(2026, 11, 1), date(2027, 11, 1))
    from sarcade.directory import licences
    licences.install(db, "fnrasec", doc)
    R.bump(db)
    db.commit()
    assert event_access.SyncAccess(db, req).refusal(op) is None
    anonymous = SimpleNamespace(state=SimpleNamespace(caller=Caller()))
    assert event_access.SyncAccess(db, anonymous).refusal(op) == "événement non visible pour ce compte"


# --- Groups and events; lists of 28 October ---------------------------------------------------------------

def test_group_organising_events_is_not_deleted(db):
    with pytest.raises(DirectoryError, match="group_has_events"):
        service.delete_group(db, ROOT_ACTOR, "vcmb", NOW)


def test_lists_of_the_console_write_in_the_groups(client, db):
    h = member(db, SERVER_ADMINS_ID, "stephane@sdubos.com")
    member(db, "pco-adrasec-78", "pco1@adrasec78.fr")
    r = client.get("/api/v0.1/admin/roles", headers=h).json()
    assert r["admins"] == ["stephane@sdubos.com"] and r["pco"] == ["pco1@adrasec78.fr"]
    r = client.put("/api/v0.1/admin/roles", json={"admins": ["stephane@sdubos.com", "second@x.fr"],
                                                  "pco": ["pco2@adrasec78.fr"]}, headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["admins"] == ["second@x.fr", "stephane@sdubos.com"] and r.json()["pco"] == ["pco2@adrasec78.fr"]
    r = client.put("/api/v0.1/admin/roles", json={"admins": ["second@x.fr"], "pco": []}, headers=h)
    assert r.status_code == 422 and r.json()["detail"] == "last_admin"  # second@ has no account yet
    assert client.get("/api/v0.1/me", headers=h).json()["is_server_admin"] is True


def test_me_for_a_migrated_pco(client, db):
    h = member(db, "pco-adrasec-78", "chef@adrasec78.fr")
    me = client.get("/api/v0.1/me", headers=h).json()
    assert me["roles"] == ["pco"] and me["can_administer"] is True and me["is_server_admin"] is False
    assert me["admin_rights"] == [{"right": "event_organizer", "scope_group_id": "adrasec-78",
                                   "scope_name": "ADRASEC 78", "until": None}]
    assert me["module_rights"] == [{"module": "adrasec", "licensed": False}]
    assert me["transition"]["active"] is True
