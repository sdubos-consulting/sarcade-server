# SPDX-License-Identifier: AGPL-3.0-only
# © SDUBOS Consulting et contributeurs SARCADE
"""Organisation groups and habilitations (lot 26)."""
from datetime import UTC, date, datetime, timedelta

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import BigInteger, create_engine, select
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from sarcade import accounts, licensing, roles
from sarcade.api import directory as directory_api
from sarcade.api.app import app
from sarcade.api.deps import get_db
from sarcade.db.base import Base
from sarcade.db.models import (AuthDeviceRow, EventInvitedGroupRow, EventRow, HabilitationRow, LoginCodeRow, OrgGroupGroupRow, OrgGroupRow,
                               OrgGroupUserRow, OrgInvitationRow, SecurityEventRow, ServerSettingRow, UserRow)
from sarcade.directory import DirectoryError, PUBLIC_ID, SERVER_ADMINS_ID, graph as graphs, rights as R, service
from sarcade.licensing import core, issue


@compiles(BigInteger, "sqlite")
def _bigint_sqlite(type_, compiler, **kw):
    return "INTEGER"


NOW = datetime(2026, 11, 3, 14, 0, tzinfo=UTC)
TABLES = (UserRow, LoginCodeRow, AuthDeviceRow, SecurityEventRow, ServerSettingRow, OrgGroupRow, OrgGroupUserRow,
          OrgGroupGroupRow, OrgInvitationRow, HabilitationRow, EventRow, EventInvitedGroupRow)
ROOT, KEY = Ed25519PrivateKey.generate(), Ed25519PrivateKey.generate()


def system_groups(db):
    for gid, short, name in ((PUBLIC_ID, "Public", "Public (tous les comptes)"),
                             (SERVER_ADMINS_ID, "Administrateurs", "Administrateurs du serveur")):
        db.add(OrgGroupRow(id=gid, short_name=short, name=name, system=True, created_at=NOW, updated_at=NOW))
    db.add(HabilitationRow(id="server-admins-right", group_id=SERVER_ADMINS_ID, right="server_admin",
                           granted_at=NOW))
    db.commit()


@pytest.fixture
def db(monkeypatch, tmp_path):
    for k in ("SARCADE_ADMIN_TOKEN", "SARCADE_ADMIN_EMAILS", "SARCADE_SMTP_HOST"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("SARCADE_LICENCE_FILE", str(tmp_path / "none.json"))
    monkeypatch.setitem(core.ROOT_KEYS, "R-TEST", issue.public_b64(ROOT))
    licensing._state = None
    R.clear_cache()
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine, tables=[t.__table__ for t in TABLES])
    session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)()
    system_groups(session)
    yield session
    session.close()
    licensing._state = None


@pytest.fixture
def client(db):
    app.dependency_overrides[get_db] = lambda: db
    yield TestClient(app)
    app.dependency_overrides.pop(get_db, None)


@pytest.fixture
def broadcasts(monkeypatch):
    sent = []

    async def fake(payload):
        sent.append(payload)
    monkeypatch.setattr(directory_api, "broadcast_all", fake)
    return sent


ROOT_ACTOR = R.Actor(label="jeton-admin (.env)", token=True)


def user(db, email):
    u, _ = accounts.ensure_user(db, email, NOW, "test")
    db.commit()
    return u


def group(db, short, parent=None, actor=ROOT_ACTOR, member=True):
    out, _ = service.create_group(db, actor, {"short_name": short, "parent_id": parent, "member_of_parent": member},
                                  NOW)
    db.commit()
    return out["id"]


def add(db, gid, email, actor=ROOT_ACTOR):
    out, _ = service.add_email(db, actor, gid, email, NOW)
    db.commit()
    return out


def grant(db, gid, right, scope=None, until=None, actor=ROOT_ACTOR):
    out, _ = service.grant(db, actor, gid, {"right": right, "scope_group_id": scope, "until": until}, NOW)
    db.commit()
    return out


def actor_for(db, email):
    u = accounts.user_by_email(db, email)
    return R.Actor(label=email, rights=R.compute(db, u.id if u else None, email))


