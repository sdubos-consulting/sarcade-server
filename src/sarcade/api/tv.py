"""Smart TV and big screens (« Affichage sur Smart TV et grand écran »).

- ``/tv`` (and ``/`` of tv.sarcade.org): full-screen read-only page for the TV (served on tv.sarcade.org,
  without Cloudflare Access): asks a pairing code, then shows the event.
- ``/tv/approve?code=…``: page opened by the QR code on a phone signed in
  (Access); any operator chooses the event and approves.
- API: pairing, approval and revocation by any operator (decision of the
  porteur, 29 Oct 2026: showing SARCADE on a TV is not a PCO right), state
  read by the TV, list of the screens of an event."""
import hashlib
import html
import secrets
import time
import uuid
from pathlib import Path
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from sarcade import roles, tv
from sarcade.basemaps import catalog
from sarcade.events import settings as event_settings
from sarcade.db.models import CustomBasemapRow, EventRow, LogbookRow, MapFeatureRow, MessageRow, PositionRow, ReferenceSiteRow
from sarcade.features import service as features
from sarcade.weather import service as weather_service
from sarcade.updates.service import get_value, set_value

from .deps import get_db
from .roles import checked_email
from .serializers import position_dict, reference_site_dict

router = APIRouter()
KEY = "displays"
NO_STORE = {"Cache-Control": "no-store"}

# Paths served on the TV address (tv.*): nothing else answers there.
TV_HOST_PATHS = ("/tv", "/api/v0.1/tv/pair", "/api/v0.1/tv/state", "/health")


def tv_host_allows(path: str) -> bool:
    if path.startswith("/tv/approve") or path.startswith("/api/v0.1/tv/approve"):
        return False
    return any(path == p or path.startswith(p + "/") for p in TV_HOST_PATHS)


def _displays(db: Session) -> list[dict]:
    return list(get_value(db, KEY, []) or [])


def _actor(request: Request) -> str:
    """Who approved or revoked a screen, for the logbook: the email checked
    by Cloudflare Access, else « opérateur » (local network)."""
    return (checked_email(request) or "opérateur")[:64]


def _client(request: Request) -> str:
    return request.headers.get("cf-connecting-ip") or (request.client.host if request.client else "?")


class PairIn(BaseModel):
    """Default name of the screen, from its browser (« TV Samsung ·
    1920×1080 »); optional (former TV pages send nothing)."""
    name: str | None = Field(default=None, max_length=200)


@router.post("/api/v0.1/tv/pair")
def pair(request: Request, body: PairIn | None = None):
    if not tv.store.allowed(_client(request)):
        raise HTTPException(status_code=429, detail="too_many_requests")
    try:
        p = tv.store.new(ip=_client(request), name=tv.clean_name(body.name if body else ""))
    except OverflowError:
        raise HTTPException(status_code=503, detail="busy")
    return {"pair_id": p.pair_id, "code": p.code, "expires_in": tv.CODE_TTL_S}


@router.get("/api/v0.1/tv/pair/{pair_id}")
def pair_status(pair_id: str, db: Session = Depends(get_db)):
    p = tv.store.status(pair_id)
    if p is None:
        raise HTTPException(status_code=404, detail="expired")
    if p.token is None:
        return {"status": "pending"}
    token, event_id = p.token, p.event_id
    tv.store.pairings.pop(pair_id, None)
    tv.store.by_code.pop(p.code, None)
    now = time.time()
    displays = [d for d in _displays(db) if d.get("expires", 0) > now - 7 * 24 * 3600]
    displays.append({"id": str(uuid.uuid4()), "hash": tv.token_hash(token), "event_id": event_id,
                     "created": now, "expires": now + tv.TOKEN_TTL_S, "revoked": False, "code": p.code,
                     "owner": p.approved_by, "name": p.name})
    set_value(db, KEY, displays)
    db.commit()
    return {"status": "approved", "token": token, "event_id": event_id}


@router.get("/api/v0.1/tv/nearby")
def nearby(request: Request):
    """Codes shown by TVs behind the same public address as the caller
    (« validation automatique depuis le même réseau », 30 Oct 2026): for
    every participant of the event (decision of the porteur, 30 Oct 2026,
    13 h 19; formerly PCO and administrators only), like approval itself;
    the TV address (tv.*) does not serve this route. Nothing is approved
    here, the operator confirms the code shown."""
    return [{"code": p.code, "name": p.name, "expires_in": max(0, int(tv.CODE_TTL_S - (time.time() - p.created)))}
            for p in tv.store.nearby(_client(request))]


@router.get("/api/v0.1/tv/code/{code}")
def pending_code(code: str):
    """Default name of a TV still showing this code, proposed in the
    application before approval (« nom choisi à l'appairage »)."""
    p = tv.store.pending(code[:12])
    if p is None:
        raise HTTPException(status_code=404, detail="unknown_code")
    return {"code": p.code, "name": p.name}


class ApproveIn(BaseModel):
    code: str = Field(min_length=4, max_length=12)
    event_id: str = Field(min_length=1, max_length=64)
    # Name chosen at pairing (the default name of the TV when omitted).
    name: str | None = Field(default=None, max_length=200)


@router.post("/api/v0.1/tv/approve")
def approve(body: ApproveIn, request: Request, db: Session = Depends(get_db)):
    actor = _actor(request)
    event = db.get(EventRow, body.event_id)
    if event is None:
        raise HTTPException(status_code=404, detail="event_not_found")
    p = tv.store.approve(body.code, body.event_id, approved_by=checked_email(request))
    if p is None:
        raise HTTPException(status_code=404, detail="unknown_code")
    p.name = tv.clean_name(body.name) or p.name
    db.add(LogbookRow(event_id=body.event_id, kind="display", object_id=None, actor_id=actor,
                      summary=f"Écran d'affichage appairé : {_label(p.name, p.code)}", time=datetime.now(UTC)))
    db.commit()
    return {"approved": True}


def _label(name: str | None, code: str | None) -> str:
    """« Salle PCO (code ABC123) », or the code alone, for the logbook."""
    return f"{name} (code {code})" if name else f"code {code}"


def _display(request: Request, db: Session) -> dict:
    auth = request.headers.get("authorization", "")
    token = auth[8:] if auth.lower().startswith("display ") else ""
    d = tv.valid_display(_displays(db), token) if token else None
    if d is None:
        raise HTTPException(status_code=401, detail="display_token_required")
    return d


def _weather(db: Session, event_id: str) -> dict | None:
    """Weather of the current hour from the cache of the weather service
    (no network call), computed at most every 10 minutes."""
    def compute():
        row = weather_service.cache_get(db, f"event:{event_id}")
        if row is None:
            return None
        vig = weather_service.cache_get(db, f"vigilance:{event_id}")
        return tv.weather_now(row.data, vig.data if vig else None, datetime.now(UTC))
    return tv.weather_cache.get(event_id, compute)


def _stamp(*parts) -> str:
    return hashlib.sha1("|".join(str(p) for p in parts).encode()).hexdigest()[:16]


_TRACES_CACHE: dict[tuple, tuple[float, list]] = {}


