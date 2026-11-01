import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime
import hashlib
import logging
import os
from pathlib import Path
import uuid

from fastapi import Depends, FastAPI, File, Form, HTTPException, Query, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.encoders import jsonable_encoder
from fastapi.responses import FileResponse, JSONResponse
from geoalchemy2.elements import WKTElement
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.exc import DataError, IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from sarcade.db.models import AckRow, EventRow, LogbookRow, MapFeatureRow, MessageRow, POIRow, PositionRow, SharedFileRow, SyncOperationRow, TeamRow
from sarcade.realtime.manager import manager
from .schemas import EventCreate, EventOut, POICreate, POIOut, PositionCreate, PositionOut, SyncOperationIn, SyncResultOut, TeamCreate, TeamOut
from .serializers import poi_dict, position_dict
from sarcade.sync.service import VALID_TYPES, record_operation
from sarcade.features import service as features
from sarcade.messages import attachments as msg_attachments
from sarcade.pois import service as pois
from sarcade.messages import deletion as msg_deletion
from sarcade.messages import reply as msg_reply

from .deps import get_db
from . import operations as event_ops
from . import groups as groups_api
from . import weather as weather_api
from . import routes as routes_api
from . import navigation as navigation_api
from . import basemaps as basemaps_api
from . import admin as admin_api
from sarcade.security import journal as security_journal, siem, watch as security_watch
from sarcade import licensing
from sarcade import modules
from sarcade.updates import service as updates_service
from sarcade.weather import service as weather_service
from sarcade.modules import hooks as module_hooks
from sarcade.modules import loader as module_loader
from sarcade.groups import service as groups
from . import access as access_api
from sarcade.access import logbook as access_logbook
from sarcade.access.verify import AccessMiddleware, identity_of
from . import geocode as geocode_api
from . import places as places_api
from . import wakeup as wakeup_api
from . import profile as profile_api
from . import personal_files as personal_files_api
from . import file_folders as file_folders_api
from sarcade.files import folders as file_folders
from . import roles as roles_api
from sarcade import roles as user_roles
from . import tv as tv_api
from sarcade.wakeup import service as wakeup_service
from sarcade.events import contacts as event_contacts
from . import diagnostics as diagnostics_api
from sarcade import diagnostics

log = logging.getLogger("sarcade.sync")

_background_tasks: list = []
# Wake-ups in flight (kept referenced until done).
_wake_tasks: set = set()


@asynccontextmanager
async def lifespan(_app):
    module_hooks.start(_background_tasks)
    weather_service.start(_background_tasks)
    navigation_api.start(_background_tasks)
    updates_service.start(_background_tasks)
    licensing.start(_background_tasks)
    siem.start(_background_tasks)
    event_contacts.start(_background_tasks)
    yield
    for task in _background_tasks:
        task.cancel()


app = FastAPI(title="SARCADE Server", version="0.1.0-dev", lifespan=lifespan)


@app.middleware("http")
async def no_store_for_api(request, call_next):
    """API answers are live data: no cache, browser or proxy, may keep them.
    A Cloudflare cache rule once kept the change feed for hours and clients
    stopped seeing new messages (25 October 2026)."""
    diagnostics.seen_cloudflare(request.headers)
    response = await call_next(request)
    if request.url.path.startswith("/api/") or request.url.path == "/health":
        response.headers["Cache-Control"] = "no-store"
    return response

@app.middleware("http")
async def tv_host_only_display(request, call_next):
    """tv.* (Smart TV, no Cloudflare Access): only the display page and its
    pairing and reading API answer; everything else is « not found »."""
    host = (request.headers.get("host") or "").split(":")[0].lower()
    if host.startswith("tv.") and request.url.path == "/":
        return tv_api.tv_page()          # tv.sarcade.org alone opens the display
    if host.startswith("tv.") and not tv_api.tv_host_allows(request.url.path):
        return JSONResponse({"detail": "not_found"}, status_code=404)
    return await call_next(request)


app.include_router(event_ops.router)
app.include_router(groups_api.router)
app.include_router(weather_api.router)
app.include_router(routes_api.router)
app.include_router(navigation_api.router)
app.include_router(basemaps_api.router)
app.include_router(admin_api.router)
app.middleware("http")(security_watch.middleware)
app.include_router(access_api.router)
# Server-side check of the Cloudflare Access token (off unless configured).
app.add_middleware(AccessMiddleware)
app.include_router(geocode_api.router)
app.include_router(places_api.router)
app.include_router(wakeup_api.router)
app.include_router(profile_api.router)
app.include_router(personal_files_api.router)
app.include_router(file_folders_api.router)
app.include_router(roles_api.router)
app.include_router(tv_api.router)
module_loader.load()
for _router in module_hooks.routers:
    app.include_router(_router)
app.include_router(diagnostics_api.router)


@app.get("/health")
def health():
    return {"status": "ok", "service": "sarcade-server"}


