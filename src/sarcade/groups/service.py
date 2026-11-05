"""Communication groups (note « Groupes de communication » in the vault).

Groups are synchronised objects with the ADR-001 rule (last writer wins per
object, tombstones), like map features. Validated decisions of 10 Oct 2026:
operators create their own groups, listen-only groups exist, ADRASEC default
groups are created when an event opens, and the PCO sees every group.

Until ADR-002 brings real roles, the PCO is recognised by its declared id:
``PCO`` or any id starting with ``PCO-``.
"""
from __future__ import annotations

from datetime import datetime
import uuid

from sarcade.features.service import InvalidFeature, parse_time

MODES = {"discussion", "listen_only"}
KINDS = {"all", "team", "default", "custom"}
ACTIONS = {"create", "update", "delete"}
GROUP_PREFIX = "group:"

MAX_NAME = 80
MAX_DESCRIPTION = 300
MAX_MEMBERS = 500
MAX_ID = 64

# Default ADRASEC groups, validated on 10 Oct 2026. ``roles`` describes who
# belongs to the group until real roles exist (ADR-002).
DEFAULT_GROUPS = (
    {"key": "tous", "name": "Tous", "kind": "all", "mode": "discussion", "color": 0xFF1565C0,
     "roles": ["*"], "description": "Messages généraux, tous les participants"},
    {"key": "diffusion-pco", "name": "Diffusion PCO", "kind": "default", "mode": "listen_only",
     "color": 0xFFC62828, "roles": ["*"], "senders": ["PCO"],
     "description": "Consignes, points de situation, alertes du PCO"},
    {"key": "pco", "name": "PCO", "kind": "default", "mode": "discussion", "color": 0xFF6A1B9A,
     "roles": ["pco"], "description": "Coordination interne au poste"},
    {"key": "equipes-terrain", "name": "Équipes terrain", "kind": "default", "mode": "discussion",
     "color": 0xFF2E7D32, "roles": ["chef_equipe", "terrain"], "description": "Coordination entre équipes"},
    {"key": "transmissions", "name": "Transmissions", "kind": "default", "mode": "discussion",
     "color": 0xFF1565C0, "roles": ["radio", "relais"], "description": "Plan de fréquences, relais, qualité des liaisons"},
    {"key": "logistique", "name": "Logistique", "kind": "default", "mode": "discussion",
     "color": 0xFFEF6C00, "roles": ["logistique"], "description": "Matériel, ravitaillement, véhicules, relèves"},
)


def is_pco(actor_id: str | None) -> bool:
    """PCO recognised by its name (``PCO``, ``PCO-…``): only during the
    transition period of the habilitations (lot 26, sarcade.directory.transition)."""
    from sarcade.directory import transition
    return bool(actor_id) and (actor_id == "PCO" or actor_id.startswith("PCO-")) and transition.active()


def default_group_id(event_id: str, key: str) -> str:
    """Stable id: every server and client computes the same one."""
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"sarcade:{event_id}:group:{key}"))


def team_group_id(event_id: str, team_id: str) -> str:
    return default_group_id(event_id, f"team:{team_id}")


def _ids(raw, field: str) -> list[str]:
    if raw is None:
        return []
    if not isinstance(raw, list) or len(raw) > MAX_MEMBERS:
        raise InvalidFeature(f"invalid_{field}")
    seen, result = set(), []
    for item in raw:
        if not isinstance(item, str) or not item or len(item) > MAX_ID:
            raise InvalidFeature(f"invalid_{field}")
        if item not in seen:
            seen.add(item)
            result.append(item)
    return result


def _text(value, field: str, maximum: int, required: bool = False) -> str:
    value = value or ""
    if not isinstance(value, str) or len(value.strip()) > maximum:
        raise InvalidFeature(f"invalid_{field}")
    if required and not value.strip():
        raise InvalidFeature(f"invalid_{field}")
    return value.strip()