@pytest.fixture
def org(db):
    """FNRASEC › ADRASEC 78 › PCO ADRASEC 78, VCMB › Signaleurs VCMB."""
    ids = {}
    ids["fnrasec"] = group(db, "FNRASEC")
    ids["a78"] = group(db, "ADRASEC 78", ids["fnrasec"])
    ids["pco"] = group(db, "PCO ADRASEC 78", ids["a78"])
    ids["vcmb"] = group(db, "VCMB")
    ids["sig"] = group(db, "Signaleurs VCMB", ids["vcmb"])
    return ids


# --- Graph: cycles, depth, Public ------------------------------------------------------------

def test_subgroup_is_attached_and_member_by_default(db, org):
    g = graphs.load(db)
    assert g.parent[org["a78"]] == org["fnrasec"] and org["a78"] in g.contains[org["fnrasec"]]
    assert g.organization_of(org["pco"]) == org["fnrasec"]
    only_attached = group(db, "Bureau VCMB", org["vcmb"], member=False)
    g = graphs.load(db)
    assert g.parent[only_attached] == org["vcmb"] and only_attached not in g.contains.get(org["vcmb"], set())


def test_cycle_refused_with_the_path(db, org):
    service.add_group_member(db, ROOT_ACTOR, org["sig"], org["a78"], NOW)  # ADRASEC 78 invited by the VCMB
    db.commit()
    with pytest.raises(DirectoryError) as exc:
        service.add_group_member(db, ROOT_ACTOR, org["pco"], org["vcmb"], NOW)
    assert exc.value.status == 409 and exc.value.detail == "cycle"
    assert exc.value.extra["path"] == ["VCMB", "Signaleurs VCMB", "ADRASEC 78", "PCO ADRASEC 78"]
    assert exc.value.extra["message"] == ("Impossible : VCMB contient déjà Signaleurs VCMB, qui contient "
                                          "ADRASEC 78, qui contient PCO ADRASEC 78")
    with pytest.raises(DirectoryError, match="cycle"):
        service.add_group_member(db, ROOT_ACTOR, org["a78"], org["a78"], NOW)


def test_attachment_under_a_descendant_refused(db, org):
    with pytest.raises(DirectoryError) as exc:
        service.update_group(db, ROOT_ACTOR, org["fnrasec"], {"parent_id": org["pco"]}, NOW)
    assert exc.value.detail == "cycle" and exc.value.extra["path"] == ["FNRASEC", "ADRASEC 78", "PCO ADRASEC 78"]


def test_at_most_eight_levels(db):
    ids = [group(db, "N1")]
    for i in range(2, 9):
        ids.append(group(db, f"N{i}", ids[-1]))
    with pytest.raises(DirectoryError) as exc:
        group(db, "N9", ids[-1])
    assert exc.value.detail == "too_deep" and exc.value.extra["levels"] == 9
    db.rollback()
    # Same rule for the membership: a chain of 5 inside a chain of 4.
    other = [group(db, "M1")]
    for i in range(2, 5):
        other.append(group(db, f"M{i}", other[-1]))
    with pytest.raises(DirectoryError, match="too_deep"):
        service.add_group_member(db, ROOT_ACTOR, ids[4], other[0], NOW)


def test_public_neither_contains_nor_joins_and_has_no_right(db, org):
    with pytest.raises(DirectoryError, match="public_not_nested"):
        service.add_group_member(db, ROOT_ACTOR, PUBLIC_ID, org["a78"], NOW)
    with pytest.raises(DirectoryError, match="public_not_nested"):
        service.add_group_member(db, ROOT_ACTOR, org["a78"], PUBLIC_ID, NOW)
    with pytest.raises(DirectoryError, match="public_rights_refused"):
        service.grant(db, ROOT_ACTOR, PUBLIC_ID, {"right": "module.race"}, NOW)
    with pytest.raises(DirectoryError, match="public_has_no_member_list"):
        service.add_email(db, ROOT_ACTOR, PUBLIC_ID, "a@x.fr", NOW)
    with pytest.raises(DirectoryError, match="system_group"):
        service.delete_group(db, ROOT_ACTOR, PUBLIC_ID, NOW)


def test_short_name_unique(db, org):
    with pytest.raises(DirectoryError, match="short_name_taken"):
        group(db, "vcmb")


# --- Effective rights ----------------------------------------------------------------------------

