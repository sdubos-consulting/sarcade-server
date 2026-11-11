"""Communication groups: synchronisation (ADR-001), listing, default groups."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from sarcade.db.models import CommGroupRow, EventRow, LogbookRow, SyncOperationRow
from sarcade.features import service as features
from sarcade.groups import service as groups
from sarcade.sync.service import record_operation

from .deps import get_db
from .roles import require_pco

router = APIRouter(prefix="/api/v0.1")

_META = ("id", "event_id", "updated_at", "updated_by", "created_by")


def group_dict(row: CommGroupRow) -> dict:
    data = dict(row.data)
    data.update({
        "id": row.id, "event_id": row.event_id, "created_by": row.created_by,
        "updated_by": row.updated_by, "updated_at": features.iso(row.updated_at),
        "revision": row.revision, "deleted": row.deleted_at is not None,
    })
    return data


def _store(row: CommGroupRow, change: dict) -> None:
    row.data = {k: v for k, v in change.items() if k not in _META}
    row.updated_at = change["updated_at"]
    row.updated_by = change["updated_by"]
    row.revision = (row.revision or 0) + 1


def ensure_groups(db: Session, event_id: str, items: list[dict], now: datetime) -> list[CommGroupRow]:
    """Creates the given canonical groups when missing (default and team
    groups). Existing groups, even modified or deleted, are left untouched."""
    created = []
    for index, item in enumerate(items):
        if db.get(CommGroupRow, item["id"]) is not None:
            continue
        # Staggered by a microsecond so listings keep the template order.
        row = CommGroupRow(id=item["id"], event_id=event_id, created_by=item["created_by"],
                           created_at=now + timedelta(microseconds=index), revision=0)
        _store(row, item)
        db.add(row)
        created.append(row)
    db.flush()
    return created


def apply_group(db: Session, op, now: datetime) -> tuple[str, int, dict | None]:
    """Validates, journals and applies one group change (ADR-001)."""
    if op.action not in groups.ACTIONS:
        return "rejected", 0, None
    try:
        if op.action == "delete":
            change = features.validate_delete(op.payload, event_id=op.event_id, object_id=op.object_id)
        else:
            change = groups.validate_upsert(op.payload, event_id=op.event_id, object_id=op.object_id)
    except features.InvalidFeature:
        return "rejected", 0, None
    if db.get(EventRow, op.event_id) is None:
        return "rejected", 0, None
    row = db.get(CommGroupRow, op.object_id)
    if row is not None and row.event_id != op.event_id:
        return "rejected", 0, None
    if row is not None and not groups.can_manage(row.data, change["updated_by"]):
        return "rejected", 0, None
    change["updated_at"] = features.clamp_time(change["updated_at"], now)

    status, cursor = record_operation(
        db, event_id=op.event_id, operation_id=op.operation_id, object_id=op.object_id,
        object_type=op.object_type, action=op.action, payload=op.payload, client_time=op.client_time,
    )
    if status != "accepted":
        return status, cursor, None
    journal = db.scalar(select(SyncOperationRow).where(SyncOperationRow.seq == cursor))
    decision = features.decide(op.action, change, row)
    if decision.status != "accepted":
        journal.status = "conflict"
        return "conflict", cursor, None

    was_deleted = row is not None and row.deleted_at is not None
    was_archived = row is not None and bool((row.data or {}).get("archived"))
    if decision.tombstone:
        unknown = row is None
        if unknown:
            row = CommGroupRow(id=op.object_id, event_id=op.event_id, data={"name": "", "archived": False},
                               created_by=change["updated_by"], created_at=now, revision=0)
            db.add(row)
        row.deleted_at = change["updated_at"]
        row.updated_at = change["updated_at"]
        row.updated_by = change["updated_by"]
        row.revision = (row.revision or 0) + 1
        action = None if unknown or was_deleted else "delete"
    else:
        if row is None:
            row = CommGroupRow(id=op.object_id, event_id=op.event_id, created_by=change["created_by"],
                               created_at=now, revision=0)
            db.add(row)
            action = "create"
        elif was_deleted:
            action = "restore"
        elif change["archived"] != was_archived:
            action = "archive" if change["archived"] else "unarchive"
        else:
            action = "update"
        _store(row, change)
        row.deleted_at = None
    db.flush()
    result = group_dict(row)
    journal.payload = result
    if action:
        db.add(LogbookRow(event_id=op.event_id, kind="group", object_id=row.id,
                          actor_id=change["updated_by"], time=now,
                          summary=groups.logbook_summary((row.data or {}).get("name", ""), action)))
    kind = "group.deleted" if row.deleted_at is not None else "group.upserted"
    return "accepted", cursor, {"type": kind, "data": result}


def message_allowed(db: Session, event_id: str, sender_id: str, recipient_ids) -> bool:
    """Server-side check of listen-only groups (the client checks too)."""
    for gid in groups.group_refs(recipient_ids):
        row = db.get(CommGroupRow, gid)
        if row is None or row.event_id != event_id or row.deleted_at is not None:
            continue  # unknown here (created offline, not synced yet): accepted
        if not groups.can_send(row.data or {}, sender_id):
            return False
    return True


@router.get("/events/{event_id}/groups")
def list_groups(event_id: str, include_deleted: bool = Query(False), db: Session = Depends(get_db)):
    """Every group of the event, operators' groups included: the PCO sees all
    of them (decision of 10 Oct 2026)."""
    if db.get(EventRow, event_id) is None:
        raise HTTPException(status_code=404, detail="event_not_found")
    stmt = select(CommGroupRow).where(CommGroupRow.event_id == event_id)
    if not include_deleted:
        stmt = stmt.where(CommGroupRow.deleted_at.is_(None))
    rows = db.scalars(stmt.order_by(CommGroupRow.created_at, CommGroupRow.id)).all()
    return [group_dict(r) for r in rows]


@router.post("/events/{event_id}/groups/defaults")
def create_default_groups(event_id: str, template: str | None = None, db: Session = Depends(get_db),
                          _pco=Depends(require_pco)):
    """Creates the base channels (« Tous », « Diffusion PCO ») or, with
    ``template=adrasec``, the channels of the ADRASEC model (PCO, Équipes
    terrain, Transmissions, Logistique) when missing. Idempotent."""
    if db.get(EventRow, event_id) is None:
        raise HTTPException(status_code=404, detail="event_not_found")
    if template is not None and template not in groups.TEMPLATES:
        raise HTTPException(status_code=422, detail="unknown_template")
    now = datetime.now(UTC)
    created = ensure_groups(db, event_id, groups.default_groups(event_id, now, template), now)
    if created:
        db.add(LogbookRow(event_id=event_id, kind="group", object_id=None, actor_id="PCO", time=now,
                          summary="Canaux du modèle ADRASEC ajoutés" if template else "Canaux par défaut créés"))
    db.commit()
    return {"created": len(created)}