@app.post("/api/v0.1/events", response_model=EventOut, status_code=201)
def create_event(payload: EventCreate, db: Session = Depends(get_db), _pco=Depends(roles_api.require_pco)):
    now = datetime.now(UTC)
    # Modules of the event: asked, or those of its event model (31 Oct 2026).
    try:
        mods = modules.clean(payload.modules) if payload.modules is not None else modules.for_kind(payload.kind)
    except modules.InvalidModules as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    row = EventRow(id=str(uuid.uuid4()), name=payload.name, kind=payload.kind, summary=payload.summary,
                   status="draft", created_at=now, version=0, settings={"modules": mods})
    db.add(row); db.flush()
    # Base channels (« Tous », « Diffusion PCO »), created when the event opens.
    groups_api.ensure_groups(db, row.id, groups.default_groups(row.id, now), now)
    db.add(LogbookRow(event_id=row.id, kind="group", object_id=None, actor_id="PCO", time=now,
                      summary="Canaux de base créés (« Tous », « Diffusion PCO »)"))
    names = ", ".join(modules.MODULES[m]["label"] for m in mods) or "aucun (Core seul)"
    db.add(LogbookRow(event_id=row.id, kind="settings", object_id=row.id, actor_id="PCO", time=now,
                      summary=f"Modules de l'événement : {names}"))
    db.commit(); db.refresh(row)
    return row


@app.get("/api/v0.1/modules")
def list_modules():
    """Modules that can be enabled per event and the event models."""
    return {"modules": modules.catalogue(), "templates": modules.TEMPLATES}


@app.post("/api/v0.1/events/{event_id}/teams", response_model=TeamOut, status_code=201)
def create_team(event_id: str, payload: TeamCreate, db: Session = Depends(get_db)):
    if db.get(EventRow, event_id) is None:
        raise HTTPException(status_code=404, detail="event_not_found")
    row = TeamRow(id=str(uuid.uuid4()), event_id=event_id, name=payload.name)
    db.add(row); db.flush()
    now = datetime.now(UTC)
    groups_api.ensure_groups(db, event_id, [groups.team_group(event_id, row.id, row.name, now)], now)
    db.commit(); db.refresh(row)
    return row


@app.post("/api/v0.1/positions", status_code=202)
async def ingest_position(payload: PositionCreate, db: Session = Depends(get_db)):
    if db.get(PositionRow, payload.id) is not None:
        return {"status": "duplicate", "id": payload.id}
    if db.get(EventRow, payload.event_id) is None:
        raise HTTPException(status_code=404, detail="event_not_found")
    row = PositionRow(id=payload.id, event_id=payload.event_id, device_id=payload.device_id,
        point=WKTElement(f"POINT({payload.lon} {payload.lat})", srid=4326), alt_m=payload.alt_m,
        accuracy_m=payload.accuracy_m, heading_deg=payload.heading_deg,
        speed_mps=payload.speed_mps, time=payload.time, battery_pct=payload.battery_pct, source="device")
    db.add(row)
    device = event_ops.touch_device(db, payload.event_id, payload.device_id, datetime.now(UTC),
                                    position_time=payload.time, battery_pct=payload.battery_pct)
    db.commit(); db.refresh(row)
    module_hooks.position_received(db.get(EventRow, payload.event_id).settings, device,
                                   payload.lat, payload.lon, datetime.now(UTC))
    data = position_dict(row)
    alerts = _geofence(db, payload.event_id, {"device_id": payload.device_id, "lat": payload.lat, "lon": payload.lon},
                       datetime.now(UTC))
    db.commit()
    await manager.broadcast(payload.event_id, {"type": "position.updated", "data": data})
    for alert in alerts:
        await manager.broadcast(payload.event_id, alert)
        task = asyncio.create_task(wakeup_service.wake_for_message(payload.event_id, alert.get("data") or {}))
        _wake_tasks.add(task)
        task.add_done_callback(_wake_tasks.discard)
    return {"status": "accepted", "id": payload.id}


def _geofence(db: Session, event_id: str, p: dict, now: datetime) -> list[dict]:
    """Géorepérage alerts of a new position; never blocks the position."""
    try:
        return routes_api.geofence_alerts(db, event_id, str(p["device_id"]), float(p["lat"]), float(p["lon"]), now)
    except Exception:  # noqa: BLE001
        log.exception("geofence check failed")
        return []


@app.get("/api/v0.1/events/{event_id}/positions/latest", response_model=list[PositionOut])
def latest_positions(event_id: str, db: Session = Depends(get_db)):
    if db.get(EventRow, event_id) is None:
        raise HTTPException(status_code=404, detail="event_not_found")
    ranked = select(PositionRow.id, func.row_number().over(
        partition_by=PositionRow.device_id, order_by=PositionRow.time.desc()).label("rn")
    ).where(PositionRow.event_id == event_id).subquery()
    rows = db.scalars(select(PositionRow).join(ranked, PositionRow.id == ranked.c.id).where(ranked.c.rn == 1)).all()
    return [position_dict(row) for row in rows]