def test_rights_are_inherited_by_membership_with_the_path(db, org):
    grant(db, org["fnrasec"], "module.adrasec")
    grant(db, org["pco"], "event_organizer", org["a78"])
    u = user(db, "chef@adrasec78.fr")
    add(db, org["pco"], "chef@adrasec78.fr")
    r = R.compute(db, u.id, u.email)
    assert set(r.reached) == {PUBLIC_ID, org["pco"], org["a78"], org["fnrasec"]}
    why = R.explain(db, r)
    texts = {x["right"]: x["path_text"] for x in why["rights"]}
    assert texts["module.adrasec"] == "Module ADRASEC ← FNRASEC ← ADRASEC 78 ← PCO ADRASEC 78 ← chef@adrasec78.fr"
    assert texts["event_organizer"] == "Organisateur d'événements ← PCO ADRASEC 78 ← chef@adrasec78.fr"
    assert "event_organizer" in [x.right for x in r.admin_grants()]
    assert roles.roles_of(db, "chef@adrasec78.fr") == ["pco"]


def test_module_needs_the_licence_of_the_organisation(db, org):
    grant(db, org["a78"], "module.adrasec")
    u = user(db, "op@adrasec78.fr")
    add(db, org["a78"], u.email)
    r = R.of(db, u.id, u.email)
    assert r.modules() == [] and r.module_habilitations() == {"adrasec"}
    doc = issue.make_licence(KEY, issue.make_key_certificate(ROOT, "R-TEST", KEY, "K-1", date(2026, 1, 1),
                                                            date(2029, 1, 1)),
                             "FNRASEC", "association", ["adrasec"], date(2026, 11, 1), date(2027, 11, 1))
    from sarcade.directory import licences
    assert licences.install(db, org["fnrasec"], doc).status == "valid"
    R.bump(db)
    db.commit()
    assert R.of(db, u.id, u.email).modules() == ["adrasec"]
    # The VCMB is not covered by the licence of the FNRASEC.
    grant(db, org["vcmb"], "module.adrasec")
    v = user(db, "v@vcmb.fr")
    add(db, org["vcmb"], v.email)
    assert R.of(db, v.id, v.email).modules() == []


def test_server_licence_counts_for_the_organisation_it_names(db, org, monkeypatch):
    grant(db, org["vcmb"], "module.race")
    u = user(db, "b@vcmb.fr")
    add(db, org["vcmb"], u.email)
    state = core.LicenceState("valid", modules={"race"}, licence={"organisation": "VCMB"})
    monkeypatch.setattr(licensing, "state", lambda: state)
    assert R.compute(db, u.id, u.email).modules() == ["race"]


def test_end_date_ends_the_right_and_the_cache_follows(db, org):
    u = user(db, "renfort@x.fr")
    add(db, org["sig"], u.email)
    grant(db, org["sig"], "event_organizer", org["sig"], until=NOW + timedelta(hours=2))
    r = R.of(db, u.id, u.email, NOW)
    assert [x.right for x in r.admin_grants()] == ["event_organizer"]
    assert R.of(db, u.id, u.email, NOW + timedelta(hours=1)) is r  # cached
    assert R.of(db, u.id, u.email, NOW + timedelta(hours=3)).admin_grants() == []


def test_cache_recomputed_after_a_change(db, org):
    u = user(db, "x@x.fr")
    add(db, org["a78"], u.email)
    assert R.of(db, u.id, u.email).admin_grants() == []
    grant(db, org["a78"], "auditor", org["a78"])
    assert [x.right for x in R.of(db, u.id, u.email).admin_grants()] == ["auditor"]


def test_device_without_account_has_public_only(db, org):
    r = R.of(db, None, None)
    assert set(r.reached) == {PUBLIC_ID} and r.grants == [] and r.modules() == []


def test_disabled_account_keeps_public_only(db, org):
    u = user(db, "x@x.fr")
    add(db, SERVER_ADMINS_ID, u.email)
    u.status = "disabled"
    db.commit()
    assert not R.compute(db, u.id, u.email).server_admin


def test_env_address_is_server_administrator(db, monkeypatch):
    monkeypatch.setenv("SARCADE_ADMIN_EMAILS", "secours@x.fr")
    why = R.explain(db, R.compute(db, None, "secours@x.fr"))
    assert why["server_admin"] and why["rights"][0]["path_text"] == \
        "Administrateur du serveur ← SARCADE_ADMIN_EMAILS (.env)"


# --- No elevation of rights -----------------------------------------------------------------------

