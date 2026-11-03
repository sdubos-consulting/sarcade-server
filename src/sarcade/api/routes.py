"""Routes with waypoints: synchronisation, team notifications, listing, GPX."""
from __future__ import annotations

from datetime import UTC, datetime
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from sarcade.db.models import CommGroupRow, DeviceRow, EventRow, LogbookRow, MessageRow, RouteObjectRow, SyncOperationRow
from sarcade.events import order_status
from sarcade.features import service as features
from sarcade.groups import service as groups
from sarcade.routes import service as routes
from sarcade.sync.service import record_operation

from .deps import get_db

router = APIRouter(prefix="/api/v0.1")
_META = ("id", "event_id", "updated_at", "updated_by", "created_by", "base_point_order")
SYSTEM_SENDER = "SARCADE"
SYNC_TYPES = ("route", "route_waypoint", "route_passage", "road_closure", "itinerary", "move_order")


def object_dict(row: RouteObjectRow) -> dict:
    data = dict(row.data)
    data.update({"id": row.id, "event_id": row.event_id, "kind": row.kind, "created_by": row.created_by,
                 "updated_by": row.updated_by, "updated_at": features.iso(row.updated_at),
                 "revision": row.revision, "deleted": row.deleted_at is not None})
    return data


class Notices:
    """Changes of assigned routes collected during one sync batch: one
    message per route to its team, urgent, to acknowledge."""

    def __init__(self):
        self.changes: dict[str, list[str]] = {}
        self.order_conflicts: list[tuple[str, str, str]] = []
        # Messages of the move orders: (sender, recipients, priority, body, order id).
        self.messages: list[tuple[str, list[str], str, str, str]] = []
        # Statuses of operators set by their move orders (lot 27), by device.
        self.statuses: dict[str, dict] = {}

    def add(self, route_id: str, text: str) -> None:
        self.changes.setdefault(route_id, [])
        if text not in self.changes[route_id]:
            self.changes[route_id].append(text)


def _route(db: Session, route_id: str | None) -> RouteObjectRow | None:
    if not route_id:
        return None
    row = db.get(RouteObjectRow, route_id)
    return row if row is not None and row.kind == "route" else None