@app.get("/api/v0.1/events/{event_id}/devices/{device_id}/positions", response_model=list[PositionOut])
def position_history(event_id: str, device_id: str, limit: int = Query(500, ge=1, le=5000), db: Session = Depends(get_db)):
    rows = db.scalars(select(PositionRow).where(
        PositionRow.event_id == event_id, PositionRow.device_id == device_id
    ).order_by(PositionRow.time.desc()).limit(limit)).all()
    return [position_dict(row) for row in reversed(rows)]


@app.post("/api/v0.1/events/{event_id}/pois", response_model=POIOut, status_code=201)
async def create_poi(event_id: str, payload: POICreate, db: Session = Depends(get_db)):
    if db.get(EventRow, event_id) is None:
        raise HTTPException(status_code=404, detail="event_not_found")
    if db.get(POIRow, payload.id) is not None:
        raise HTTPException(status_code=409, detail="poi_already_exists")
    row = POIRow(id=payload.id, event_id=event_id, kind=payload.kind, label=payload.label,
        point=WKTElement(f"POINT({payload.lon} {payload.lat})", srid=4326),
        created_at=datetime.now(UTC), version=0, created_by=payload.created_by, color=payload.color)
    try:
        row.icon = pois.clean_icon(payload.icon)
    except pois.InvalidPoi:
        raise HTTPException(status_code=422, detail="invalid_icon")
    db.add(row); db.commit(); db.refresh(row)
    data = poi_dict(row)
    await manager.broadcast(event_id, {"type": "poi.created", "data": data})
    return data


@app.get("/api/v0.1/events/{event_id}/pois", response_model=list[POIOut])
def list_pois(event_id: str, db: Session = Depends(get_db)):
    rows = db.scalars(select(POIRow).where(POIRow.event_id == event_id, POIRow.deleted_at.is_(None)).order_by(POIRow.created_at)).all()
    return [poi_dict(row) for row in rows]




FILE_ROOT=Path(os.getenv("SARCADE_FILE_ROOT","/var/lib/sarcade/files"))
MAX_FILE_BYTES=int(os.getenv("SARCADE_MAX_FILE_BYTES",str(25*1024*1024)))

def file_dict(r):
    return {"id":r.id,"event_id":r.event_id,"sender_id":r.sender_id,"name":r.name,
            "mime_type":r.mime_type,"size_bytes":r.size_bytes,"sha256":r.sha256,"created_at":r.created_at,
            "folder":getattr(r,"folder",None) or ""}

@app.get("/api/v0.1/events/{event_id}/files")
def list_files(event_id:str,with_folders:bool=Query(False),db:Session=Depends(get_db)):
    """A list of files; with ``with_folders`` an object {files, folders}
    (subfolders, sarcade.files.folders)."""
    rows=db.scalars(select(SharedFileRow).where(SharedFileRow.event_id==event_id).order_by(SharedFileRow.created_at)).all()
    if with_folders:
        return {"files":[file_dict(r) for r in rows],"folders":file_folders_api.listing(db,event_id,rows)}
    return [file_dict(r) for r in rows]

@app.post("/api/v0.1/events/{event_id}/files",status_code=201)
async def upload_file(event_id:str,request:Request,sender_id:str=Form(...),file:UploadFile=File(...),
                      file_id:str|None=Form(None),mime_type:str|None=Form(None),folder:str|None=Form(None),
                      db:Session=Depends(get_db)):
    """[file_id], chosen by the client, makes the upload idempotent: an upload
    retried after a network loss returns the stored file instead of a copy.
    [folder] (form field or query parameter): subfolder, "" for the root."""
    if db.get(EventRow,event_id) is None: raise HTTPException(status_code=404,detail="unknown_event")
    try: folder=file_folders.clean_path(folder if folder is not None else request.query_params.get("folder"))
    except file_folders.FolderError as exc: raise HTTPException(status_code=exc.status,detail=exc.detail)
    if file_id is not None and not msg_attachments.valid_file_id(file_id):
        raise HTTPException(status_code=422,detail="invalid_file_id")
    data=await file.read(MAX_FILE_BYTES+1)
    if len(data)>MAX_FILE_BYTES: raise HTTPException(status_code=413,detail="file_too_large")
    digest=hashlib.sha256(data).hexdigest()
    if file_id is not None and (existing:=db.get(SharedFileRow,file_id)) is not None:
        if existing.event_id!=event_id or existing.sha256!=digest:
            raise HTTPException(status_code=409,detail="file_id_conflict")
        return JSONResponse(jsonable_encoder(file_dict(existing)),status_code=200)
    fid=file_id or str(uuid.uuid4())
    # Stored under FILE_ROOT only, whatever the identifiers given.
    root=os.path.realpath(FILE_ROOT)
    path=os.path.realpath(os.path.join(root,event_id,fid))
    if not path.startswith(root+os.sep): raise HTTPException(status_code=422,detail="invalid_path")
    os.makedirs(os.path.dirname(path),exist_ok=True)
    with open(path,"wb") as out: out.write(data)
    row=SharedFileRow(id=fid,event_id=event_id,sender_id=sender_id,name=file.filename or "file",
        mime_type=msg_attachments.clean_mime_type(mime_type or file.content_type),size_bytes=len(data),storage_path=str(path),
        sha256=digest,created_at=datetime.now(UTC),folder=folder)
    db.add(row);file_folders.ensure(db,"event",event_id,folder);db.commit();db.refresh(row)
    db.add(LogbookRow(event_id=event_id,kind="file",object_id=row.id,actor_id=sender_id,
        summary=f"Fichier partagé : {row.name}",time=row.created_at));db.commit()
    await manager.broadcast(event_id,{"type":"file.created","data":file_dict(row)})
    return file_dict(row)