def test_group_administrator_administers_the_attached_groups_only(db, org):
    grant(db, org["fnrasec"], "group_admin", org["fnrasec"])
    u = user(db, "nat@fnrasec.fr")
    add(db, org["fnrasec"], u.email)
    a = actor_for(db, u.email)
    g = graphs.load(db)
    assert R.can_admin_group(g, a, org["pco"]) and not R.can_admin_group(g, a, org["vcmb"])
    # ADRASEC 78 invited in the signallers of the VCMB: not administered by the FNRASEC.
    service.add_group_member(db, ROOT_ACTOR, org["sig"], org["a78"], NOW)
    db.commit()
    assert not R.can_admin_group(graphs.load(db), actor_for(db, u.email), org["sig"])
    assert R.visible_groups(graphs.load(db), a) == {org["fnrasec"], org["a78"], org["pco"]}


def test_no_elevation(db, org):
    grant(db, org["a78"], "group_admin", org["a78"])
    u = user(db, "chef@adrasec78.fr")
    add(db, org["a78"], u.email)
    a = actor_for(db, u.email)
    with pytest.raises(DirectoryError, match="no_elevation"):
        service.grant(db, a, org["pco"], {"right": "server_admin"}, NOW)
    with pytest.raises(DirectoryError, match="no_elevation"):
        service.grant(db, a, org["pco"], {"right": "module.race"}, NOW)
    with pytest.raises(DirectoryError, match="no_elevation"):
        service.grant(db, a, org["pco"], {"right": "event_organizer", "scope_group_id": org["fnrasec"]}, NOW)
    with pytest.raises(DirectoryError, match="no_elevation"):
        service.grant(db, a, org["pco"], {"right": "auditor"}, NOW)  # auditor of the server
    with pytest.raises(DirectoryError, match="group_admin_required"):
        service.grant(db, a, org["vcmb"], {"right": "member_manager"}, NOW)
    out, _ = service.grant(db, a, org["pco"], {"right": "event_organizer", "scope_group_id": org["a78"]}, NOW)
    assert out["scope_name"] == "ADRASEC 78"
    with pytest.raises(DirectoryError, match="server_admin_required"):
        service.create_group(db, a, {"short_name": "Nouvelle organisation"}, NOW)
    sub, _ = service.create_group(db, a, {"short_name": "Transmissions 78", "parent_id": org["a78"]}, NOW)
    assert sub["parent_id"] == org["a78"]


def test_member_manager_manages_addresses_only(db, org):
    grant(db, org["a78"], "member_manager", org["a78"])
    u = user(db, "sec@adrasec78.fr")
    add(db, org["a78"], u.email)
    a = actor_for(db, u.email)
    assert service.add_email(db, a, org["a78"], "new@x.fr", NOW)[0]["status"] == "invited"
    with pytest.raises(DirectoryError, match="member_manager_required"):
        service.add_email(db, a, org["pco"], "new@x.fr", NOW)
    with pytest.raises(DirectoryError, match="group_admin_required"):
        service.add_group_member(db, a, org["a78"], org["vcmb"], NOW)
    with pytest.raises(DirectoryError, match="group_admin_required"):
        service.update_group(db, a, org["a78"], {"name": "x"}, NOW)


def test_scope_rules(db, org):
    with pytest.raises(DirectoryError, match="scope_not_allowed"):
        service.grant(db, ROOT_ACTOR, org["a78"], {"right": "server_admin", "scope_group_id": org["a78"]}, NOW)
    with pytest.raises(DirectoryError, match="scope_not_allowed"):
        service.grant(db, ROOT_ACTOR, org["a78"], {"right": "module.race", "scope_group_id": org["a78"]}, NOW)
    with pytest.raises(DirectoryError, match="unknown_right"):
        service.grant(db, ROOT_ACTOR, org["a78"], {"right": "god"}, NOW)
    with pytest.raises(DirectoryError, match="until_in_past"):
        service.grant(db, ROOT_ACTOR, org["a78"], {"right": "module.race", "until": NOW - timedelta(days=1)}, NOW)
    out, _ = service.grant(db, ROOT_ACTOR, org["a78"], {"right": "member_manager"}, NOW)
    assert out["scope_group_id"] == org["a78"]  # default: the group itself
    with pytest.raises(DirectoryError, match="habilitation_exists"):
        service.grant(db, ROOT_ACTOR, org["a78"], {"right": "member_manager"}, NOW)


