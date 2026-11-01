"""High points, relays and other reference sites of the ADRASEC module
(imported from uMap). Moved out of ``sarcade.api.app`` with lot C (card
« Séparer le Core et les modules », 1 Nov 2026).

An event without the ADRASEC module gets an empty list when it asks with
``event_id`` (step S3); the import stays a server-wide admin action.
"""
from __future__ import annotations

from datetime import UTC, datetime
import json
import os

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from geoalchemy2.elements import WKTElement
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from sarcade import modules
from sarcade.api.deps import get_db
from sarcade.api.schemas import ReferenceSiteOut
from sarcade.api.serializers import reference_site_dict
from sarcade.db.models import EventRow, ReferenceSiteRow

from .reference.umap import REFERENCE_LAYERS, parse_umap_reference_sites

router = APIRouter()

REFERENCE_IMPORT_MAX_BYTES = int(os.getenv("SARCADE_REFERENCE_IMPORT_MAX_BYTES", str(10 * 1024 * 1024)))


def _set_reference_site(row: ReferenceSiteRow, site: dict):
    row.category = site["category"]
    row.subtype = site.get("subtype")
    row.name = site["name"]
    row.callsign = site.get("callsign")
    row.point = WKTElement(f'POINT({site["lon"]} {site["lat"]})', srid=4326)
    row.alt_m = site.get("alt_m")
    row.access = site.get("access")
    row.clearance = site.get("clearance")
    row.mode = site.get("mode")
    row.rx_mhz = site.get("rx_mhz")
    row.tx_mhz = site.get("tx_mhz")
    row.ctcss_rx = site.get("ctcss_rx")
    row.ctcss_tx = site.get("ctcss_tx")
    row.offset = site.get("offset")
    row.description = site.get("description")
    row.verified_at = site.get("verified_at")
    row.source = site["source"]
    row.source_layer = site["source_layer"]
    row.source_object_id = site["source_object_id"]
    row.source_hash = site["source_hash"]
    row.source_properties = site["source_properties"]
    row.status = "active"
    row.imported_at = site["imported_at"]


@router.get("/api/v0.1/reference-sites", response_model=list[ReferenceSiteOut])
def list_reference_sites(
    category: str | None = Query(default=None),
    q: str | None = Query(default=None, max_length=120),
    status: str = Query(default="active"),
    limit: int = Query(default=1000, ge=1, le=5000),
    event_id: str | None = Query(default=None, max_length=80),
    db: Session = Depends(get_db),
):
    if event_id is not None:
        event = db.get(EventRow, event_id)
        if event is None or not modules.enabled(event.settings, "adrasec"):
            return []
    stmt = select(ReferenceSiteRow).where(ReferenceSiteRow.status == status)
    if category:
        stmt = stmt.where(ReferenceSiteRow.category == category.upper())
    if q and q.strip():
        text = q.strip()
        term = f"%{text}%"
        criteria = [
            ReferenceSiteRow.name.ilike(term),
            ReferenceSiteRow.callsign.ilike(term),
            ReferenceSiteRow.mode.ilike(term),
            ReferenceSiteRow.subtype.ilike(term),
            ReferenceSiteRow.description.ilike(term),
        ]
        try:
            frequency = float(text.replace(",", "."))
            criteria.extend([
                func.abs(ReferenceSiteRow.rx_mhz - frequency) < 0.001,
                func.abs(ReferenceSiteRow.tx_mhz - frequency) < 0.001,
            ])
        except ValueError:
            pass
        stmt = stmt.where(or_(*criteria))
    rows = db.scalars(
        stmt.order_by(ReferenceSiteRow.category, ReferenceSiteRow.name).limit(limit)
    ).all()
    return [reference_site_dict(row) for row in rows]


@router.post("/api/v0.1/reference-sites/import/umap")
async def import_umap_reference_sites(
    file: UploadFile = File(...),
    source: str = Form("umap:adrasec78"),
    apply: bool = Form(False),
    db: Session = Depends(get_db),
):
    raw = await file.read(REFERENCE_IMPORT_MAX_BYTES + 1)
    if len(raw) > REFERENCE_IMPORT_MAX_BYTES:
        raise HTTPException(status_code=413, detail="reference_file_too_large")
    try:
        document = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise HTTPException(status_code=400, detail="invalid_umap_json")

    sites = parse_umap_reference_sites(document, source=source)
    if not sites:
        raise HTTPException(status_code=422, detail="no_supported_reference_sites")

    layers = tuple(REFERENCE_LAYERS.keys())
    existing = db.scalars(select(ReferenceSiteRow).where(
        ReferenceSiteRow.source == source,
        ReferenceSiteRow.source_layer.in_(layers),
    )).all()
    by_key = {(row.source_layer, row.source_object_id): row for row in existing}
    desired_keys = {(site["source_layer"], site["source_object_id"]) for site in sites}

    changes = []
    created = modified = archived = unchanged = 0
    for site in sites:
        key = (site["source_layer"], site["source_object_id"])
        row = by_key.get(key)
        if row is None:
            created += 1
            changes.append({"action": "create", "category": site["category"], "name": site["name"]})
            if apply:
                row = ReferenceSiteRow(id=site["id"])
                _set_reference_site(row, site)
                db.add(row)
        elif row.source_hash != site["source_hash"] or row.status != "active":
            modified += 1
            changes.append({"action": "update", "category": site["category"], "name": site["name"]})
            if apply:
                _set_reference_site(row, site)
        else:
            unchanged += 1

    for row in existing:
        key = (row.source_layer, row.source_object_id)
        if key not in desired_keys and row.status != "archived":
            archived += 1
            changes.append({"action": "archive", "category": row.category, "name": row.name})
            if apply:
                row.status = "archived"
                row.imported_at = datetime.now(UTC)

    if apply:
        db.commit()

    return {
        "applied": apply,
        "source": source,
        "source_file": file.filename,
        "parsed": len(sites),
        "summary": {
            "created": created,
            "modified": modified,
            "archived": archived,
            "unchanged": unchanged,
        },
        "changes": changes[:500],
        "changes_truncated": len(changes) > 500,
    }