@app.get("/api/v0.1/events/{event_id}/files/{file_id}/content")
def download_file(event_id:str,file_id:str,request:Request,db:Session=Depends(get_db)):
    row=db.get(SharedFileRow,file_id)
    if row is None or row.event_id!=event_id: raise HTTPException(status_code=404,detail="file_not_found")
    # Photo of a deleted message: only a checked PCO / administrator still gets it.
    if file_id in msg_deletion.hidden_file_ids(db,event_id):
        email=roles_api.checked_email(request)
        if email is None or "pco" not in user_roles.roles_of(db,email):
            raise HTTPException(status_code=410,detail="message_deleted")
    path=Path(row.storage_path)
    if not path.is_file(): raise HTTPException(status_code=404,detail="file_content_not_found")
    # Same origin as the web client: never let a browser sniff or render an
    # uploaded file as a page.
    return FileResponse(path,media_type=row.mime_type,filename=row.name,headers={"X-Content-Type-Options":"nosniff"})


@app.websocket("/api/v0.1/events/{event_id}/ws")
async def event_websocket(websocket: WebSocket, event_id: str):
    await manager.connect(event_id, websocket)
    try:
        while True:
            # « ping » from the clients every 30 s: the answer proves the
            # channel is alive end to end (a connection can stay open on the
            # client side behind a proxy while it is dead on the server side).
            if (await websocket.receive_text()).strip() == "ping":
                await websocket.send_json({"type": "pong", "data": {}})
    except WebSocketDisconnect:
        manager.disconnect(event_id, websocket)