# --- Invitations and import ---------------------------------------------------------------------------

def test_invitation_becomes_a_membership_at_the_first_connection(db, org):
    assert add(db, org["a78"], "Futur@ADRASEC78.fr")["kind"] == "invitation"
    u = user(db, "futur@adrasec78.fr")
    assert db.get(OrgGroupUserRow, (org["a78"], u.id)) is not None
    assert db.scalars(select(OrgInvitationRow)).all() == []
    assert add(db, org["a78"], "futur@adrasec78.fr")["status"] == "already"


def test_csv_import(db, org):
    user(db, "deja@x.fr")
    text = "email\nDeja@x.fr\nnouveau@x.fr; autre@x.fr\npas-une-adresse\nnouveau@x.fr\n"
    out, _ = service.import_addresses(db, ROOT_ACTOR, org["a78"], text, NOW)
    assert out == {"added": 1, "invited": 2, "already": 0, "invalid": ["pas-une-adresse"]}
    out, _ = service.import_addresses(db, ROOT_ACTOR, org["a78"], text, NOW)
    assert out["already"] == 3 and out["added"] == 0


# --- Always one administrator ------------------------------------------------------------------------

def test_last_administrator_cannot_be_removed(db, org):
    a = user(db, "stephane@sdubos.com")
    add(db, SERVER_ADMINS_ID, a.email)
    with pytest.raises(DirectoryError, match="last_admin"):
        service.remove_user(db, ROOT_ACTOR, SERVER_ADMINS_ID, a.id, NOW)
    db.rollback()
    with pytest.raises(DirectoryError, match="last_admin"):
        service.revoke(db, ROOT_ACTOR, "server-admins-right", NOW)
    db.rollback()
    with pytest.raises(DirectoryError, match="last_admin"):
        service.set_user_status(db, ROOT_ACTOR, a.id, {"status": "disabled"}, NOW)
    db.rollback()
    with pytest.raises(DirectoryError, match="last_admin"):
        service.set_until(db, ROOT_ACTOR, "server-admins-right", NOW + timedelta(days=1), NOW)
    db.rollback()
    b = user(db, "second@sdubos.com")
    add(db, SERVER_ADMINS_ID, b.email)
    service.remove_user(db, ROOT_ACTOR, SERVER_ADMINS_ID, a.id, NOW)
    db.commit()
    assert R.active_admins(db) == {b.id}


def test_administrators_by_a_nested_group_count(db, org):
    a = user(db, "a@x.fr")
    add(db, org["a78"], a.email)
    service.add_group_member(db, ROOT_ACTOR, SERVER_ADMINS_ID, org["a78"], NOW)
    db.commit()
    assert R.active_admins(db) == {a.id}
    with pytest.raises(DirectoryError, match="last_admin"):
        service.remove_group_member(db, ROOT_ACTOR, SERVER_ADMINS_ID, org["a78"], NOW)


def test_group_with_subgroups_is_not_deleted(db, org):
    with pytest.raises(DirectoryError, match="group_has_subgroups"):
        service.delete_group(db, ROOT_ACTOR, org["a78"], NOW)
    service.delete_group(db, ROOT_ACTOR, org["sig"], NOW)
    db.commit()
    assert db.get(OrgGroupRow, org["sig"]) is None


# --- API -----------------------------------------------------------------------------------------------

ADMIN = {"Authorization": "Bearer t0k"}


@pytest.fixture
def token(monkeypatch):
    monkeypatch.setenv("SARCADE_ADMIN_TOKEN", "t0k")


def login(client, db, email):
    u = user(db, email)
    _, tok = accounts.enroll(db, u, NOW, "email_code", device_id="TEL")
    db.commit()
    return {"Authorization": f"Bearer {tok}"}