def _traces(db: Session, event_id: str, opts: dict, shown: list[dict]) -> list[dict]:
    """Traces the phone shows (« Traces » on: every operator shown;
    otherwise the traces pinned one by one), last positions of the window,
    at most 500 per device; read again at most once a minute."""
    layers = opts["layers"]
    ids = [p["device_id"] for p in shown] if layers.get("traces") else [i for i in opts.get("traces_ids", [])]
    if not ids:
        return []
    window = opts.get("traces_window", 60)
    key = (event_id, window, tuple(sorted(ids)))
    hit = _TRACES_CACHE.get(key)
    if hit and time.time() - hit[0] < 60:
        return hit[1]
    since = datetime.now(UTC) - timedelta(minutes=window)
    ranked = select(PositionRow.id, func.row_number().over(
        partition_by=PositionRow.device_id, order_by=PositionRow.time.desc()).label("rn")
    ).where(PositionRow.event_id == event_id, PositionRow.device_id.in_(ids), PositionRow.time >= since).subquery()
    rows = db.scalars(select(PositionRow).join(ranked, PositionRow.id == ranked.c.id)
                      .where(ranked.c.rn <= tv.MAX_TRACE_POINTS).order_by(PositionRow.device_id, PositionRow.time)).all()
    by: dict[str, dict] = {}
    for r in rows:
        t = by.setdefault(r.device_id, {"device_id": r.device_id, "aprs": r.source == "aprs", "points": []})
        t["points"].append([round(r.lat, 6), round(r.lon, 6)])
    out = [t for t in by.values() if len(t["points"]) > 1]
    if len(_TRACES_CACHE) > 200:
        _TRACES_CACHE.clear()
    _TRACES_CACHE[key] = (time.time(), out)
    return out


def _map_layers(db: Session, event_id: str, opts: dict, fr: str | None, rr: str | None) -> dict:
    """Drawings, bearings and reference sites, sent again only when they
    changed since the revisions the TV already has (``fr``, ``rr``)."""
    out: dict = {}
    layers = opts["layers"]
    count, last = db.execute(select(func.count(), func.max(MapFeatureRow.updated_at))
                             .where(MapFeatureRow.event_id == event_id)).one()
    out["features_rev"] = _stamp(count, last, layers["drawings"], layers["bearings"])
    if out["features_rev"] != fr:
        drawings, bearings = [], []
        if layers["drawings"] or layers["bearings"]:
            rows = db.scalars(select(MapFeatureRow).where(MapFeatureRow.event_id == event_id,
                                                          MapFeatureRow.deleted_at.is_(None))
                              .order_by(MapFeatureRow.created_at)).all()
            for r in rows:
                f = features.feature_dict(r)
                if r.kind == "bearing":
                    b = tv.tv_bearing(f)
                    if b:
                        bearings.append(b)
                else:
                    drawings.append(tv.tv_drawing(f))
        out["drawings"], out["bearings"] = drawings, bearings
    cats = [c for c, on in (("HIGH_POINT", layers["highpoints"]), ("RELAY", layers["relays"])) if on]
    count, last = (db.execute(select(func.count(), func.max(ReferenceSiteRow.imported_at))
                              .where(ReferenceSiteRow.status == "active", ReferenceSiteRow.category.in_(cats))).one()
                   if cats else (0, None))
    out["references_rev"] = _stamp(count, last, *cats)
    if out["references_rev"] != rr:
        rows = db.scalars(select(ReferenceSiteRow).where(ReferenceSiteRow.status == "active",
                                                         ReferenceSiteRow.category.in_(cats))
                          .limit(2000)).all() if cats else []
        out["references"] = [{k: v for k, v in reference_site_dict(r).items()
                              if k in ("id", "category", "name", "callsign", "lat", "lon")} for r in rows]
    return out


@router.get("/api/v0.1/tv/state")
def state(request: Request, fr: str | None = Query(None, max_length=32), rr: str | None = Query(None, max_length=32),
          db: Session = Depends(get_db)):
    """``fr`` / ``rr``: revisions of the drawings and reference sites the TV
    already has; unchanged layers are not sent again."""
    d = _display(request, db)
    tv.seen[d["id"]] = time.time()
    event = db.get(EventRow, d["event_id"])
    if event is None:
        raise HTTPException(status_code=404, detail="event_not_found")
    ranked = select(PositionRow.id, func.row_number().over(
        partition_by=PositionRow.device_id, order_by=PositionRow.time.desc()).label("rn")
    ).where(PositionRow.event_id == event.id).subquery()
    rows = db.scalars(select(PositionRow).join(ranked, PositionRow.id == ranked.c.id).where(ranked.c.rn == 1)).all()
    positions = [{k: v for k, v in position_dict(r).items() if k in ("device_id", "lat", "lon", "time", "source", "callsign")}
                 for r in rows]
    msgs = db.scalars(select(MessageRow).where(MessageRow.event_id == event.id)
                      .order_by(MessageRow.created_at.desc()).limit(60)).all()
    snap = tv.snapshot({"id": event.id, "name": event.name}, positions, [_msg(m) for m in reversed(msgs)])
    snap.update(_alert(db, event.id, d))
    view = d.get("view")
    layers = _map_layers(db, event.id, tv.display_options(view), fr, rr)
    snap = tv.apply_view(snap, view, datetime.now(UTC), weather=_weather(db, event.id),
                         drawings=layers.get("drawings"), bearings=layers.get("bearings"),
                         references=layers.get("references"))
    snap["features_rev"], snap["references_rev"] = layers["features_rev"], layers["references_rev"]
    # Traces chosen on the phone (1 Nov 2026).
    snap["traces"] = _traces(db, event.id, tv.display_options(view), snap["positions"])
    snap["view"] = view
    # « Messages : afficher / masquer » of this screen, applied at once.
    snap["options"] = {**snap["options"], "messages": d.get("messages", snap["options"]["messages"])}
    snap["default_base"] = _default_base(db, event)
    snap.update(_screen(d, snap))
    return snap


def _screen(d: dict, snap: dict, now: float | None = None) -> dict:
    """Day / night of the page (pushed with the view, « auto » before any
    send), name of the screen and the « Identifier » request."""
    now = now if now is not None else time.time()
    ident = d.get("identify") or 0
    return {"night": tv.theme_night(snap["options"].get("theme"), snap.get("weather"), datetime.fromtimestamp(now, UTC)),
            "name": d.get("name") or "",
            "identify": ({"id": str(ident), "name": d.get("name") or d.get("code") or "", "seconds": tv.IDENTIFY_S}
                         if now - ident < IDENTIFY_WINDOW_S else None)}


def _default_base(db: Session, event: EventRow) -> dict | None:
    """Base map of the event (default view of a screen), https only."""
    bid = event_settings.merged(event.settings).get("basemap") or "osm"
    item = next((b for b in catalog.BUILT_IN if b["id"] == bid), None)
    if item is None:
        row = db.get(CustomBasemapRow, bid)
        item = dict(row.data) if row is not None else None
    url = (item or {}).get("url") or ""
    if not (url.startswith("https://") and all(t in url for t in ("{z}", "{x}", "{y}"))
            and not any(ch in url for ch in "\"'<> ")):
        return None
    return {"tile_url": url, "attribution": str(item.get("attribution") or "")[:200], "id": str(bid)[:40]}