def apply_route_object(db: Session, op, now: datetime, notices: Notices,
                       pco: bool = False) -> tuple[str, int, dict | None]:
    """``pco``: the caller is PCO by the email checked by Cloudflare Access."""
    kind = op.object_type
    if op.action not in ("create", "update", "delete"):
        return "rejected", 0, None
    try:
        if op.action == "delete":
            change = features.validate_delete(op.payload, event_id=op.event_id, object_id=op.object_id)
        else:
            change = routes.VALIDATORS[kind](op.payload, event_id=op.event_id, object_id=op.object_id)
    except features.InvalidFeature:
        return "rejected", 0, None
    if db.get(EventRow, op.event_id) is None:
        return "rejected", 0, None
    row = db.get(RouteObjectRow, op.object_id)
    if row is not None and (row.event_id != op.event_id or row.kind != kind):
        return "rejected", 0, None

    # Rights: an assigned route and its waypoints are changed only by the
    # route author and the PCO. Passages are recorded by the team.
    actor = change["updated_by"]
    if kind == "road_closure" and not groups.is_pco(actor):
        return "rejected", 0, None  # roads are closed and reopened by the PCO
    if kind == "itinerary" and row is not None and actor not in (row.created_by, row.data.get("device_id")) \
            and not groups.is_pco(actor):
        return "rejected", 0, None
    if kind == "move_order" and not routes.move_order_allowed(
            dict(row.data) if row is not None else None, row.created_by if row is not None else None,
            change, actor, op.action, pco or groups.is_pco(actor),
            _same_person(db, op.event_id, actor, (row.data or {}).get("target_id") if row is not None else None)):
        return "rejected", 0, None
    if kind in ("road_closure", "itinerary", "move_order"):
        route_row = None
    elif kind == "route":
        if row is not None and not routes.can_edit(row.data, row.created_by, actor):
            return "rejected", 0, None
        route_row = row
    else:
        route_id = change.get("route_id") or (row.route_id if row is not None else None)
        route_row = _route(db, route_id)
        if kind == "route_waypoint" and route_row is not None and not routes.can_edit(
                route_row.data, route_row.created_by, actor):
            return "rejected", 0, None
    change["updated_at"] = features.clamp_time(change["updated_at"], now)

    status, cursor = record_operation(
        db, event_id=op.event_id, operation_id=op.operation_id, object_id=op.object_id,
        object_type=kind, action=op.action, payload=op.payload, client_time=op.client_time)
    if status != "accepted":
        return status, cursor, None
    journal = db.scalar(select(SyncOperationRow).where(SyncOperationRow.seq == cursor))
    decision = features.decide(op.action, change, row)
    if decision.status != "accepted":
        journal.status = "conflict"
        return "conflict", cursor, None

    before = dict(row.data) if row is not None else None
    was_deleted = row is not None and row.deleted_at is not None
    if row is None:
        row = RouteObjectRow(id=op.object_id, event_id=op.event_id, kind=kind,
                             created_by=change.get("created_by", actor), created_at=now, revision=0,
                             data={"name": ""})
        db.add(row)
    if decision.tombstone:
        row.deleted_at = change["updated_at"]
    else:
        row.data = {k: v for k, v in change.items() if k not in _META}
        if kind == "move_order":
            kept = before if not was_deleted else None
            row.data = order_status.keep_fields(kept, routes.keep_eta(kept, row.data))
            _follow_order_status(db, op.event_id, row, kept, actor, change["updated_at"], now, notices)
        row.route_id = row.id if kind == "route" else change.get("route_id")
        row.deleted_at = None
    row.updated_at, row.updated_by = change["updated_at"], actor
    row.revision = (row.revision or 0) + 1
    db.flush()
    result = object_dict(row)
    journal.payload = result
    _logbook_and_notices(db, op.event_id, kind, row, before, was_deleted, change, route_row, notices, now)
    suffix = "deleted" if row.deleted_at is not None else "upserted"
    return "accepted", cursor, {"type": f"{kind}.{suffix}", "data": result}


def _follow_order_status(db: Session, event_id: str, row: RouteObjectRow, before: dict | None, actor: str,
                         at: datetime, now: datetime, notices: Notices) -> None:
    """Status of the operator after a change of his order (lot 27,
    sarcade.events.order_status): the target of the order and, when the
    answer came from another terminal of the same person, that terminal.
    [at]: time of the change, so that a status chosen by hand later is kept.
    The status before the order is saved on the order at the first answer."""
    from .operations import journal_operator_status, record_operator_status

    data = dict(row.data or {})
    event = order_status.transition(before, data)
    if event is None:
        return
    saved = dict(data.get(order_status.BEFORE_FIELD) or {})
    target = data.get("target_id") or ""
    if event in ("refused", "cancelled"):
        devices = list(saved)
    else:
        devices = [target] + ([actor] if actor != target and _same_person(db, event_id, actor, target) else [])
    for device_id in devices:
        device = db.get(DeviceRow, (event_id, device_id))
        if device is None:
            continue  # never seen in this event: no status to follow
        if event in ("accepted", "arrived") and device_id not in saved:
            saved[device_id] = device.operator_status
        apply, status = order_status.target_status(event, saved, device_id)
        if apply and record_operator_status(db, device, status, at, now, actor, order_status.REASONS[event]):
            notices.statuses[device_id] = journal_operator_status(db, device, now)
    # Done with the order (refused, cancelled, finished): nothing to restore later.
    if event in ("refused", "cancelled", "finished"):
        saved = {}
    if saved:
        data[order_status.BEFORE_FIELD] = saved
    else:
        data.pop(order_status.BEFORE_FIELD, None)
    row.data = data


def _same_person(db: Session, event_id: str, actor: str, target: str | None) -> bool:
    """The actor and the target of an order are terminals of the same person
    (same callsign in Paramètres › Profil), lot 20."""
    if not target or target == actor:
        return False
    a, t = db.get(DeviceRow, (event_id, actor)), db.get(DeviceRow, (event_id, target))
    return a is not None and t is not None and routes.same_person(a.callsign, t.callsign)