def test_api_groups_members_habilitations_and_why(client, db, token, broadcasts):
    r = client.post("/api/v0.1/admin/groups", json={"short_name": "FNRASEC", "name": "Fédération"}, headers=ADMIN)
    assert r.status_code == 201 and r.json()["organization"] is True
    fn = r.json()["id"]
    a78 = client.post("/api/v0.1/admin/groups", json={"short_name": "ADRASEC 78", "parent_id": fn},
                      headers=ADMIN).json()
    assert a78["member_of"] == [fn] and a78["organization_id"] == fn
    assert client.post(f"/api/v0.1/admin/groups/{fn}/habilitations", json={"right": "module.adrasec"},
                       headers=ADMIN).status_code == 201
    r = client.post(f"/api/v0.1/admin/groups/{a78['id']}/members", json={"email": "Paul@ADRASEC78.fr"}, headers=ADMIN)
    assert r.json() == {"kind": "invitation", "email": "paul@adrasec78.fr", "status": "invited"}
    h = login(client, db, "paul@adrasec78.fr")
    me = client.get("/api/v0.1/me", headers=h).json()
    assert {x["short_name"] for x in me["groups"]} == {"Public", "ADRASEC 78", "FNRASEC"}
    assert me["modules"] == [] and me["module_rights"] == [{"module": "adrasec", "licensed": False}]
    assert me["can_administer"] is False and me["is_server_admin"] is False
    why = client.get("/api/v0.1/admin/rights", params={"email": "paul@adrasec78.fr"}, headers=ADMIN).json()
    assert why["rights"][0]["path_text"] == "Module ADRASEC ← FNRASEC ← ADRASEC 78 ← paul@adrasec78.fr"
    assert why["rights"][0]["licensed"] is False and why["rights"][0]["organization_name"] == "FNRASEC"
    detail = client.get(f"/api/v0.1/admin/groups/{a78['id']}", headers=ADMIN).json()
    assert [u["email"] for u in detail["users"]] == ["paul@adrasec78.fr"] and detail["ancestors"][0]["id"] == fn
    assert any(b["type"] == "rights.changed" for b in broadcasts)


def test_api_cycle_answers_409_with_the_path_and_journal(client, db, token, org):
    client.post(f"/api/v0.1/admin/groups/{org['sig']}/members", json={"group_id": org["a78"]}, headers=ADMIN)
    r = client.post(f"/api/v0.1/admin/groups/{org['pco']}/members", json={"group_id": org["vcmb"]}, headers=ADMIN)
    assert r.status_code == 409
    assert r.json()["detail"]["error"] == "cycle" and r.json()["detail"]["path"][0] == "VCMB"
    refused = db.scalars(select(SecurityEventRow).where(SecurityEventRow.action == "directory_refused")).all()
    assert refused[-1].details["reason"] == "cycle"


def test_api_every_change_is_journaled_with_its_author(client, db, token, org):
    h = client.post(f"/api/v0.1/admin/groups/{org['a78']}/habilitations",
                    json={"right": "event_organizer", "until": "2027-01-01T00:00:00Z"}, headers=ADMIN).json()
    client.patch(f"/api/v0.1/admin/habilitations/{h['id']}", json={"until": None}, headers=ADMIN)
    client.delete(f"/api/v0.1/admin/habilitations/{h['id']}", headers=ADMIN)
    rows = db.scalars(select(SecurityEventRow).where(SecurityEventRow.category == "rights")).all()
    assert [r.action for r in rows][-3:] == ["habilitation_granted", "habilitation_until_changed",
                                             "habilitation_revoked"]
    assert {r.actor for r in rows[-3:]} == {"jeton-admin (.env)"}


def test_api_import_csv_as_text(client, db, token, org):
    r = client.post(f"/api/v0.1/admin/groups/{org['a78']}/members/import", content="a@x.fr\nb@x.fr\n",
                    headers={**ADMIN, "Content-Type": "text/csv"})
    assert r.json() == {"added": 0, "invited": 2, "already": 0, "invalid": []}
    r = client.post(f"/api/v0.1/admin/groups/{org['a78']}/members/import", json={"csv": "c@x.fr"}, headers=ADMIN)
    assert r.json()["invited"] == 1
    assert len(client.get("/api/v0.1/admin/invitations", headers=ADMIN).json()) == 3


def test_api_refused_without_administration_right(client, db, token, org):
    assert client.get("/api/v0.1/admin/groups").status_code == 401
    h = login(client, db, "simple@x.fr")
    r = client.get("/api/v0.1/admin/groups", headers=h)
    assert r.status_code == 403 and r.json()["detail"] == "admin_required"