def _apply_map_feature(db: Session, op, now: datetime) -> tuple[str, int, dict | None]:
    """Validates, journals and applies one map feature change (ADR-001).

    Returns (status, cursor, broadcast payload). Only accepted changes are
    visible in the change feed, with the resulting state as payload.
    """
    if op.action not in features.ACTIONS:
        return "rejected", 0, None
    try:
        if op.action == "delete":
            change = features.validate_delete(op.payload, event_id=op.event_id, object_id=op.object_id)
        else:
            change = features.validate_upsert(op.payload, event_id=op.event_id, object_id=op.object_id)
    except features.InvalidFeature:
        return "rejected", 0, None
    event = db.get(EventRow, op.event_id)
    if event is None:
        return "rejected", 0, None
    # Bearings belong to the ADRASEC module: refused in an event without it
    # (31 Oct 2026); deleting an older one stays possible.
    if op.action != "delete" and (op.payload or {}).get("kind") == "bearing" \
            and not modules.enabled(event.settings, "adrasec"):
        return "rejected", 0, None
    change["updated_at"] = features.clamp_time(change["updated_at"], now)

    status, cursor = record_operation(
        db, event_id=op.event_id, operation_id=op.operation_id, object_id=op.object_id,
        object_type=op.object_type, action=op.action, payload=op.payload, client_time=op.client_time,
    )
    if status != "accepted":
        return status, cursor, None
    journal = db.scalar(select(SyncOperationRow).where(SyncOperationRow.seq == cursor))

    row = db.get(MapFeatureRow, op.object_id)
    if row is not None and row.event_id != op.event_id:
        journal.status = "rejected"
        return "rejected", 0, None
    # A bearing is changed or deleted by its author or the PCO only
    # (note « Relevés et croisements », decision 5).
    if row is not None and row.kind == "bearing" and change["updated_by"] != row.created_by \
            and not groups.is_pco(change["updated_by"]):
        journal.status = "rejected"
        return "rejected", 0, None
    decision = features.decide(op.action, change, row)
    if decision.status != "accepted":
        journal.status = "conflict"
        return "conflict", cursor, None

    was_deleted = row is not None and row.deleted_at is not None
    if decision.tombstone:
        unknown = row is None
        if unknown:
            row = MapFeatureRow(id=op.object_id, event_id=op.event_id, kind="point",
                data={"points": [[0.0, 0.0]], "color": 0, "stroke_width": 1.0, "label": ""},
                created_by=change["updated_by"], created_at=now, revision=0)
            db.add(row)
        row.deleted_at = change["updated_at"]
        logbook_action = None if unknown or was_deleted else "delete"
    else:
        data = {k: v for k, v in change.items() if k not in ("updated_at", "updated_by", "id", "event_id", "kind")}
        if row is None:
            row = MapFeatureRow(id=op.object_id, event_id=op.event_id, created_by=change["created_by"],
                created_at=now, revision=0)
            db.add(row)
            logbook_action = "create"
        else:
            # Every change of a bearing goes to the logbook (decision 5).
            logbook_action = "restore" if was_deleted else ("update" if change["kind"] == "bearing" else None)
        row.kind = change["kind"]
        row.data = data
        row.geom = WKTElement(features.geometry_wkt(change), srid=4326)
        row.deleted_at = None
    row.updated_at = change["updated_at"]
    row.updated_by = change["updated_by"]
    row.revision = (row.revision or 0) + 1
    db.flush()

    result = features.feature_dict(row)
    journal.payload = result
    group = features.group_of(op.payload)
    if group is not None:
        # One line for the whole group, written by its first operation.
        logbook_action = None
        if db.scalar(select(LogbookRow.seq).where(LogbookRow.event_id == op.event_id, LogbookRow.kind == "map_feature",
                                                 LogbookRow.object_id == group["id"])) is None:
            db.add(LogbookRow(event_id=op.event_id, kind="map_feature", object_id=group["id"],
                              actor_id=change["updated_by"], time=now, summary=features.group_summary(group)))
    if logbook_action:
        db.add(LogbookRow(event_id=op.event_id, kind="map_feature", object_id=row.id,
            actor_id=change["updated_by"], time=now,
            summary=features.logbook_summary(row.kind, (row.data or {}).get("label", ""), logbook_action,
                                             exercise=bool(((row.data or {}).get("bearing") or {}).get("exercise")))))
    kind = "map_feature.deleted" if row.deleted_at is not None else "map_feature.upserted"
    return "accepted", cursor, {"type": kind, "data": result}


def _apply_poi_change(db: Session, op, now: datetime, pco: bool) -> tuple[str, int, dict | None]:
    """Rename or delete of a point of interest: by its author or the PCO;
    the change feed carries the resulting state (``deleted`` on a deletion)."""
    row = db.get(POIRow, op.object_id)
    if row is None or row.event_id != op.event_id:
        raise SyncRejected("point d'intérêt inconnu")
    actor = pois.actor_of(op.payload)
    if not pois.can_edit(row.created_by, actor, pco):
        raise SyncRejected("seul l'auteur du point ou le PCO peut le modifier", journal=dict(
            category="rights", action="write_refused", outcome="refused", actor=actor, event_id=op.event_id,
            details={"object_type": "poi", "object_id": op.object_id, "action": op.action}))
    update = op.action == "update"
    try:
        label = pois.clean_label(op.payload.get("label")) if update and "label" in op.payload else row.label
    except pois.InvalidPoi:
        raise SyncRejected("nom de point invalide")
    try:
        icon = pois.clean_icon(op.payload.get("icon")) if update and "icon" in op.payload else row.icon
    except pois.InvalidPoi:
        raise SyncRejected("icône de point invalide")
    status, cursor = record_operation(
        db, event_id=op.event_id, operation_id=op.operation_id, object_id=op.object_id,
        object_type=op.object_type, action=op.action, payload=op.payload, client_time=op.client_time)
    if status != "accepted":
        return status, cursor, None
    journal = db.scalar(select(SyncOperationRow).where(SyncOperationRow.seq == cursor))
    if row.deleted_at is not None:
        # Already deleted elsewhere: nothing more to do.
        journal.status = "conflict"
        return "conflict", cursor, None
    row.updated_by, row.updated_at = actor, now
    row.version = (row.version or 0) + 1
    if op.action == "delete":
        row.deleted_at = now
        db.add(LogbookRow(event_id=op.event_id, kind="poi", object_id=row.id, actor_id=actor, time=now,
                          summary=pois.logbook_summary(row.label, "delete")))
    else:
        row.label, row.icon = label, icon
    db.flush()
    result = pois.state(row)
    journal.payload = result
    return "accepted", cursor, {"type": "poi.deleted" if op.action == "delete" else "poi.updated", "data": result}


def _iso(value) -> datetime:
    if not isinstance(value, str):
        raise ValueError("invalid_time")
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


class SyncRejected(Exception):
    """The operation can never be applied: the client sets it aside."""

    def __init__(self, reason: str, journal: dict | None = None):
        super().__init__(reason)
        self.reason = reason
        # Security journal entry, written after the savepoint is undone.
        self.journal = journal