def _logbook_and_notices(db, event_id, kind, row, before, was_deleted, change, route_row, notices, now):
    actor = change["updated_by"]
    name = (row.data or {}).get("name", "")

    def log(text):
        db.add(LogbookRow(event_id=event_id, kind="route", object_id=row.id, actor_id=actor, summary=text, time=now))

    if kind == "route":
        if row.deleted_at is not None:
            if before is not None and not was_deleted:
                log(f"Route supprimée : « {(before or {}).get('name', '')} »")
            return
        if before is None or was_deleted:
            log(f"Route créée : « {name} »")
        data = row.data
        assigned_before = routes.is_assigned(before) if before else False
        if routes.is_assigned(data) and (not assigned_before or (
                before.get("assigned_team_id"), before.get("assigned_group_id")) != (
                data.get("assigned_team_id"), data.get("assigned_group_id"))):
            log(f"Route « {name} » affectée")
            notices.add(row.id, "route affectée à votre équipe")
        elif assigned_before and routes.is_assigned(data):
            if before.get("point_order") != data.get("point_order"):
                notices.add(row.id, "ordre des points modifié")
                if routes.concurrent_order_change(before.get("point_order"), change.get("base_point_order"),
                                                  data.get("point_order")):
                    notices.order_conflicts.append((row.id, name, actor))
            elif before != data:
                notices.add(row.id, "caractéristiques de la route modifiées")
        return
    if kind == "route_waypoint":
        if route_row is None or not routes.is_assigned(route_row.data):
            return
        wp = (before or {}).get("name") or name
        if row.deleted_at is not None:
            notices.add(route_row.id, f"point « {wp} » supprimé")
        elif before is None or was_deleted:
            notices.add(route_row.id, f"point « {name} » ajouté")
        elif (before.get("lat"), before.get("lon")) != (row.data.get("lat"), row.data.get("lon")):
            notices.add(route_row.id, f"point « {name} » déplacé")
        elif before != row.data:
            notices.add(route_row.id, f"point « {name} » modifié")
        return
    if kind == "road_closure":
        label = (row.data or {}).get("label") or (before or {}).get("label", "")
        active = row.deleted_at is None and (row.data or {}).get("active", True)
        was_active = before is not None and not was_deleted and before.get("active", True)
        if active and not was_active:
            log(f"Route coupée : « {label} »")
        elif was_active and not active:
            log(f"Route rouverte : « {label} »")
        return
    if kind == "move_order" and row.deleted_at is None:
        d = row.data
        target = d.get("target_id")
        label = (d.get("destination") or {}).get("label") or routes.DEFAULT_ORDER_LABEL
        status = d.get("status")
        if before is None:
            log(f"Ordre de déplacement : {target} vers « {label} »")
            notices.messages.append((actor, [target], "immediate", routes.move_order_text(d), row.id))
        elif status != before.get("status"):
            if status == "accepted":
                log(f"{target} a accepté l'ordre de déplacement vers « {label} »")
            elif status == "refused":
                log(f"{target} a refusé l'ordre de déplacement vers « {label} »")
                notices.messages.append((target, [row.created_by], "urgent",
                                         f"Ordre de déplacement refusé par {target} : « {label} »", row.id))
            elif status == "arrived":
                log(f"Arrivée de {target} à « {label} » (ordre de déplacement)")
                notices.messages.append((target, [row.created_by], "routine",
                                         f"{target} est arrivé à « {label} »", row.id))
            elif status == "cancelled":
                log(f"Ordre de déplacement de {target} vers « {label} » annulé")
                notices.messages.append((actor, [target], "urgent",
                                         f"Ordre de déplacement annulé : « {label} »", row.id))
        return
    if kind == "itinerary" and row.deleted_at is None:
        d = row.data
        dest = d.get("destination", {}).get("label") or "point désigné"
        who = d.get("team_id") or d.get("device_id")
        if before is None:
            log(f"Départ de {who} vers « {dest} »")
        elif d.get("status") == "arrived" and before.get("status") != "arrived":
            log(f"Arrivée de {who} à « {dest} »")
        return
    if kind == "route_passage" and row.deleted_at is None:
        waypoint = db.get(RouteObjectRow, row.data.get("waypoint_id"))
        wp = (waypoint.data or {}).get("name", "?") if waypoint else "?"
        route_name = (route_row.data or {}).get("name", "?") if route_row else "?"
        if before is None:
            how = "automatique" if row.data.get("mode") == "auto" else "manuel"
            log(f"Passage au point « {wp} » de la route « {route_name} » ({how})")
        elif row.data.get("cancelled") and not before.get("cancelled"):
            log(f"Passage annulé au point « {wp} » de la route « {route_name} »")


