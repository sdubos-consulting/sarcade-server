"""Navigation (level 2): itineraries computed by Valhalla on the server,
legs of routes computed along paths, roads closed by the PCO avoided."""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime
import logging
import os
import uuid

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy import select

from sarcade.db.models import EventRow, RouteObjectRow
from sarcade.db.session import SessionLocal
from sarcade.realtime.manager import manager
from sarcade.routing import graph as road_graph
from sarcade.routing import valhalla
from sarcade.sync.service import record_operation

from .routes import object_dict

log = logging.getLogger("sarcade.routing")
router = APIRouter(prefix="/api/v0.1")
SERVER_ACTOR = "SARCADE"
PROFILE_MODES = {"foot": "foot", "vehicle": "car"}


class RoutingIn(BaseModel):
    event_id: str | None = Field(default=None, max_length=64)
    points: list[list[float]] = Field(min_length=2, max_length=25)
    mode: str = Field(pattern="^(car|foot|offroad)$")
    # Up to two variants, shown in grey on the map (decision of 10 Oct 2026).
    alternatives: int = Field(default=0, ge=0, le=2)


def closures(db, event_id: str | None) -> list[list[list[float]]]:
    if not event_id:
        return []
    rows = db.scalars(select(RouteObjectRow).where(RouteObjectRow.event_id == event_id,
                                                   RouteObjectRow.kind == "road_closure",
                                                   RouteObjectRow.deleted_at.is_(None))).all()
    return [r.data["points"] for r in rows if r.data.get("active", True)]


@router.post("/routing")
async def compute_itinerary(payload: RoutingIn):
    """Itinerary between points (current position first), avoiding the roads
    closed by the PCO of the event."""
    for p in payload.points:
        if len(p) != 2 or not (-90 <= p[0] <= 90 and -180 <= p[1] <= 180):
            raise HTTPException(status_code=422, detail="invalid_point")
    db = SessionLocal()
    try:
        closed = closures(db, payload.event_id)
    finally:
        db.close()
    try:
        result = await valhalla.route(payload.points, payload.mode, closed, alternatives=payload.alternatives)
    except valhalla.NoRoute:
        raise HTTPException(status_code=422, detail="no_route")
    except valhalla.RoutingUnavailable:
        raise HTTPException(status_code=503, detail="routing_unavailable")
    result["mode"] = payload.mode
    result["avoided_closures"] = len(closed)
    for alt in result.get("alternatives", []):
        alt["mode"] = payload.mode
    return result


@router.get("/routing/status")
async def routing_status():
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            r = await client.get(f"{valhalla.valhalla_url()}/status")
        return {"available": r.status_code == 200, "url": valhalla.valhalla_url()}
    except httpx.HTTPError as exc:
        return {"available": False, "url": valhalla.valhalla_url(), "detail": str(exc) or type(exc).__name__}


def _pending_legs(db, event_id: str, route_id: str | None = None):
    stmt = select(RouteObjectRow).where(RouteObjectRow.event_id == event_id, RouteObjectRow.kind == "route",
                                        RouteObjectRow.deleted_at.is_(None))
    if route_id:
        stmt = stmt.where(RouteObjectRow.id == route_id)
    work = []
    for route in db.scalars(stmt):
        waypoints = {w.id: w for w in db.scalars(select(RouteObjectRow).where(
            RouteObjectRow.route_id == route.id, RouteObjectRow.kind == "route_waypoint",
            RouteObjectRow.deleted_at.is_(None)))}
        order = [i for i in route.data.get("point_order", []) if i in waypoints]
        for prev_id, wp_id in zip(order, order[1:]):
            wp = waypoints[wp_id]
            if wp.data.get("leg_mode") == "paths" and (wp.data.get("leg_needs_routing") or not wp.data.get("leg_geometry")):
                prev = waypoints[prev_id]
                work.append((route, wp.id, [[prev.data["lat"], prev.data["lon"]], [wp.data["lat"], wp.data["lon"]]]))
    return work