def _apply_operation(db: Session, op: SyncOperationIn, now: datetime,
                     notices: dict | None = None, pco: bool = False) -> tuple[str, int, dict | None]:
    """Applies one synchronised operation. Raises SyncRejected (or a data
    error) when it cannot be applied; the caller isolates it in a savepoint."""
    if op.object_type in routes_api.SYNC_TYPES:
        batch = (notices if notices is not None else {}).setdefault(op.event_id, routes_api.Notices())
        return routes_api.apply_route_object(db, op, now, batch, pco=pco)
    if op.object_type == "map_feature":
        return _apply_map_feature(db, op, now)
    if op.object_type == "comm_group":
        return groups_api.apply_group(db, op, now)
    if op.object_type == "poi" and op.action in ("update", "delete"):
        return _apply_poi_change(db, op, now, pco)
    if op.object_type not in VALID_TYPES:
        raise SyncRejected(f"type d'élément inconnu : {op.object_type}")
    if op.object_type == "message" and op.action == "create":
        try:
            op.payload["attachments"] = msg_attachments.clean_attachments(op.payload.get("attachments"))
        except ValueError:
            raise SyncRejected("pièces jointes invalides")
        op.payload["reply_to"] = msg_reply.clean_reply_to(db, op.event_id, op.payload.get("reply_to"))
        if not groups_api.message_allowed(db, op.event_id, op.payload.get("sender_id", ""),
                                          op.payload.get("recipient_ids")):
            raise SyncRejected("envoi non autorisé dans ce groupe (écoute seule)", journal=dict(
                category="rights", action="write_refused", outcome="refused",
                actor=str(op.payload.get("sender_id", ""))[:64] or None, event_id=op.event_id,
                details={"object_type": "message", "reason": "listen_only_or_archived_group",
                         "recipients": op.payload.get("recipient_ids") or []}))
    if op.object_type == "message" and op.action == "delete":
        return _apply_message_delete(db, op, now, pco)
    p = op.payload
    if op.object_type == "ack" and op.action == "create" and db.get(AckRow, op.object_id) is None:
        if db.get(MessageRow, p.get("message_id")) is None:
            raise SyncRejected("accusé de réception d'un message inconnu de ce serveur")
    status, cursor = record_operation(
        db, event_id=op.event_id, operation_id=op.operation_id,
        object_id=op.object_id, object_type=op.object_type,
        action=op.action, payload=op.payload, client_time=op.client_time,
    )
    if status != "accepted" or op.action != "create":
        return status, cursor, None
    if op.object_type == "position" and db.get(PositionRow, op.object_id) is None:
        row = PositionRow(
            id=op.object_id, event_id=op.event_id, device_id=p["device_id"],
            point=WKTElement(f'POINT({float(p["lon"])} {float(p["lat"])})', srid=4326),
            alt_m=p.get("alt_m"), accuracy_m=p.get("accuracy_m"),
            heading_deg=p.get("heading_deg"), speed_mps=p.get("speed_mps"),
            time=_iso(p["time"]),
            battery_pct=event_ops.clean_battery(p.get("battery_pct")), source="device",
        )
        db.add(row); db.flush()
        device = event_ops.touch_device(db, op.event_id, p["device_id"], now,
                                        position_time=row.time, battery_pct=row.battery_pct)
        event = db.get(EventRow, op.event_id)
        if event is not None:
            module_hooks.position_received(event.settings, device, p["lat"], p["lon"], now)
        return status, cursor, {"type": "position.updated", "data": p}
    if op.object_type == "poi" and db.get(POIRow, op.object_id) is None:
        row = POIRow(
            id=op.object_id, event_id=op.event_id, kind=p["kind"], label=p.get("label"),
            point=WKTElement(f'POINT({float(p["lon"])} {float(p["lat"])})', srid=4326),
            created_at=_iso(p["created_at"]), version=0,
            created_by=pois.actor_of(p), color=pois.clean_color(p.get("color")),
            icon=pois.clean_icon(p.get("icon")),
        )
        db.add(row); db.flush()
        return status, cursor, {"type": "poi.created", "data": p}
    if op.object_type == "message" and db.get(MessageRow, op.object_id) is None:
        row = MessageRow(id=op.object_id, event_id=op.event_id, sender_id=p["sender_id"],
            recipient_ids=p.get("recipient_ids", []), priority=p.get("priority", "routine"),
            body=p["body"], attachments=p["attachments"] or None,
            created_at=_iso(p["created_at"]), reply_to=p.get("reply_to"))
        db.add(row); db.flush()
        db.add(LogbookRow(event_id=op.event_id, kind="message", object_id=row.id,
            actor_id=row.sender_id, summary=msg_attachments.logbook_summary(row.body, p["attachments"]),
            time=row.created_at))
        db.flush()
        return status, cursor, {"type": "message.created", "data": p}
    if op.object_type == "ack" and db.get(AckRow, op.object_id) is None:
        row = AckRow(id=op.object_id, event_id=op.event_id, message_id=p["message_id"],
            actor_id=p["actor_id"], status=p["status"], time=_iso(p["time"]))
        db.add(row); db.flush()
        db.add(LogbookRow(event_id=op.event_id, kind="ack", object_id=row.id,
            actor_id=row.actor_id, summary=f'{row.status}: {row.message_id}', time=row.time))
        db.flush()
        return status, cursor, {"type": "ack.created", "data": p}
    return status, cursor, None