def _msg(m: MessageRow) -> dict:
    return {"sender_id": m.sender_id, "recipient_ids": m.recipient_ids or [], "priority": m.priority,
            "body": m.body, "created_at": m.created_at}


def _alert(db: Session, event_id: str, display: dict) -> dict:
    """Red banner and SOS reminder of a screen, from the last immediate and
    urgent messages (distress and end of distress)."""
    rows = db.scalars(select(MessageRow).where(MessageRow.event_id == event_id,
                                               MessageRow.priority.in_(("immediate", "urgent")))
                      .order_by(MessageRow.created_at.desc()).limit(300)).all()
    return tv.alert_state([_msg(m) for m in reversed(rows)], datetime.now(UTC), display.get("alert_cleared"))


@router.get("/api/v0.1/events/{event_id}/displays")
def list_displays(event_id: str, request: Request, db: Session = Depends(get_db)):
    now = time.time()
    email = checked_email(request)
    admin = email is not None and "admin" in roles.roles_of(db, email)
    out = []
    for d in _displays(db):
        if d.get("event_id") != event_id:
            continue
        active = not d.get("revoked") and d["expires"] > now
        view = d.get("view") or {}
        out.append({"id": d["id"], "code": d.get("code"), "name": d.get("name") or "",
                    "last_seen": tv.seen.get(d["id"]), "created": d["created"], "expires": d["expires"],
                    "active": active, "owner": d.get("owner"), "revoked": bool(d.get("revoked")),
                    "messages": d.get("messages", tv.display_options(view or None)["messages"]),
                    "view_from": view.get("from") or None,
                    "alert": active and _alert(db, event_id, d)["alert"] is not None,
                    "can_clear_alert": _may_clear(d, email, admin),
                    "mine": _is_mine(d, email)})
    return out


def _is_mine(display: dict, email: str | None) -> bool:
    """Screen paired by the caller (« Écrans » button of the map, lot 11).
    Without a checked email (local network without Access) every screen
    counts, as for the other rights."""
    return email is None or display.get("owner") == email


def _may_clear(display: dict, email: str | None, admin: bool) -> bool:
    """The person who paired the screen (email checked by Access) or an
    administrator. Without a checked email (local network without Access)
    the server cannot know who calls: allowed, as for the other rights."""
    if email is None:
        return True
    return admin or (display.get("owner") or "").lower() == email


@router.post("/api/v0.1/events/{event_id}/displays/{display_id}/alert/clear")
def clear_alert(event_id: str, display_id: str, request: Request, db: Session = Depends(get_db)):
    """« Retirer le bandeau SOS » of one screen: the messages received up to
    now no longer show the red banner there; a new immediate message shows
    it again, and the SOS reminder of the top bar stays until the end of
    the distress."""
    email = checked_email(request)
    admin = email is not None and "admin" in roles.roles_of(db, email)
    displays = _displays(db)
    for d in displays:
        if d["id"] == display_id and d.get("event_id") == event_id and not d.get("revoked"):
            if not _may_clear(d, email, admin):
                raise HTTPException(status_code=403, detail="owner_or_admin_required")
            d["alert_cleared"] = time.time()
            set_value(db, KEY, displays)
            db.add(LogbookRow(event_id=event_id, kind="display", object_id=None, actor_id=_actor(request),
                              summary=f"Bandeau SOS retiré de l'écran ({_label(d.get('name'), d.get('code'))})", time=datetime.now(UTC)))
            db.commit()
            return {"cleared": True}
    raise HTTPException(status_code=404, detail="display_not_found")


@router.delete("/api/v0.1/events/{event_id}/displays/{display_id}", status_code=204)
def revoke_display(event_id: str, display_id: str, request: Request, db: Session = Depends(get_db)):
    actor = _actor(request)
    displays = _displays(db)
    for d in displays:
        if d["id"] == display_id and d.get("event_id") == event_id:
            d["revoked"] = True
            set_value(db, KEY, displays)
            db.add(LogbookRow(event_id=event_id, kind="display", object_id=None, actor_id=actor,
                              summary=f"Écran d'affichage révoqué ({_label(d.get('name'), d.get('code'))})", time=datetime.now(UTC)))
            db.commit()
            return
    raise HTTPException(status_code=404, detail="display_not_found")


class ViewIn(BaseModel):
    """View of a phone copied onto a TV (« Envoyer ma vue », 29 Oct 2026);
    ``view`` null gives the TV back its automatic framing. ``live``
    (« Suivre ma vue », lot 11): the view follows the map of the sender;
    only the first view of a follow goes to the logbook."""
    view: dict | None = None
    live: bool = False
    # « Colonne des messages » ticked or unticked on purpose when sending;
    # otherwise the screen keeps its own choice (1 Nov 2026: a followed view
    # brought back the messages hidden on the screen).
    messages: bool | None = None


@router.put("/api/v0.1/events/{event_id}/displays/{display_id}/view")
def send_view(event_id: str, display_id: str, body: ViewIn, request: Request, db: Session = Depends(get_db)):
    try:
        view = tv.clean_view(body.view)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    displays = _displays(db)
    for d in displays:
        if d["id"] == display_id and d.get("event_id") == event_id and not d.get("revoked"):
            following = bool(body.live and view is not None and d.get("live"))
            d["view"] = view
            d["live"] = bool(body.live and view is not None)
            if body.messages is not None:
                d["messages"] = body.messages
            set_value(db, KEY, displays)
            if following:
                db.commit()
                return {"view": view}
            text = ("Vue suivie en direct sur l'écran" if body.live else "Vue envoyée sur l'écran") if view \
                else "Vue par défaut rendue à l'écran"
            db.add(LogbookRow(event_id=event_id, kind="display", object_id=None, actor_id=_actor(request),
                              summary=f"{text} ({_label(d.get('name'), d.get('code'))})", time=datetime.now(UTC)))
            db.commit()
            return {"view": view}
    raise HTTPException(status_code=404, detail="display_not_found")


class MessagesIn(BaseModel):
    messages: bool


@router.put("/api/v0.1/events/{event_id}/displays/{display_id}/messages")
def display_messages(event_id: str, display_id: str, body: MessagesIn, request: Request, db: Session = Depends(get_db)):
    """« Messages : afficher / masquer » in the menu of a screen: applied at
    once, without sending the view again (card « Envoyer ma vue » par
    écran, option A of 30 Oct 2026)."""
    displays = _displays(db)
    for d in displays:
        if d["id"] == display_id and d.get("event_id") == event_id and not d.get("revoked"):
            d["messages"] = body.messages
            set_value(db, KEY, displays)
            text = "Messages affichés sur l'écran" if body.messages else "Messages masqués sur l'écran"
            db.add(LogbookRow(event_id=event_id, kind="display", object_id=None, actor_id=_actor(request),
                              summary=f"{text} ({_label(d.get('name'), d.get('code'))})", time=datetime.now(UTC)))
            db.commit()
            return {"messages": body.messages}
    raise HTTPException(status_code=404, detail="display_not_found")


