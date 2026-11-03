from datetime import datetime
from pydantic import BaseModel, Field


class EventCreate(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    kind: str
    summary: str | None = Field(default=None, max_length=1000)
    # Modules of the event (sarcade.modules); None: those of its kind.
    modules: list[str] | None = None


class EventOut(EventCreate):
    id: str
    status: str
    created_at: datetime
    version: int


class TeamCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)


class TeamOut(TeamCreate):
    id: str
    event_id: str
    status: str
    version: int


class PositionCreate(BaseModel):
    id: str
    event_id: str
    device_id: str
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    alt_m: float | None = None
    accuracy_m: float | None = Field(default=None, ge=0)
    heading_deg: float | None = Field(default=None, ge=0, lt=360)
    speed_mps: float | None = Field(default=None, ge=0)
    time: datetime
    battery_pct: int | None = Field(default=None, ge=0, le=100)


class PositionOut(PositionCreate):
    source: str = "device"
    aprs_via: str | None = None
    aprs_symbol: str | None = None
    aprs_path: str | None = None
    callsign: str | None = None
    # Status of the operator (lot 24), latest positions only.
    operator_status: str | None = None
    operator_status_at: datetime | None = None


class POICreate(BaseModel):
    id: str
    kind: str = Field(min_length=1, max_length=64)
    label: str | None = Field(default=None, max_length=160)
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    created_by: str | None = Field(default=None, max_length=64)
    color: int | None = Field(default=None, ge=0, le=0xFFFFFFFF)
    icon: str | None = Field(default=None, max_length=32)


class POIOut(POICreate):
    event_id: str
    created_at: datetime
    version: int
    updated_by: str | None = None
    updated_at: datetime | None = None


class SyncOperationIn(BaseModel):
    operation_id: str
    event_id: str
    object_id: str
    object_type: str
    action: str
    client_time: datetime
    payload: dict


class SyncResultOut(BaseModel):
    operation_id: str
    status: str
    server_time: datetime
    sync_cursor: str | None = None
    # Readable reason when the operation is not accepted.
    reason: str | None = None


class ReferenceSiteOut(BaseModel):
    id: str
    category: str
    subtype: str | None = None
    name: str
    callsign: str | None = None
    lat: float
    lon: float
    alt_m: float | None = None
    access: str | None = None
    clearance: str | None = None
    mode: str | None = None
    rx_mhz: float | None = None
    tx_mhz: float | None = None
    ctcss_rx: str | None = None
    ctcss_tx: str | None = None
    offset: str | None = None
    description: str | None = None
    verified_at: str | None = None
    source: str
    source_layer: str
    status: str