def test_api_group_admin_sees_only_his_organisation(client, db, token, org):
    grant(db, org["vcmb"], "group_admin", org["vcmb"])
    add(db, org["vcmb"], "bureau@vcmb.fr")
    h = login(client, db, "bureau@vcmb.fr")
    names = [g["short_name"] for g in client.get("/api/v0.1/admin/groups", headers=h).json()]
    assert names == ["VCMB", "Signaleurs VCMB"]
    assert client.get(f"/api/v0.1/admin/groups/{org['a78']}", headers=h).status_code == 403
    assert client.get("/api/v0.1/admin/rights", params={"email": "bureau@vcmb.fr"}, headers=h).status_code == 200
    add(db, org["a78"], "op@adrasec78.fr")
    assert client.get("/api/v0.1/admin/rights", params={"email": "op@adrasec78.fr"}, headers=h).status_code == 403
    me = client.get("/api/v0.1/me", headers=h).json()
    assert me["can_administer"] is True and me["admin_rights"][0]["scope_name"] == "VCMB"


def test_api_last_admin_409(client, db, token, org):
    a = user(db, "stephane@sdubos.com")
    add(db, SERVER_ADMINS_ID, a.email)
    r = client.delete(f"/api/v0.1/admin/groups/{SERVER_ADMINS_ID}/members/users/{a.id}", headers=ADMIN)
    assert r.status_code == 409 and r.json()["detail"]["error"] == "last_admin"
    assert db.get(OrgGroupUserRow, (SERVER_ADMINS_ID, a.id)) is not None
    h = login(client, db, "stephane@sdubos.com")
    me = client.get("/api/v0.1/me", headers=h).json()
    assert me["is_server_admin"] is True and me["roles"] == ["admin", "pco"]
    assert client.get("/api/v0.1/admin/users", headers=h).status_code == 200


def test_api_organisation_licence(client, db, token, org):
    doc = issue.make_licence(KEY, issue.make_key_certificate(ROOT, "R-TEST", KEY, "K-1", date(2026, 1, 1),
                                                            date(2029, 1, 1)),
                             "FNRASEC", "association", ["adrasec"], date(2026, 11, 1), date(2027, 11, 1))
    assert client.put(f"/api/v0.1/admin/groups/{org['a78']}/licence", json=doc, headers=ADMIN).status_code == 422
    r = client.put(f"/api/v0.1/admin/groups/{org['fnrasec']}/licence", json=doc, headers=ADMIN)
    assert r.status_code == 200 and r.json()["modules"] == ["adrasec"]
    assert client.get(f"/api/v0.1/admin/groups/{org['fnrasec']}/licence", headers=ADMIN).json()["modules"] == \
        ["adrasec"]
    bad = dict(doc, signature="AAAA")
    r = client.put(f"/api/v0.1/admin/groups/{org['fnrasec']}/licence", json=bad, headers=ADMIN)
    assert r.status_code == 422 and r.json()["detail"]["error"] == "licence_refused"
    assert client.delete(f"/api/v0.1/admin/groups/{org['fnrasec']}/licence", headers=ADMIN).json() == {"removed": True}


def test_api_catalogue_lists_the_rights(client, token):
    rights = {r["right"]: r for r in client.get("/api/v0.1/admin/directory/catalogue", headers=ADMIN).json()["rights"]}
    assert set(rights) >= {"server_admin", "group_admin", "member_manager", "event_organizer", "auditor",
                           "module.adrasec", "module.race", "module.assist"}
    assert rights["auditor"]["scope"] == "either" and rights["module.race"]["module"] == "race"


def test_api_auditor_of_the_server_reads_the_journal(client, db, token, org):
    grant(db, org["a78"], "auditor")
    add(db, org["a78"], "audit@x.fr")
    h = login(client, db, "audit@x.fr")
    assert client.get("/api/v0.1/admin/security-journal", headers=h).status_code == 200
    r = client.post(f"/api/v0.1/admin/groups/{org['a78']}/members", json={"email": "z@x.fr"}, headers=h)
    assert r.status_code == 403


def test_api_disable_account(client, db, token, org):
    u = user(db, "x@x.fr")
    h = login(client, db, "x@x.fr")
    r = client.patch(f"/api/v0.1/admin/users/{u.id}", json={"status": "disabled"}, headers=ADMIN)
    assert r.json()["status"] == "disabled"
    assert client.get("/api/v0.1/me", headers=h).status_code == 401


def test_licence_modules_include_race_and_adrasec():
    assert {"race", "adrasec", "siem", "assist"} <= set(core.MODULES)