# The TV asks its state every 10 s: « Identifier » stays readable that long,
# the TV shows the name 5 s once per request.
IDENTIFY_WINDOW_S = 15


class NameIn(BaseModel):
    name: str = Field(max_length=200)


@router.put("/api/v0.1/events/{event_id}/displays/{display_id}/name")
def rename_display(event_id: str, display_id: str, body: NameIn, request: Request, db: Session = Depends(get_db)):
    """« Renommer » in the menu of a screen (any operator, as the other
    actions on a screen)."""
    name = tv.clean_name(body.name)
    if not name:
        raise HTTPException(status_code=422, detail="name_required")
    displays = _displays(db)
    for d in displays:
        if d["id"] == display_id and d.get("event_id") == event_id and not d.get("revoked"):
            old = _label(d.get("name"), d.get("code"))
            d["name"] = name
            set_value(db, KEY, displays)
            db.add(LogbookRow(event_id=event_id, kind="display", object_id=None, actor_id=_actor(request),
                              summary=f"Écran renommé : {old} → {name}", time=datetime.now(UTC)))
            db.commit()
            return {"name": name}
    raise HTTPException(status_code=404, detail="display_not_found")


@router.post("/api/v0.1/events/{event_id}/displays/{display_id}/identify")
def identify_display(event_id: str, display_id: str, db: Session = Depends(get_db)):
    """« Identifier »: the TV shows its name in very large letters for 5 s
    (at its next request, within 10 s)."""
    displays = _displays(db)
    for d in displays:
        if d["id"] == display_id and d.get("event_id") == event_id and not d.get("revoked"):
            d["identify"] = time.time()
            set_value(db, KEY, displays)
            db.commit()
            return {"identify": True, "seconds": tv.IDENTIFY_S}
    raise HTTPException(status_code=404, detail="display_not_found")


@router.get("/tv/approve")
def approve_page(db: Session = Depends(get_db)):
    """The code comes from the QR code (?code=…): read by the page script,
    never written into the HTML by the server."""
    events = db.scalars(select(EventRow).where(EventRow.ended_at.is_(None)).order_by(EventRow.created_at.desc())).all()
    options = "".join(f'<option value="{html.escape(e.id)}">{html.escape(e.name or "")}</option>' for e in events)
    return HTMLResponse(_APPROVE.replace("__OPTIONS__", options), headers=NO_STORE)


_LOGO = Path(__file__).with_name("tv_logo.png")


# QR code (qrcodejs 1.0.0) and map (Leaflet 1.9.4) served by SARCADE, not
# by public CDNs: TVs on a network without Internet or behind a filter
# (card « TV Samsung : ni QR code ni code », 31 Oct 2026).
_STATIC = Path(__file__).with_name("tv_static")
_STATIC_TYPES = {".js": "text/javascript", ".css": "text/css", ".png": "image/png"}
_STATIC_FILES = {p.relative_to(_STATIC).as_posix(): p for p in _STATIC.rglob("*") if p.suffix in _STATIC_TYPES}


@router.get("/tv/static/{name:path}")
def tv_static(name: str):
    path = _STATIC_FILES.get(name)
    if path is None:
        raise HTTPException(status_code=404, detail="not_found")
    return FileResponse(path, media_type=_STATIC_TYPES[path.suffix], headers={"Cache-Control": "public, max-age=604800"})


@router.get("/tv/logo.png")
def tv_logo():
    return FileResponse(_LOGO, media_type="image/png", headers={"Cache-Control": "public, max-age=86400"})


@router.get("/tv")
def tv_page():
    return HTMLResponse(_TV.replace("__NONCE__", secrets.token_hex(4)), headers=NO_STORE)


_APPROVE = """<!doctype html><html lang="fr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><meta name="robots" content="noindex">
<title>SARCADE · écran</title>
<style>body{font-family:system-ui,sans-serif;max-width:420px;margin:32px auto;padding:0 16px;color:#022a58}
.logo{display:block;width:180px;margin:0 auto 18px}
h1{font-size:22px}label{display:block;margin:14px 0 4px;font-weight:600}input,select,button{font-size:17px;width:100%;padding:10px;box-sizing:border-box;border-radius:10px;border:1px solid #b9c6d2}
button{background:#022a58;color:#fff;border:0;margin-top:18px;font-weight:600}#r{margin-top:16px}</style></head><body>
<img class="logo" src="/tv/logo.png" alt="SARCADE"><h1>Afficher SARCADE sur un écran</h1>
<p>Choisissez l'événement à afficher sur la TV, puis validez.</p>
<label for="c">Code affiché sur la TV</label><input id="c" autocapitalize="characters" maxlength="12">
<label for="n">Nom de l'écran</label><input id="n" maxlength="60">
<label for="e">Événement</label><select id="e">__OPTIONS__</select>
<button id="b">Afficher sur la TV</button><p id="r"></p>
<script>document.getElementById('c').value=(new URLSearchParams(location.search).get('code')||'').slice(0,12);
// Name proposed by the TV (« TV Samsung · 1920×1080 »), kept if typed over.
(async()=>{const c=document.getElementById('c').value;if(!c)return;try{const r=await fetch('/api/v0.1/tv/code/'+encodeURIComponent(c));
if(r.ok){const j=await r.json();const n=document.getElementById('n');if(!n.value)n.value=j.name||'';}}catch(e){}})();
document.getElementById('b').onclick=async()=>{const r=await fetch('/api/v0.1/tv/approve',{method:'POST',
headers:{'content-type':'application/json'},body:JSON.stringify({code:document.getElementById('c').value,event_id:document.getElementById('e').value,name:document.getElementById('n').value})});
document.getElementById('r').textContent=r.ok?'C\\u2019est fait : la TV affiche l\\u2019événement.':(r.status===401||r.status===403?'Connectez-vous à SARCADE, puis réessayez.':'Code inconnu ou expiré.');};</script>
</body></html>"""