def flush_notices(db: Session, event_id: str, notices: Notices, now: datetime) -> list[dict]:
    """Creates the urgent messages to the teams and the PCO warnings.
    Returns realtime payloads to broadcast."""
    payloads = []
    for route_id, changes in notices.changes.items():
        route = _route(db, route_id)
        if route is None or not routes.is_assigned(route.data):
            continue
        data = route.data
        target = data.get("assigned_group_id") or groups.team_group_id(event_id, data["assigned_team_id"])
        if data.get("assigned_group_id") is None and db.get(CommGroupRow, target) is None:
            target = None
        recipients = [f"{groups.GROUP_PREFIX}{target}"] if target else []
        if data.get("assigned_team_id") and not target:
            recipients = [data["assigned_team_id"]]
        body = routes.change_summary(data.get("name", ""), changes)
        msg = MessageRow(id=str(uuid.uuid4()), event_id=event_id, sender_id=SYSTEM_SENDER,
                         recipient_ids=recipients, priority="urgent", body=body, attachments=None, created_at=now)
        db.add(msg)
        db.add(LogbookRow(event_id=event_id, kind="message", object_id=msg.id, actor_id=SYSTEM_SENDER,
                          summary=body, time=now))
        payloads.append({"type": "message.created", "data": {
            "id": msg.id, "event_id": event_id, "sender_id": SYSTEM_SENDER, "recipient_ids": recipients,
            "priority": "urgent", "body": body, "attachments": [], "route_id": route_id,
            "created_at": features.iso(now)}})
    for sender, recipients, priority, body, order_id in notices.messages:
        msg = MessageRow(id=str(uuid.uuid4()), event_id=event_id, sender_id=sender[:64],
                         recipient_ids=recipients, priority=priority, body=body, attachments=None, created_at=now)
        db.add(msg)
        db.add(LogbookRow(event_id=event_id, kind="message", object_id=msg.id, actor_id=sender[:64],
                          summary=body, time=now))
        payloads.append({"type": "message.created", "data": {
            "id": msg.id, "event_id": event_id, "sender_id": sender[:64], "recipient_ids": recipients,
            "priority": priority, "body": body, "attachments": [], "move_order_id": order_id,
            "created_at": features.iso(now)}})
    for data in notices.statuses.values():
        payloads.append({"type": "operator.status", "data": data})
    for route_id, name, actor in notices.order_conflicts:
        text = f"Ordre des points de la route « {name} » modifié en même temps par plusieurs personnes : la dernière version ({actor}) l'emporte"
        db.add(LogbookRow(event_id=event_id, kind="route", object_id=route_id, actor_id=actor, summary=text, time=now))
        payloads.append({"type": "route.order_conflict", "data": {"route_id": route_id, "summary": text}})
    return payloads


def route_view(route: RouteObjectRow, children: list[RouteObjectRow]) -> dict:
    """A route with its ordered waypoints, distances and passages."""
    data = object_dict(route)
    waypoints = {c.id: object_dict(c) for c in children if c.kind == "route_waypoint" and c.deleted_at is None}
    order = [i for i in route.data.get("point_order", []) if i in waypoints]
    order += sorted((i for i in waypoints if i not in order), key=lambda i: waypoints[i]["updated_at"])
    ordered = [waypoints[i] for i in order]
    total = 0.0
    for previous, w in zip(ordered, ordered[1:]):
        w["leg_length_m"] = round(routes.leg_length_m(previous, w), 1)
        total += w["leg_length_m"]
        w["cumulative_m"] = round(total, 1)
    if ordered:
        ordered[0]["leg_length_m"], ordered[0]["cumulative_m"] = 0.0, 0.0
    data["waypoints"] = ordered
    data["total_length_m"] = round(total, 1)
    data["passages"] = [object_dict(c) for c in children if c.kind == "route_passage" and c.deleted_at is None]
    return data


