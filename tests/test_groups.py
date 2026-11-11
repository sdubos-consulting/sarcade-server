from datetime import UTC, datetime

import pytest

from sarcade.features.service import InvalidFeature
from sarcade.groups import service as g

EVENT, GID = "evt-1", "0192f6a0-0000-7000-8000-0000000000aa"
NOW = datetime(2026, 10, 10, 6, 0, tzinfo=UTC)


def payload(**overrides):
    p = {"id": GID, "event_id": EVENT, "name": " Recherche nord ", "mode": "discussion",
         "kind": "custom", "members": ["TEL-01", "TEL-02", "TEL-01"], "created_by": "TEL-01",
         "updated_by": "TEL-01", "updated_at": "2026-10-10T06:00:00Z"}
    p.update(overrides)
    return p


def test_valid_group_is_canonicalised():
    c = g.validate_upsert(payload(), event_id=EVENT, object_id=GID)
    assert c["name"] == "Recherche nord"
    assert c["members"] == ["TEL-01", "TEL-02"]
    assert c["managers"] == ["TEL-01"], "the creator manages the group"
    assert c["archived"] is False and c["updated_at"] == NOW


@pytest.mark.parametrize("overrides,reason", [
    ({"name": "  "}, "invalid_name"),
    ({"name": "x" * 81}, "invalid_name"),
    ({"mode": "broadcast"}, "invalid_mode"),
    ({"kind": "secret"}, "invalid_kind"),
    ({"members": "TEL-01"}, "invalid_members"),
    ({"members": [""]}, "invalid_members"),
    ({"color": True}, "invalid_color"),
    ({"archived": "no"}, "invalid_archived"),
    ({"event_id": "other"}, "id_mismatch"),
])
def test_invalid_groups_are_rejected(overrides, reason):
    with pytest.raises(InvalidFeature, match=reason):
        g.validate_upsert(payload(**overrides), event_id=EVENT, object_id=GID)


def test_pco_is_recognised_by_declared_id():
    assert g.is_pco("PCO") and g.is_pco("PCO-2")
    assert not g.is_pco("TEL-PCO") and not g.is_pco("")


def test_only_managers_and_pco_change_an_existing_group():
    stored = {"managers": ["TEL-01"]}
    assert g.can_manage(None, "TEL-09"), "anyone creates a group"
    assert g.can_manage(stored, "TEL-01") and g.can_manage(stored, "PCO")
    assert not g.can_manage(stored, "TEL-02")


def test_listen_only_groups_accept_only_designated_senders():
    group = {"mode": "listen_only", "senders": ["CHEF-1"], "managers": ["TEL-01"]}
    assert g.can_send(group, "PCO") and g.can_send(group, "CHEF-1") and g.can_send(group, "TEL-01")
    assert not g.can_send(group, "TEL-02")
    assert g.can_send({"mode": "discussion"}, "TEL-02")
    assert not g.can_send({"mode": "discussion", "archived": True}, "PCO")


def test_group_references_in_recipients():
    assert g.group_refs(["TEL-01", "group:abc", "group:def"]) == ["abc", "def"]
    assert g.group_refs(None) == []


def test_default_groups_match_the_validated_list():
    groups = g.default_groups(EVENT, NOW)
    # 28 Oct 2026: a new event has « Tous » and « Diffusion PCO » only.
    assert [x["name"] for x in groups] == ["Tous", "Diffusion PCO"]
    diffusion = groups[1]
    assert diffusion["mode"] == "listen_only" and diffusion["senders"] == ["PCO"]
    model = g.default_groups(EVENT, NOW, "adrasec")
    assert [x["name"] for x in model] == ["PCO", "Équipes terrain", "Transmissions", "Logistique"]
    assert len({x["id"] for x in groups + model}) == 6
    assert g.default_groups(EVENT, NOW)[0]["id"] == groups[0]["id"], "ids are stable"
    assert g.default_groups("evt-2", NOW)[0]["id"] != groups[0]["id"]


def test_team_group():
    team = g.team_group(EVENT, "team-1", "Équipe 1", NOW)
    assert team["kind"] == "team" and team["team_id"] == "team-1" and team["name"] == "Équipe 1"
    assert team["id"] == g.team_group_id(EVENT, "team-1")


def test_logbook_summary():
    assert g.logbook_summary("Logistique", "archive") == "Groupe de communication archivé : « Logistique »"