def _apply_message_delete(db: Session, op: SyncOperationIn, now: datetime, pco: bool) -> tuple[str, int, dict | None]:
    """Deletion for everyone: the author during 2 days, the PCO always."""
    existing = db.scalar(select(SyncOperationRow).where(SyncOperationRow.operation_id == op.operation_id))
    if existing is not None:
        return "duplicate", existing.seq, None
    row = db.get(MessageRow, op.object_id)
    if row is None or row.event_id != op.event_id:
        raise SyncRejected("suppression d'un message inconnu de ce serveur")
    if row.deleted_at is not None:
        return "conflict", 0, None
    actor = str(op.payload.get("deleted_by") or "")[:64]
    ok, as_pco = msg_deletion.allowed(row, actor, pco or groups.is_pco(actor), now)
    if not ok:
        raise SyncRejected("suppression refusée : réservée à l'auteur pendant 2 jours, ou au PCO", journal=dict(
            category="rights", action="write_refused", outcome="refused", actor=actor or None,
            event_id=op.event_id, details={"object_type": "message", "reason": "delete_not_allowed",
                                           "message_id": row.id}))
    data = msg_deletion.apply(db, row, actor, as_pco, now)
    status, cursor = record_operation(
        db, event_id=op.event_id, operation_id=op.operation_id, object_id=op.object_id,
        object_type="message", action="delete", payload=data, client_time=op.client_time)
    return status, cursor, {"type": "message.deleted", "data": data}


_REJECT_REASONS = {"route": "route invalide ou non autorisée", "route_waypoint": "point de passage invalide",
                   "route_passage": "passage invalide", "map_feature": "objet cartographique invalide", "comm_group": "groupe invalide ou non autorisé"}


def _sync_result(operation_id: str, status: str, cursor: int = 0, reason: str | None = None) -> dict:
    return {"operation_id": operation_id, "status": status, "server_time": datetime.now(UTC),
            "sync_cursor": str(cursor) if cursor else None, "reason": reason}


@app.post("/api/v0.1/sync", response_model=list[SyncResultOut])
async def synchronize(operations: list[dict], request: Request, db: Session = Depends(get_db)):
    """Applies a batch of offline operations, each one on its own (« Robustesse
    de la synchronisation »): an operation that fails is undone alone, in its
    savepoint, and gets its own status; it never blocks the others.

    Statuses: accepted, duplicate, conflict (applied elsewhere, nothing to
    do), rejected (can never be applied, set aside by the client),
    unknown_event (the event does not exist on this server, kept by the
    client) and retry (transient server error, sent again later).
    """
    results = []
    broadcasts = []
    known_events: dict[str, bool] = {}
    identity = identity_of(request)
    seen_actors: set[tuple[str, str]] = set()
    notices: dict[str, routes_api.Notices] = {}
    # PCO by the email checked by Cloudflare Access (move orders).
    email = roles_api.checked_email(request)
    pco = email is not None and "pco" in user_roles.roles_of(db, email)
    for raw in operations:
        operation_id = str(raw.get("operation_id") or "")[:64] if isinstance(raw, dict) else ""
        try:
            op = SyncOperationIn.model_validate(raw)
        except ValidationError:
            results.append(_sync_result(operation_id, "rejected", reason="élément mal formé"))
            continue
        if op.event_id not in known_events:
            known_events[op.event_id] = db.get(EventRow, op.event_id) is not None
        if not known_events[op.event_id]:
            results.append(_sync_result(op.operation_id, "unknown_event",
                                        reason=f"événement {op.event_id} inconnu de ce serveur"))
            continue
        now = datetime.now(UTC)
        try:
            with db.begin_nested():
                status, cursor, message = _apply_operation(db, op, now, notices, pco)
        except SyncRejected as exc:
            if exc.journal:
                security_journal.record(db, **exc.journal)
            results.append(_sync_result(op.operation_id, "rejected", reason=exc.reason))
            continue
        except (KeyError, TypeError, ValueError, IntegrityError, DataError) as exc:
            log.warning("sync operation %s rejected: %r", op.operation_id, exc)
            results.append(_sync_result(op.operation_id, "rejected", reason="données invalides"))
            continue
        except SQLAlchemyError:
            log.exception("sync operation %s failed", op.operation_id)
            results.append(_sync_result(op.operation_id, "retry", reason="erreur passagère du serveur"))
            continue
        if message:
            broadcasts.append((op.event_id, message))
            if message.get("type") == "position.updated":
                broadcasts.extend((op.event_id, x) for x in _geofence(db, op.event_id, op.payload, now))
        if identity is not None and status == "accepted":
            actor = access_logbook.actor_of(op.object_type, op.payload)
            if actor and (op.event_id, actor) not in seen_actors:
                seen_actors.add((op.event_id, actor))
                access_logbook.record_access(db, op.event_id, actor, identity, now)
        reason = _REJECT_REASONS.get(op.object_type) if status == "rejected" else None
        results.append(_sync_result(op.operation_id, status, cursor, reason))
    for event_id, batch in notices.items():
        for payload in routes_api.flush_notices(db, event_id, batch, datetime.now(UTC)):
            broadcasts.append((event_id, payload))
    db.commit()
    for event_id, payload in broadcasts:
        await manager.broadcast(event_id, payload)
        # Devices with SARCADE closed: woken by a signal without content.
        if payload.get("type") == "message.created":
            task = asyncio.create_task(wakeup_service.wake_for_message(event_id, payload.get("data") or {}))
            _wake_tasks.add(task)
            task.add_done_callback(_wake_tasks.discard)
    return results