@router.get("/events/{event_id}/routes")
def list_routes(event_id: str, db: Session = Depends(get_db)):
    if db.get(EventRow, event_id) is None:
        raise HTTPException(status_code=404, detail="event_not_found")
    rows = db.scalars(select(RouteObjectRow).where(RouteObjectRow.event_id == event_id)).all()
    by_route: dict[str, list] = {}
    for r in rows:
        if r.kind != "route":
            by_route.setdefault(r.route_id, []).append(r)
    return [route_view(r, by_route.get(r.id, [])) for r in rows if r.kind == "route" and r.deleted_at is None]


@router.get("/events/{event_id}/road-closures")
def list_closures(event_id: str, db: Session = Depends(get_db)):
    rows = db.scalars(select(RouteObjectRow).where(RouteObjectRow.event_id == event_id,
                                                   RouteObjectRow.kind == "road_closure",
                                                   RouteObjectRow.deleted_at.is_(None))).all()
    return [object_dict(r) for r in rows]


@router.get("/events/{event_id}/itineraries")
def list_itineraries(event_id: str, db: Session = Depends(get_db)):
    """Shared itineraries: planned path and estimated arrival for the PCO."""
    rows = db.scalars(select(RouteObjectRow).where(RouteObjectRow.event_id == event_id,
                                                   RouteObjectRow.kind == "itinerary",
                                                   RouteObjectRow.deleted_at.is_(None))).all()
    return [object_dict(r) for r in rows]


@router.get("/events/{event_id}/move-orders")
def list_move_orders(event_id: str, db: Session = Depends(get_db)):
    """Move orders of the PCO to the operators, with their answers."""
    rows = db.scalars(select(RouteObjectRow).where(RouteObjectRow.event_id == event_id,
                                                   RouteObjectRow.kind == "move_order",
                                                   RouteObjectRow.deleted_at.is_(None))).all()
    return [object_dict(r) for r in rows]


class EtaIn(BaseModel):
    # Terminal that sends the estimate (the targeted operator, another
    # terminal of the same person, or the PCO).
    device_id: str = Field(min_length=1, max_length=64)
    # Estimated arrival time (ISO 8601); null clears it.
    eta: datetime | None = None
    # Distance still to go, in metres (optional, shown to the PCO).
    remaining_m: float | None = None


@router.put("/events/{event_id}/move-orders/{order_id}/eta")
async def put_move_order_eta(event_id: str, order_id: str, payload: EtaIn, request: Request,
                             db: Session = Depends(get_db)):
    """Estimated arrival time of an accepted move order (card « Android
    Auto : heure d'arrivée estimée envoyée au PCO », lot 24). Only the
    estimate changes: the order keeps its ``updated_at`` (its answers and
    the cancellation of the PCO still win), ``revision`` goes up, the
    change feed and ``move_order.upserted`` carry the order."""
    from sarcade import roles as user_roles
    from sarcade.realtime.manager import manager

    from .roles import checked_email

    row = db.get(RouteObjectRow, order_id)
    if row is None or row.event_id != event_id or row.kind != "move_order" or row.deleted_at is not None:
        raise HTTPException(status_code=404, detail="move_order_not_found")
    now = datetime.now(UTC)
    try:
        eta = routes.clean_eta(payload.eta, now)
        remaining = routes.clean_remaining(payload.remaining_m)
    except features.InvalidFeature as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    email = checked_email(request)
    pco = groups.is_pco(payload.device_id) or (email is not None and "pco" in user_roles.roles_of(db, email))
    data = dict(row.data or {})
    if data.get("status") != "accepted":
        if eta is None:
            return object_dict(row)          # nothing to clear
        raise HTTPException(status_code=409, detail="order_not_accepted")
    if not routes.eta_allowed(data, payload.device_id, pco,
                              _same_person(db, event_id, payload.device_id, data.get("target_id"))):
        raise HTTPException(status_code=403, detail="not_the_target")
    if eta is None and not data.get("eta"):
        return object_dict(row)              # nothing to clear
    # Sent again unchanged (every 2 min): ``eta_updated_at`` tells the PCO
    # the estimate is still fresh.
    row.data = routes.with_eta(data, eta, payload.device_id, now, remaining)
    row.revision = (row.revision or 0) + 1
    db.flush()
    result = object_dict(row)
    record_operation(db, event_id=event_id, operation_id=f"eta-{uuid.uuid4()}", object_id=row.id,
                     object_type="move_order", action="update", payload=result, client_time=now)
    db.commit()
    await manager.broadcast(event_id, {"type": "move_order.upserted", "data": result})
    return result