def validate_upsert(payload: dict, *, event_id: str, object_id: str) -> dict:
    if not isinstance(payload, dict):
        raise InvalidFeature("invalid_payload")
    if payload.get("id") != object_id or payload.get("event_id") != event_id:
        raise InvalidFeature("id_mismatch")
    mode = payload.get("mode", "discussion")
    if mode not in MODES:
        raise InvalidFeature("invalid_mode")
    kind = payload.get("kind", "custom")
    if kind not in KINDS:
        raise InvalidFeature("invalid_kind")
    color = payload.get("color", 0xFF546E7A)
    if isinstance(color, bool) or not isinstance(color, int) or not 0 <= color <= 0xFFFFFFFF:
        raise InvalidFeature("invalid_color")
    archived = payload.get("archived", False)
    if not isinstance(archived, bool):
        raise InvalidFeature("invalid_archived")
    updated_by = payload.get("updated_by") or payload.get("created_by")
    if not isinstance(updated_by, str) or not updated_by or len(updated_by) > MAX_ID:
        raise InvalidFeature("invalid_updated_by")
    created_by = payload.get("created_by") if isinstance(payload.get("created_by"), str) else updated_by
    managers = _ids(payload.get("managers"), "managers") or [created_by[:MAX_ID]]
    return {
        "id": object_id, "event_id": event_id,
        "name": _text(payload.get("name"), "name", MAX_NAME, required=True),
        "description": _text(payload.get("description"), "description", MAX_DESCRIPTION),
        "color": color, "mode": mode, "kind": kind,
        "members": _ids(payload.get("members"), "members"),
        "roles": _ids(payload.get("roles"), "roles"),
        "senders": _ids(payload.get("senders"), "senders"),
        "managers": managers,
        "team_id": _text(payload.get("team_id"), "team_id", MAX_ID) or None,
        "radio_channel": _text(payload.get("radio_channel"), "radio_channel", 80),
        "archived": archived,
        "created_by": created_by[:MAX_ID],
        "updated_by": updated_by,
        "updated_at": parse_time(payload.get("updated_at")),
    }


def can_manage(stored_data: dict | None, actor_id: str) -> bool:
    """Creation is open to every operator; later changes are reserved to the
    group managers and the PCO."""
    if stored_data is None or is_pco(actor_id):
        return True
    return actor_id in (stored_data.get("managers") or [])


def can_send(group_data: dict, sender_id: str) -> bool:
    """Listen-only groups only accept their designated senders, their
    managers and the PCO. Archived groups accept nothing."""
    if group_data.get("archived"):
        return False
    if group_data.get("mode") != "listen_only":
        return True
    if is_pco(sender_id):
        return True
    return sender_id in (group_data.get("senders") or []) or sender_id in (group_data.get("managers") or [])


def group_refs(recipient_ids) -> list[str]:
    """Group ids targeted by a message (recipients written ``group:<id>``)."""
    return [r[len(GROUP_PREFIX):] for r in (recipient_ids or [])
            if isinstance(r, str) and r.startswith(GROUP_PREFIX)]


# Channels of every new event (decision of 28 Oct 2026): « Tous » and
# « Diffusion PCO »; the others come from a model added in one touch.
BASE_KEYS = ("tous", "diffusion-pco")
TEMPLATES = {"adrasec": ("pco", "equipes-terrain", "transmissions", "logistique")}


def default_groups(event_id: str, now: datetime, template: str | None = None) -> list[dict]:
    """Canonical data of the default channels of an event: the two base
    channels, or the channels of a model (``adrasec``)."""
    keys = BASE_KEYS if template is None else TEMPLATES[template]
    groups = []
    for template in (t for t in DEFAULT_GROUPS if t["key"] in keys):
        gid = default_group_id(event_id, template["key"])
        groups.append({
            "id": gid, "event_id": event_id, "name": template["name"],
            "description": template["description"], "color": template["color"],
            "mode": template["mode"], "kind": template["kind"], "members": [],
            "roles": list(template["roles"]), "senders": list(template.get("senders", [])),
            "managers": ["PCO"], "team_id": None, "radio_channel": "", "archived": False,
            "created_by": "PCO", "updated_by": "PCO", "updated_at": now,
        })
    return groups


def team_group(event_id: str, team_id: str, team_name: str, now: datetime) -> dict:
    return {
        "id": team_group_id(event_id, team_id), "event_id": event_id, "name": team_name,
        "description": "Groupe de l'équipe", "color": 0xFF00897B, "mode": "discussion",
        "kind": "team", "members": [], "roles": [], "senders": [], "managers": ["PCO"],
        "team_id": team_id, "radio_channel": "", "archived": False,
        "created_by": "PCO", "updated_by": "PCO", "updated_at": now,
    }


def logbook_summary(name: str, action: str) -> str:
    verb = {"create": "créé", "delete": "supprimé", "restore": "restauré",
            "archive": "archivé", "unarchive": "désarchivé"}.get(action, "modifié")
    return f"Groupe de communication {verb} : « {name} »"