async def compute_route_legs(event_id: str, route_id: str | None = None) -> dict:
    """Computes the legs « along paths » still to compute (after a creation
    offline, for instance). Each update goes to the change feed."""
    db = SessionLocal()
    try:
        work = _pending_legs(db, event_id, route_id)
        closed = closures(db, event_id)
    finally:
        db.close()
    computed = failed = 0
    for route, wp_id, points in work:
        mode = PROFILE_MODES.get(route.data.get("profile", "foot"), "foot")
        try:
            result = await valhalla.route(points, mode, closed)
            geometry, ok = result["geometry"], True
        except valhalla.NoRoute:
            geometry, ok = None, False
        now = datetime.now(UTC)
        db = SessionLocal()
        try:
            wp = db.get(RouteObjectRow, wp_id)
            if wp is None or wp.deleted_at is not None:
                continue
            data = dict(wp.data)
            data["leg_needs_routing"] = False
            data["leg_geometry"] = geometry
            data["leg_routing_failed"] = not ok
            wp.data, wp.updated_at, wp.updated_by = data, now, SERVER_ACTOR
            wp.revision = (wp.revision or 0) + 1
            db.flush()
            result_dict = object_dict(wp)
            _, cursor = record_operation(db, event_id=event_id, operation_id=str(uuid.uuid4()), object_id=wp.id,
                                         object_type="route_waypoint", action="update", payload=result_dict,
                                         client_time=now)
            db.commit()
        finally:
            db.close()
        computed += ok
        failed += not ok
        await manager.broadcast(event_id, {"type": "route_waypoint.upserted", "data": result_dict})
    return {"computed": computed, "no_route": failed}


@router.post("/events/{event_id}/routes/{route_id}/legs")
async def compute_legs(event_id: str, route_id: str):
    try:
        return await compute_route_legs(event_id, route_id)
    except valhalla.RoutingUnavailable:
        raise HTTPException(status_code=503, detail="routing_unavailable")


async def legs_loop() -> None:
    """Computes pending legs of active events every two minutes."""
    while True:
        await asyncio.sleep(120)
        db = SessionLocal()
        try:
            ids = [e.id for e in db.scalars(select(EventRow).where(EventRow.ended_at.is_(None)))]
        finally:
            db.close()
        for event_id in ids:
            try:
                await compute_route_legs(event_id)
            except valhalla.RoutingUnavailable:
                break
            except Exception as exc:  # noqa: BLE001
                log.warning("legs not computed for %s: %s", event_id, exc)


# --- Road graph for navigation on the device (level 3) ----------------------

def osm_path() -> str:
    return os.getenv("SARCADE_OSM_PBF", "/var/lib/sarcade/osm/sarcade-area.osm.pbf")


def graph_path() -> str:
    return os.getenv("SARCADE_ROUTING_GRAPH", "/var/lib/sarcade/routing/sarcade-graph.srg.gz")


def graph_info() -> dict | None:
    meta = graph_path() + ".json"
    if not (os.path.isfile(graph_path()) and os.path.isfile(meta)):
        return None
    import json
    with open(meta) as f:
        return json.load(f)


def graph_outdated() -> bool:
    src, out = osm_path(), graph_path()
    if not os.path.isfile(src):
        return False
    return not os.path.isfile(out) or os.path.getmtime(src) > os.path.getmtime(out)


def rebuild_graph() -> dict | None:
    if not graph_outdated():
        return graph_info()
    os.makedirs(os.path.dirname(graph_path()) or ".", exist_ok=True)
    info = road_graph.build_package(osm_path(), graph_path())
    log.info("road graph built: %s vertices, %s edges, %s bytes", info["vertices"], info["edges"], info["size"])
    return info


graph_state: dict = {"last_error": None, "source_found": None}


async def graph_loop() -> None:
    """Builds the graph when the OSM extract is newer (weekly refresh)."""
    while True:
        graph_state["source_found"] = os.path.isfile(osm_path())
        try:
            await asyncio.to_thread(rebuild_graph)
            graph_state["last_error"] = None
        except Exception as exc:  # noqa: BLE001
            graph_state["last_error"] = f"{type(exc).__name__}: {exc}"[:300]
            log.warning("road graph not built: %s", exc)
        await asyncio.sleep(3600)


@router.get("/routing/graph/info")
def road_graph_info():
    """Version of the road graph the apps download (Wi-Fi or local network)."""
    info = graph_info()
    return {"available": info is not None, **(info or {}), "build": dict(graph_state)}


@router.get("/routing/graph")
def road_graph_download(request: Request):
    info = graph_info()
    if info is None:
        raise HTTPException(status_code=404, detail="no_road_graph")
    etag = f'"{info["sha256"]}"'
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers={"ETag": etag})
    return FileResponse(graph_path(), media_type="application/octet-stream", filename="sarcade-graph.srg.gz",
                        headers={"ETag": etag})


def start(tasks: list) -> None:
    if os.getenv("SARCADE_VALHALLA_URL"):
        tasks.append(asyncio.create_task(legs_loop()))
    if os.getenv("SARCADE_ROUTING_GRAPH_BUILD", "1") != "0":
        tasks.append(asyncio.create_task(graph_loop()))