_TV = """<!doctype html><html lang="fr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><meta name="robots" content="noindex">
<title>SARCADE · affichage</title>
<link rel="stylesheet" href="/tv/static/leaflet.css">
<style>html,body{margin:0;height:100%;overflow:hidden;background:#03152b;color:#eef3f9;-webkit-text-size-adjust:100%;text-size-adjust:100%;
font-family:Roboto,'Noto Sans','Helvetica Neue',Arial,Helvetica,sans-serif;letter-spacing:normal}
#pair{display:grid;grid-template-columns:auto auto;justify-content:center;align-items:center;grid-gap:8vw;gap:8vw;height:100%;padding:0 8vw;box-sizing:border-box;
background:radial-gradient(ellipse at 20% 30%,#0a3266 0%,#03152b 60%)}
.left{display:flex;flex-direction:column;min-width:0}.left>*+*{margin-top:3.2vh}
.logo{background:#fff;border-radius:22px;padding:14px 24px;width:max-content}.logo img{height:20vh;max-height:170px;display:block}
.lead{font-size:4.2vh;font-size:min(4.2vh,38px);font-weight:700}
#code{font-family:ui-monospace,'SF Mono',Menlo,Consolas,monospace;font-size:12vh;font-size:min(16vh,9vw,150px);letter-spacing:.16em;font-weight:800;
color:#ffb020;border-left:8px solid #ff4d1a;padding-left:3vw;line-height:1}
.how{font-size:2.6vh;font-size:min(2.6vh,24px);line-height:1.45;color:#c4d1e0;max-width:32em}.how b{color:#fff}
#qr{background:#fff;padding:22px;border-radius:24px;box-shadow:0 12px 40px rgba(0,0,0,.35)}#qr img,#qr canvas{display:block}
#hint{font-size:18px;color:#ffb020;min-height:1em}
/* Old TV browsers: no gap in flex layouts (Chromium < 84), margins instead. */
header>*+*{margin-left:22px}.stats>*+*{margin-left:26px}#wx>*+*{margin-left:6px}#legend div>*+*{margin-left:7px}
@media (max-aspect-ratio:1/1){#pair{grid-template-columns:1fr;justify-items:center;text-align:center;padding:6vh 6vw;align-content:center}
.left{align-items:center}#code{border-left:0;padding-left:0}}
/* Day / night of the page (« Envoyer ma vue » pushes the mode of the phone, 30 Oct 2026). */
#app{--bg:#03152b;--fg:#eef3f9;--strong:#fff;--muted:#c4d1e0;--h2:#b9c6d2;--line:#1e2c38;--line2:#16212b;--panel:rgba(3,21,43,.88);--amber:#ffb020;--red:#ff4d5e;--ok:#3ddc84;--shadow:#000}
#app.day{--bg:#f4f7fb;--fg:#0b1a2b;--strong:#000;--muted:#2c3e52;--h2:#33475c;--line:#b9c6d2;--line2:#d5dee8;--panel:rgba(255,255,255,.94);--amber:#8a4b00;--red:#b3141f;--ok:#137a3a;--shadow:#fff}
#app{display:none;height:100%;grid-template-columns:1fr minmax(300px,17%);grid-template-rows:68px 1fr;background:var(--bg);color:var(--fg)}
#app.nomsg{grid-template-columns:1fr}#app.nomsg aside{display:none}
.night .leaflet-tile-pane{filter:invert(1) hue-rotate(180deg) brightness(.95) contrast(.9) saturate(.6)}
header{grid-column:1/-1;display:flex;align-items:center;padding:0 20px 0 10px;background:var(--bg);border-bottom:1px solid var(--line);overflow:hidden;min-width:0}
.hlogo{background:#fff;border-radius:10px;padding:4px 10px;display:flex}.hlogo img{height:50px;display:block}
#title{font-size:26px;font-weight:700;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;min-width:0}
.stats{display:flex;align-items:center;margin-left:auto;font-size:19px;color:var(--muted);white-space:nowrap;min-width:0;overflow:hidden}.stats b{color:var(--strong);font-size:22px}
.dot{display:inline-block;width:11px;height:11px;border-radius:50%;background:var(--ok);margin-right:7px}#link.lost{color:var(--red)}#link.lost .dot{background:var(--red)}
#clock{font-size:34px;font-weight:700;font-variant-numeric:tabular-nums;margin-left:10px;color:var(--strong)}
#sun{font-size:17px;color:var(--muted);line-height:1.25;font-variant-numeric:tabular-nums}#sun b{color:var(--amber);font-weight:700}
#map{height:100%;min-width:0}aside{overflow:hidden;padding:12px 14px;border-left:1px solid var(--line);font-size:17px;min-width:0;overflow-wrap:anywhere;word-wrap:break-word}
h2{font-size:16px;color:var(--h2);text-transform:uppercase;letter-spacing:1px;margin:14px 0 6px}
.m{padding:6px 0;border-bottom:1px solid var(--line2)}.u{color:var(--amber)}.i{color:var(--red);font-weight:700}
#wx{display:flex;align-items:center;font-size:28px;font-weight:700;color:var(--strong);margin-left:4px;white-space:nowrap}
.vig{display:inline-block;width:12px;height:12px;border-radius:50%;margin-left:2px}.vig.orange{background:#ff8c1a}.vig.red{background:#ff4d5e}
.ex{background:#ffb020;color:#03152b;font-weight:800;border-radius:4px;padding:0 5px;font-size:12px}
.zlab span{display:inline-block;white-space:nowrap;background:var(--panel);color:var(--fg);border-left:4px solid;padding:2px 7px;font-size:14px;border-radius:4px}
#mapBox{position:relative;min-width:0;height:100%}
#legend{display:none;position:absolute;left:10px;bottom:10px;z-index:900;background:var(--panel);color:var(--fg);border:1px solid var(--line);border-radius:8px;padding:6px 10px;font-size:15px;max-width:40%}
#legend div{display:flex;align-items:center;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;line-height:1.6}
#legend i{display:inline-block;width:22px;height:4px;border-radius:2px;flex:none}#legend .zr{width:14px;height:14px;border-radius:50%;background:rgba(255,77,94,.15);border:2px solid}
#from{display:none;position:absolute;right:10px;bottom:10px;z-index:900;background:var(--panel);color:var(--muted);font-size:13px;border-radius:6px;padding:2px 8px}
.txt span{white-space:nowrap;font-size:16px;font-weight:700;text-shadow:0 0 3px var(--shadow)}
.lowzoom .rlab{display:none}
#sos{display:none;background:#ff4d5e;color:#fff;font-weight:800;font-size:16px;border-radius:999px;padding:3px 10px;animation:blink 1.2s steps(2,start) infinite}
@keyframes blink{to{visibility:hidden}}
#ident{display:none;position:fixed;top:0;right:0;bottom:0;left:0;z-index:3000;align-items:center;justify-content:center;text-align:center;padding:4vw;box-sizing:border-box;
background:rgba(3,21,43,.92);color:#fff;font-size:14vw;font-size:min(14vw,22vh);font-weight:800;line-height:1.1;overflow-wrap:anywhere}
#alert{display:none;position:fixed;left:0;right:0;top:0;padding:18px;background:#ff4d5e;color:#fff;font-size:30px;font-weight:700;text-align:center;z-index:2000}
</style></head><body>
<div id="pair"><div class="left"><div class="logo"><img src="/tv/logo.png" alt="SARCADE"></div>
<div class="lead">Afficher SARCADE sur cet écran</div><div id="code">······</div>
<div class="how">Dans l'application SARCADE : <b>Outils › Afficher sur une TV</b>, puis scannez le QR code ou saisissez ce code.</div>
<div id="hint"></div></div><div id="qr"></div></div>
<div id="app"><header><div class="hlogo"><img src="/tv/logo.png" alt="SARCADE"></div><div id="title"></div><div class="stats"><span><b id="nOps">0</b> opérateurs actifs</span><span><b id="nAprs">0</b> stations APRS</span><span id="link"><span class="dot"></span><span id="linkTxt">Connexion…</span></span></div><span id="sos" title="Détresse en cours">SOS</span><div id="wx"></div><div id="sun"></div><div id="clock">--:--</div></header><div id="mapBox"><div id="map"></div><div id="legend"></div><div id="from"></div></div><aside><div id="msgBox"><h2>Messages</h2><div id="msgs"></div></div></aside></div>
<div id="alert"></div><div id="ident"></div>
<script>
// Plain old script: if the page script cannot run on this browser (too old
// TV), say so after 10 s instead of a silent screen with « ······ ».
var sarcadeStarted=false;
setTimeout(function(){
  if(window.sarcadeStarted)return;
  var m=navigator.userAgent.match(/Chrome\\/(\\d+)/);
  document.getElementById('hint').textContent='Le navigateur de cet écran n\\u2019a pas pu lancer SARCADE'+(m?' (moteur Chrome '+m[1]+')':'')+
    '. Mettez à jour le navigateur de la TV, ou branchez un autre appareil (clé HDMI, ordinateur).';
},10000);
</script>
<script src="/tv/static/qrcode.min.js"></script>
<script src="/tv/static/leaflet.js"></script>
<script>
window.sarcadeStarted=true;
const $=id=>document.getElementById(id);let token=localStorage.getItem('sarcade_tv_token');let map,markers={};
const appHost=location.hostname.replace(/^tv\\./,'app.');
// Old TV browsers (Samsung Tizen, older LG webOS: Chromium 56 and later):
// syntax of 2017 only in this page, no nullish operator, no optional
// chaining, no object spread (card « TV Samsung : ni QR code ni code »,
// 31 Oct 2026); tests/test_tv_page_syntax.py checks it.
function nz(){for(var i=0;i<arguments.length;i++){var v=arguments[i];if(v!==null&&v!==undefined)return v;}return undefined;}
const esc=v=>String(nz(v,'')).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
// Station name without the technical prefix: callsign for APRS, else the terminal.
function label(p){return p.callsign||String(p.device_id||'').replace(/^aprs:/,'');}
// Labels that would overlap are hidden (operators first, then APRS), recomputed on zoom.
function declutter(){if(!map)return;const kept=[];
const ms=Object.values(markers).sort((a,b)=>(a._aprs?1:0)-(b._aprs?1:0));
for(const m of ms){const pt=map.latLngToContainerPoint(m.getLatLng());
const clash=kept.some(q=>Math.abs(q.x-pt.x)<110&&Math.abs(q.y-pt.y)<22);
if(clash)m.closeTooltip();else{m.openTooltip();kept.push(pt);}}}
function hm(t){const d=new Date(t);return d.toLocaleTimeString('fr-FR',{hour:'2-digit',minute:'2-digit'});}
// Default name of this screen, from its browser (« TV Samsung · 1920×1080 »):
// brand or system announced in the user agent, and the resolution.
function tvName(){const u=navigator.userAgent||'';const B=[[/samsung|tizen/i,'TV Samsung'],[/web0s|webos|\blg\b|netcast/i,'TV LG'],
[/bravia|sony/i,'TV Sony'],[/philips|nettv/i,'TV Philips'],[/hisense|vidaa/i,'TV Hisense'],[/panasonic|viera/i,'TV Panasonic'],
[/\btcl\b/i,'TV TCL'],[/xiaomi|mitv/i,'TV Xiaomi'],[/\baft|fire tv/i,'Fire TV'],[/roku/i,'Roku'],[/apple ?tv|tvos/i,'Apple TV'],
[/google ?tv|android ?tv|\bcrkey/i,'Android TV'],[/smart-?tv|hbbtv/i,'Smart TV'],[/cros/i,'Écran ChromeOS'],[/windows/i,'Écran Windows'],
[/mac os x|macintosh/i,'Écran Mac'],[/android/i,'Écran Android'],[/linux/i,'Écran Linux']];
const b=(B.find(x=>x[0].test(u))||[0,'Écran'])[1];const r=window.devicePixelRatio||1;
return b+' · '+Math.round(screen.width*r)+'×'+Math.round(screen.height*r);}
async function pair(){const r=await fetch('/api/v0.1/tv/pair',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({name:tvName()})});if(!r.ok){$('hint').textContent='Réessai dans une minute…';setTimeout(pair,60000);return;}
const p=await r.json();$('code').textContent=p.code;$('qr').innerHTML='';new QRCode($('qr'),{text:'https://'+appHost+'/tv/approve?code='+p.code,width:340,height:340});
const until=Date.now()+p.expires_in*1000;const poll=async()=>{if(Date.now()>until){pair();return;}
const s=await fetch('/api/v0.1/tv/pair/'+p.pair_id);if(s.ok){const j=await s.json();if(j.status==='approved'){token=j.token;localStorage.setItem('sarcade_tv_token',token);start();return;}}
setTimeout(poll,3000);};poll();}
// View sent by a phone (« Envoyer ma vue »): centre, zoom and base map, kept
// until the next one; none: automatic framing on the stations.
const OSM='https://tile.openstreetmap.org/{z}/{x}/{y}.png';let base=null,viewRev,defBase=null;
function setBase(url,attr){const u=url||OSM;if(base&&base._url===u)return;if(base)map.removeLayer(base);
base=L.tileLayer(u,{attribution:esc(url?(attr||''):'© OpenStreetMap'),maxZoom:19}).addTo(map);}
// No automatic return (30 Oct 2026): the view stays until « Revenir à la vue
// par défaut » (framing on the active operators, base map of the event).
function applyView(v){const rev=v?v.rev:null;
if(v){$('from').textContent=v.from?'Vue de '+v.from:'';$('from').style.display=v.from?'block':'none';}else $('from').style.display='none';
if(!v)setBase(defBase&&defBase.tile_url,defBase&&defBase.attribution);if(rev===viewRev)return;viewRev=rev;
if(v){map._fitted=true;map.setView([v.lat,v.lon],v.zoom);setBase(v.tile_url,v.attribution);}else map._fitted=false;}
// Weather of the current hour (computed by the server at most every 10 min).
const C1='<path d="M7 18h10a4 4 0 0 0 .5-8A6 6 0 0 0 6 9.5 4.3 4.3 0 0 0 7 18z" fill="#b9c6d2"/>';
const C2='<path d="M7 15h10a4 4 0 0 0 .5-8A6 6 0 0 0 6 6.5 4.3 4.3 0 0 0 7 15z" fill="#b9c6d2"/>';
const SUN='<circle cx="12" cy="12" r="5" fill="#ffb020"/><path d="M12 2v3M12 19v3M2 12h3M19 12h3M4.9 4.9l2.1 2.1M17 17l2.1 2.1M4.9 19.1L7 17M17 7l2.1-2.1" stroke="#ffb020" stroke-width="2" stroke-linecap="round"/>';
const MOON='<path d="M15 3a9 9 0 1 0 6 15A7 7 0 0 1 15 3z" fill="#dfe7f1"/>';
const WX={sun:SUN,moon:MOON,cloud:C1,fog:'<path d="M3 8h18M5 12h14M3 16h18M6 20h12" stroke="#b9c6d2" stroke-width="2" stroke-linecap="round"/>',
sun_cloud:'<circle cx="8" cy="8" r="4.5" fill="#ffb020"/>'+C1,moon_cloud:'<path d="M9 2a6 6 0 1 0 5 9A5 5 0 0 1 9 2z" fill="#dfe7f1"/>'+C1,
rain:C2+'<path d="M8 19l-1 3M12 19l-1 3M16 19l-1 3" stroke="#3dd6f5" stroke-width="2" stroke-linecap="round"/>',
storm:C2+'<path d="M12 14l-3 5h3l-1 4 4-6h-3l1-3z" fill="#ffb020"/>',
snow:C2+'<circle cx="8" cy="20" r="1.4" fill="#fff"/><circle cx="12" cy="22" r="1.4" fill="#fff"/><circle cx="16" cy="20" r="1.4" fill="#fff"/>'};
let wxAt=0;
function weather(w){if(wxAt&&Date.now()-wxAt<600000)return;wxAt=Date.now();
// Temperature only, then sunrise and sunset in place of the weather icon (29 Oct 2026).
$('wx').innerHTML=w&&w.temp_c!=null?'<span>'+Math.round(Number(w.temp_c))+'°</span>'
+(w.vigilance==='orange'||w.vigilance==='red'?'<span class="vig '+w.vigilance+'"></span>':''):'';
$('sun').innerHTML=w&&w.sunrise&&w.sunset?'<b>↑</b> '+hm(w.sunrise)+'<br><b>↓</b> '+hm(w.sunset):'';}
// Layers of the view: drawings, bearings, reference sites; sent again by
// the server only when they changed (revisions fr / rr).
let fr='',rr='',lastOp='',drawL,brgL,refL,dData=[],bData=[],rData=[],zData=[];
function argb(c){c=Number(c)>>>0;return{col:'#'+('00000'+(c&0xFFFFFF).toString(16)).slice(-6),a:((c>>>24)&255)/255||1};}
function arrowHead(pts){const a=pts[pts.length-2],b=pts[pts.length-1],k=Math.cos(b[0]*Math.PI/180)||1;
const dx=(b[1]-a[1])*k,dy=b[0]-a[0],h=Math.hypot(dx,dy)*0.2,t=Math.atan2(dy,dx);
const p=s=>[b[0]+h*Math.sin(t+s),b[1]+h*Math.cos(t+s)/k];return[p(2.6),b,p(-2.6)];}
function drawFeatures(op){drawL.clearLayers();for(const f of dData){const c=argb(f.color),o=nz(f.opacity,1)*op*c.a,pts=f.points||[];
if(!pts.length)continue;const st={color:c.col,weight:f.stroke_width||2,opacity:o,fillOpacity:o*0.25};let l;
if(f.kind==='point')l=L.circleMarker(pts[0],Object.assign({},st,{radius:6,fillOpacity:o}));
else if(f.kind==='text')l=L.marker(pts[0],{interactive:false,icon:L.divIcon({className:'txt',iconSize:null,html:'<span style="color:'+c.col+';opacity:'+o+'">'+esc(f.label)+'</span>'})});
else if(f.kind==='circle')l=L.circle(pts[0],Object.assign({},st,{radius:Number(f.radius_m)||1}));
else if(f.kind==='rectangle'&&pts.length>1)l=L.rectangle([pts[0],pts[1]],st);
else if(f.kind==='zone')l=L.polygon(pts,st);
else if(f.kind==='arrow'&&pts.length>1)l=L.featureGroup([L.polyline(pts,st),L.polyline(arrowHead(pts),st)]);
else l=L.polyline(pts,st);
if(f.label&&f.kind!=='text')l.bindTooltip(esc(f.label),{permanent:true,direction:'center'});l.addTo(drawL);}}
function dest(lat,lon,az,m){const d=m/6371000,t=az*Math.PI/180,p1=lat*Math.PI/180,l1=lon*Math.PI/180;
const p2=Math.asin(Math.sin(p1)*Math.cos(d)+Math.cos(p1)*Math.sin(d)*Math.cos(t));
return[p2*180/Math.PI,(l1+Math.atan2(Math.sin(t)*Math.sin(d)*Math.cos(p1),Math.cos(d)-Math.sin(p1)*Math.sin(p2)))*180/Math.PI];}
// Colours computed by the server, the same as the application (406 violet,
// 121,5 red, exercise 121,375 blue); no label at the end of a bearing
// (30 Oct 2026): the legend bottom left names the signals.
const col6=c=>/^#[0-9a-f]{6}$/i.test(c||'')?c:'#3DD6F5';
function drawBearings(op){brgL.clearLayers();
// Bearings set aside from a probable zone (automatically or by the PCO):
// grey dashed line without cone, as in the application (1 Nov 2026).
const out={};for(const z of zData){for(const id of z.excluded||[])out[id]=true;}
for(const b of bData){const col=col6(b.color),len=Number(b.length_m)||10000,cone=Number(nz(b.cone_deg,5));
const end=dest(b.lat,b.lon,b.azimuth_deg,len);
if(out[b.id]||b.zone==='out'){const g={color:'#9aa3ad',weight:2.5,opacity:op,dashArray:'10 8'};
L.circleMarker([b.lat,b.lon],Object.assign({},g,{radius:4,fillOpacity:op,dashArray:null})).addTo(brgL);L.polyline([[b.lat,b.lon],end],g).addTo(brgL);continue;}
const st={color:col,weight:3,opacity:op};
if(cone>0)L.polygon([[b.lat,b.lon],dest(b.lat,b.lon,(b.azimuth_deg-cone+360)%360,len),dest(b.lat,b.lon,(b.azimuth_deg+cone)%360,len)],
{color:col,weight:1,opacity:.35*op,fillColor:col,fillOpacity:.10*op,interactive:false}).addTo(brgL);
L.circleMarker([b.lat,b.lon],Object.assign({},st,{radius:4,fillOpacity:op})).addTo(brgL);L.polyline([[b.lat,b.lon],end],st).addTo(brgL);}
// Probable zone and crossings of each search, computed by the server.
for(const z of zData){const col=col6(z.color);for(const c of z.crossings||[])L.circleMarker(c,{radius:4,color:col,weight:1,opacity:op,fillOpacity:op}).addTo(brgL);
if(z.lat==null)continue;L.circle([z.lat,z.lon],{radius:Number(z.radius_m)||50,color:'#ff4d5e',weight:2,opacity:op,fillColor:'#ff4d5e',fillOpacity:.13*op,interactive:false}).addTo(brgL);
L.marker([z.lat,z.lon],{interactive:false,icon:L.divIcon({className:'zlab',iconSize:null,iconAnchor:[-10,12],
html:'<span style="border-color:'+col+';opacity:'+op+'">'+(z.exercise?'<span class="ex">EXERCICE</span> ':'')+esc(z.label)+'</span>'})}).addTo(brgL);}
legend();}
// Legend bottom left, always visible while bearings are shown.
function legend(){const by={};for(const b of bData){const k=b.search||'';by[k]=by[k]||{color:col6(b.color),ex:false};by[k].ex=by[k].ex||b.exercise;}
const keys=Object.keys(by).sort();const zone=zData.some(z=>z.lat!=null);
$('legend').innerHTML=keys.map(k=>'<div><i style="background:'+by[k].color+'"></i>'+esc(k||'Sans signal')+(by[k].ex?' <span class="ex">EXERCICE</span>':'')+'</div>').join('')
+(zone?'<div><i class="zr" style="border-color:#ff4d5e"></i>Zone probable</div>':'');$('legend').style.display=keys.length?'block':'none';}
function drawRefs(o){refL.clearLayers();for(const r of rData){const hp=r.category==='HIGH_POINT',c=hp?'#7ddc5a':'#c77dff',op=nz(hp?o.high:o.relay,o.ref,1);
L.circleMarker([r.lat,r.lon],{radius:5,color:c,weight:2,opacity:op,fillOpacity:op*0.6})
.bindTooltip(esc(r.callsign||r.name),{permanent:true,direction:'right',className:'rlab'}).addTo(refL);}}
// Day / night chosen by the server (mode pushed with the view, else sunrise
// and sunset); in night the plan tiles are darkened as in the application
// (not the photos). Messages hidden: the column leaves, the map takes the
// whole width and keeps its centre and zoom.
let lastLook='';
function look(s){const night=s.night!==false,msg=!(s.options&&s.options.messages===false);
const b=String((s.view&&s.view.basemap)||(s.default_base&&s.default_base.id)||'');const dark=night&&!/photo|sat/i.test(b);
const k=night+'|'+dark+'|'+msg;if(k===lastLook)return;lastLook=k;
$('app').classList.toggle('day',!night);$('app').classList.toggle('nomsg',!msg);map.getContainer().classList.toggle('night',dark);
map.invalidateSize({animate:false});}
// « Identifier »: the name of this screen in very large letters, 5 s.
let identId='';
function identify(i){if(!i||i.id===identId)return;identId=i.id;$('ident').textContent=i.name;$('ident').style.display='flex';
setTimeout(()=>{$('ident').style.display='none';},(Number(i.seconds)||5)*1000);}
// Traces as on aprs.fi and in the application (1 Nov 2026): thick line,
// a dot per position received (zoomed in), operators cyan, APRS amber.
let trL,tData=[],trOp=1;
function drawTraces(){if(!trL)return;trL.clearLayers();const dots=map&&map.getZoom()>=13;
for(const t of tData){const col=t.aprs?'#ffb020':'#3dd6f5';
L.polyline(t.points,{color:col,weight:5,opacity:.8*trOp,lineCap:'round',lineJoin:'round',interactive:false}).addTo(trL);
if(dots)for(const p of t.points)L.circleMarker(p,{radius:3,color:'#ffffff',weight:1,fillColor:'#b71c1c',fillOpacity:trOp,opacity:trOp,interactive:false}).addTo(trL);}}
function layers(s){const o=(s.options&&s.options.opacity)||{},key=JSON.stringify(o);
const tk=JSON.stringify(s.traces||[]);if(tk!==layers.tk||nz(o.traces,1)!==trOp){layers.tk=tk;tData=s.traces||[];trOp=nz(o.traces,1);drawTraces();}
if(s.drawings)dData=s.drawings;if(s.bearings){bData=s.bearings;zData=s.zones||[];}if(s.references)rData=s.references;
if(s.drawings||s.bearings||key!==lastOp){drawFeatures(nz(o.draw,1));drawBearings(nz(o.brg,1));}
if(s.references||key!==lastOp)drawRefs(o);lastOp=key;fr=s.features_rev||'';rr=s.references_rev||'';
// One opacity per layer: operators and APRS apart.
for(const m of Object.values(markers)){const op=nz(m._aprs?o.aprs:o.ops,o.ops,1);m.setStyle({opacity:op,fillOpacity:.9*op});m.bringToFront();}
look(s);}
// Top bar: clock, and the state of the link with the server.
let lastOk=0;
function tick(){const d=new Date();$('clock').textContent=d.toLocaleTimeString('fr-FR',{hour:'2-digit',minute:'2-digit'});}
function linkState(ok){if(ok)lastOk=Date.now();const lost=!ok&&Date.now()-lastOk>30000;$('link').className=lost?'lost':'';
$('linkTxt').textContent=lost?'Liaison perdue'+(lastOk?' depuis '+hm(lastOk):''):(lastOk?'Connecté':'Connexion…');}
async function refresh(){let r;try{r=await fetch('/api/v0.1/tv/state?fr='+encodeURIComponent(fr)+'&rr='+encodeURIComponent(rr),{headers:{authorization:'Display '+token}});}catch(e){linkState(false);return;}
if(r.status===401){localStorage.removeItem('sarcade_tv_token');location.reload();return;}if(!r.ok){linkState(false);return;}const s=await r.json();linkState(true);
$('title').textContent=s.event.name;const pts=[];const now=Date.now();defBase=s.default_base||null;
const active=s.positions.filter(p=>p.source!=='aprs'&&now-new Date(p.time)<10*60000).map(p=>[p.lat,p.lon]);
$('nOps').textContent=s.positions.filter(p=>p.source!=='aprs'&&now-new Date(p.time)<10*60000).length;
$('nAprs').textContent=s.positions.filter(p=>p.source==='aprs').length;
for(const p of s.positions){const ll=[p.lat,p.lon];pts.push(ll);const k=p.device_id;const aprs=p.source==='aprs';
if(!markers[k]){markers[k]=L.circleMarker(ll,{radius:aprs?6:8,color:aprs?'#ffb020':'#3dd6f5',fillOpacity:.9}).bindTooltip(esc(label(p)),{permanent:true,direction:'right'}).addTo(map);markers[k]._aprs=aprs;}else markers[k].setLatLng(ll);}
// Stations no longer shown (hidden layer) leave the map.
const keep=new Set(s.positions.map(p=>p.device_id));for(const k of Object.keys(markers))if(!keep.has(k)){map.removeLayer(markers[k]);delete markers[k];}
layers(s);weather(s.weather);identify(s.identify);
declutter();
applyView(s.view);
const fit=active.length?active:pts;if(fit.length&&!map._fitted){map.fitBounds(fit,{padding:[40,40],maxZoom:15});map._fitted=true;}
$('msgs').innerHTML=s.messages.slice(-14).reverse().map(m=>'<div class="m '+(m.priority==='immediate'?'i':m.priority==='urgent'?'u':'')+'">'+hm(m.created_at)+' · <b>'+esc(m.sender_id)+'</b> : '+esc(m.body)+'</div>').join('');
// Red banner chosen by the server (cleared from a phone, or closed by « Fin
// de détresse »); the SOS pill stays while a distress is not closed.
const a=s.alert;$('alert').style.display=a?'block':'none';if(a)$('alert').textContent=a.sender_id+' : '+a.body;
const sos=s.sos||[];$('sos').style.display=sos.length?'inline-block':'none';$('sos').title='Détresse en cours : '+sos.join(', ');}
function start(){$('pair').style.display='none';$('app').style.display='grid';map=L.map('map',{zoomControl:false}).setView([46.6,2.4],6);
map.on('zoomend moveend',declutter);trL=L.layerGroup().addTo(map);drawL=L.layerGroup().addTo(map);brgL=L.layerGroup().addTo(map);refL=L.layerGroup().addTo(map);
map.on('zoomend',function(){drawTraces();});
map.on('zoomend',()=>map.getContainer().classList.toggle('lowzoom',map.getZoom()<12));map.getContainer().classList.add('lowzoom');tick();setInterval(tick,1000);refresh();setInterval(refresh,10000);}
if(token)start();else pair();
</script></body></html>"""