@app.get("/api/v0.1/events/{event_id}/sync/changes")
def sync_changes(event_id: str, after: int = Query(0, ge=0), limit: int = Query(500, ge=1, le=2000), db: Session = Depends(get_db)):
    rows = db.scalars(select(SyncOperationRow).where(
        SyncOperationRow.event_id == event_id, SyncOperationRow.seq > after,
        SyncOperationRow.status == "accepted",
    ).order_by(SyncOperationRow.seq).limit(limit)).all()
    return {
        "changes": [{"cursor": str(r.seq), "operation_id": r.operation_id, "object_id": r.object_id,
                     "object_type": r.object_type, "action": r.action, "payload": r.payload,
                     "server_time": r.server_time} for r in rows],
        "next_cursor": str(rows[-1].seq) if rows else str(after),
    }


@app.get("/api/v0.1/events/{event_id}/map-features")
def list_map_features(event_id: str, include_deleted: bool = Query(False), db: Session = Depends(get_db)):
    stmt = select(MapFeatureRow).where(MapFeatureRow.event_id == event_id)
    if not include_deleted:
        stmt = stmt.where(MapFeatureRow.deleted_at.is_(None))
    rows = db.scalars(stmt.order_by(MapFeatureRow.created_at)).all()
    return [features.feature_dict(r) for r in rows]


@app.get("/api/v0.1/events/{event_id}/map-features.geojson")
def map_features_geojson(event_id: str, db: Session = Depends(get_db)):
    rows = db.scalars(select(MapFeatureRow).where(
        MapFeatureRow.event_id == event_id, MapFeatureRow.deleted_at.is_(None)
    ).order_by(MapFeatureRow.created_at)).all()
    return JSONResponse({"type": "FeatureCollection", "features": [features.feature_geojson(r) for r in rows]},
                        media_type="application/geo+json")


@app.get("/api/v0.1/events/{event_id}/messages")
def list_messages(event_id: str, limit: int = Query(200, ge=1, le=2000), db: Session = Depends(get_db)):
    rows = db.scalars(select(MessageRow).where(MessageRow.event_id == event_id).order_by(MessageRow.created_at.desc()).limit(limit)).all()
    return [msg_deletion.public_dict(r) for r in reversed(rows)]


@app.get("/api/v0.1/events/{event_id}/messages/deleted")
def list_deleted_messages(event_id: str, _pco: str | None = Depends(roles_api.require_pco), db: Session = Depends(get_db)):
    """Deleted messages with their original content: PCO and administrators only."""
    rows = db.scalars(select(MessageRow).where(MessageRow.event_id == event_id, MessageRow.deleted_at.is_not(None))
                      .order_by(MessageRow.deleted_at)).all()
    return [msg_deletion.pco_dict(r) for r in rows]

@app.get("/api/v0.1/events/{event_id}/logbook")
def list_logbook(event_id: str, after: int = Query(0, ge=0), limit: int = Query(500, ge=1, le=2000), db: Session = Depends(get_db)):
    rows = db.scalars(select(LogbookRow).where(LogbookRow.event_id == event_id,LogbookRow.seq > after).order_by(LogbookRow.seq).limit(limit)).all()
    return [{"seq":r.seq,"kind":r.kind,"object_id":r.object_id,"actor_id":r.actor_id,"summary":r.summary,"time":r.time} for r in rows]


# Keep last: the web client is mounted at "/" and must not shadow API routes.
from .web_client import mount_web_client  # noqa: E402

mount_web_client(app, os.getenv("SARCADE_WEB_ROOT"))