@router.get("/events/{event_id}/routes/{route_id}.gpx")
def route_gpx(event_id: str, route_id: str, db: Session = Depends(get_db)):
    route = _route(db, route_id)
    if route is None or route.event_id != event_id or route.deleted_at is not None:
        raise HTTPException(status_code=404, detail="route_not_found")
    children = db.scalars(select(RouteObjectRow).where(RouteObjectRow.route_id == route_id)).all()
    view = route_view(route, list(children))
    name = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in view["name"]) or "route"
    return Response(routes.gpx(view, view["waypoints"]), media_type="application/gpx+xml",
                    headers={"Content-Disposition": f'attachment; filename="{name}.gpx"'})


class Report(BaseModel):
    actor_id: str = Field(min_length=1, max_length=64)
    waypoint_id: str = Field(min_length=1, max_length=64)
    reason: str = Field(min_length=1, max_length=300)


@router.post("/events/{event_id}/routes/{route_id}/reports", status_code=201)
async def report_waypoint(event_id: str, route_id: str, payload: Report, db: Session = Depends(get_db)):
    """The team reports an impracticable waypoint; the PCO decides."""
    from sarcade.realtime.manager import manager

    route = _route(db, route_id)
    waypoint = db.get(RouteObjectRow, payload.waypoint_id)
    if route is None or route.event_id != event_id or waypoint is None or waypoint.route_id != route_id:
        raise HTTPException(status_code=404, detail="route_or_waypoint_not_found")
    now = datetime.now(UTC)
    text = (f"Point « {waypoint.data.get('name', '')} » de la route « {route.data.get('name', '')} » "
            f"signalé impraticable : {payload.reason.strip()}")
    db.add(LogbookRow(event_id=event_id, kind="route", object_id=waypoint.id, actor_id=payload.actor_id,
                      summary=text, time=now))
    db.commit()
    data = {"route_id": route_id, "waypoint_id": waypoint.id, "actor_id": payload.actor_id,
            "reason": payload.reason.strip(), "summary": text, "time": features.iso(now)}
    await manager.broadcast(event_id, {"type": "route.report", "data": data})
    return data


def geofence_alerts(db: Session, event_id: str, device_id: str, lat: float, lon: float, now: datetime) -> list[dict]:
    """Géorepérage: urgent message to the PCO and logbook line when a device
    enters or leaves a fenced zone. Returns realtime payloads to broadcast."""
    from sarcade import geofence
    from sarcade.db.models import MapFeatureRow
    rows = db.scalars(select(MapFeatureRow).where(MapFeatureRow.event_id == event_id, MapFeatureRow.deleted_at.is_(None),
                                                  MapFeatureRow.kind.in_(geofence.KINDS))).all()
    fences = [{**r.data, "id": r.id} for r in rows if (r.data or {}).get("geofence") is True]
    payloads = []
    for fence, entered in geofence.transitions(event_id, device_id, lat, lon, fences):
        body = geofence.message(device_id, fence, entered)
        msg = MessageRow(id=str(uuid.uuid4()), event_id=event_id, sender_id=SYSTEM_SENDER,
                         recipient_ids=["PCO"], priority="urgent", body=body, attachments=None, created_at=now)
        db.add(msg)
        db.add(LogbookRow(event_id=event_id, kind="geofence", object_id=fence["id"], actor_id=device_id, summary=body, time=now))
        payloads.append({"type": "message.created", "data": {
            "id": msg.id, "event_id": event_id, "sender_id": SYSTEM_SENDER, "recipient_ids": ["PCO"],
            "priority": "urgent", "body": body, "attachments": [], "created_at": features.iso(now)}})
    return payloads
