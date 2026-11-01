from datetime import datetime
import uuid

from geoalchemy2 import Geography
from sqlalchemy import false
from sqlalchemy import JSON, BigInteger, Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


def new_id() -> str:
    return str(uuid.uuid4())


class OrganizationRow(Base):
    __tablename__ = "organizations"
    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="active", nullable=False)


class EventRow(Base):
    __tablename__ = "events"
    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=new_id)
    organization_id: Mapped[str | None] = mapped_column(ForeignKey("organizations.id"))
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="draft", nullable=False)
    summary: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    version: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # Operational settings set by the PCO (sarcade.events.settings).
    settings: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class TeamRow(Base):
    __tablename__ = "teams"
    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=new_id)
    event_id: Mapped[str] = mapped_column(ForeignKey("events.id"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="available", nullable=False)
    version: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


class PositionRow(Base):
    __tablename__ = "positions"
    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=new_id)
    event_id: Mapped[str] = mapped_column(ForeignKey("events.id"), nullable=False, index=True)
    device_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    point = mapped_column(Geography(geometry_type="POINT", srid=4326), nullable=False)
    alt_m: Mapped[float | None] = mapped_column(Float)
    accuracy_m: Mapped[float | None] = mapped_column(Float)
    heading_deg: Mapped[float | None] = mapped_column(Float)
    speed_mps: Mapped[float | None] = mapped_column(Float)
    time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    battery_pct: Mapped[int | None] = mapped_column(Integer)
    # "device" (phone or PC) or "aprs".
    source: Mapped[str] = mapped_column(String(16), default="device", nullable=False)
    # APRS only: "rf" (radio, Gateway) or "is" (APRS-IS, Internet).
    aprs_via: Mapped[str | None] = mapped_column(String(8))
    # APRS only: symbol (table + code, "/>" = car) and path of the packet
    # ("F4JPO-10*,WIDE1-1,qAR,F4JPO-10"), shown in the station sheet.
    aprs_symbol: Mapped[str | None] = mapped_column(String(2))
    aprs_path: Mapped[str | None] = mapped_column(String(160))
    # APRS only: callsign heard (F4JPO-5), shown even when linked to a terminal.
    aprs_callsign: Mapped[str | None] = mapped_column(String(12))


class POIRow(Base):
    __tablename__ = "pois"
    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=new_id)
    event_id: Mapped[str] = mapped_column(ForeignKey("events.id"), nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String(64), nullable=False)
    label: Mapped[str | None] = mapped_column(String(160))
    point = mapped_column(Geography(geometry_type="POINT", srid=4326), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    version: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # Author, his drawing colour (ARGB) and the last change (29 Oct 2026).
    created_by: Mapped[str | None] = mapped_column(String(64))
    color: Mapped[int | None] = mapped_column(BigInteger)
    updated_by: Mapped[str | None] = mapped_column(String(64))
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Shape of the icon (30 Oct 2026), None = default pin.
    icon: Mapped[str | None] = mapped_column(String(32))



class SyncOperationRow(Base):
    __tablename__ = "sync_operations"
    seq: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    operation_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    event_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    object_id: Mapped[str] = mapped_column(String(64), nullable=False)
    object_type: Mapped[str] = mapped_column(String(32), nullable=False)
    action: Mapped[str] = mapped_column(String(16), nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    client_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    server_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)


class MessageRow(Base):
    __tablename__ = "messages"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    event_id: Mapped[str] = mapped_column(ForeignKey("events.id"), nullable=False, index=True)
    sender_id: Mapped[str] = mapped_column(String(64), nullable=False)
    recipient_ids: Mapped[list] = mapped_column(JSON, nullable=False)
    priority: Mapped[str] = mapped_column(String(16), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    attachments: Mapped[list | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # Deleted for everyone: the original body stays here for the PCO only.
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deleted_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    deleted_by_pco: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=false())
    # Reply (31 Oct 2026): id of the quoted message, same event.
    reply_to: Mapped[str | None] = mapped_column(String(64), nullable=True)

class AckRow(Base):
    __tablename__ = "acks"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    event_id: Mapped[str] = mapped_column(ForeignKey("events.id"), nullable=False)
    message_id: Mapped[str] = mapped_column(ForeignKey("messages.id"), nullable=False, index=True)
    actor_id: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

class LogbookRow(Base):
    __tablename__ = "logbook"
    seq: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    event_id: Mapped[str] = mapped_column(ForeignKey("events.id"), nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    object_id: Mapped[str | None] = mapped_column(String(64))
    actor_id: Mapped[str | None] = mapped_column(String(64))
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class SharedFileRow(Base):
    __tablename__ = "shared_files"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    event_id: Mapped[str] = mapped_column(ForeignKey("events.id"), nullable=False, index=True)
    sender_id: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(160), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    storage_path: Mapped[str] = mapped_column(Text, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # Folder path, "" for the root, up to 3 segments joined by "/"
    # (sarcade.files.folders).
    folder: Mapped[str] = mapped_column(String(200), default="", nullable=False)


class PersonalFileRow(Base):
    """« Personnel » folder (decision of 28 Oct 2026): files of one person,
    kept by the server, visible to that person only, tied to the email
    checked by Cloudflare Access, found again on every device and event."""
    __tablename__ = "personal_files"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    owner_email: Mapped[str] = mapped_column(String(320), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(160), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    storage_path: Mapped[str] = mapped_column(Text, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    folder: Mapped[str] = mapped_column(String(200), default="", nullable=False)


class FileFolderRow(Base):
    """Folder of the Files screen, kept even when empty. ``scope`` « event »
    (``owner`` = event id) or « personal » (``owner`` = checked email)."""
    __tablename__ = "file_folders"
    __table_args__ = (UniqueConstraint("scope", "owner", "path", name="uq_file_folder"),)
    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    scope: Mapped[str] = mapped_column(String(16), nullable=False)
    owner: Mapped[str] = mapped_column(String(320), nullable=False)
    path: Mapped[str] = mapped_column(String(200), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ReferenceSiteRow(Base):
    __tablename__ = "reference_sites"
    __table_args__ = (
        UniqueConstraint(
            "source", "source_layer", "source_object_id",
            name="uq_reference_site_source_object",
        ),
    )
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    category: Mapped[str] = mapped_column(String(24), nullable=False, index=True)
    subtype: Mapped[str | None] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(160), nullable=False, index=True)
    callsign: Mapped[str | None] = mapped_column(String(64))
    point = mapped_column(Geography(geometry_type="POINT", srid=4326), nullable=False)
    alt_m: Mapped[float | None] = mapped_column(Float)
    access: Mapped[str | None] = mapped_column(Text)
    clearance: Mapped[str | None] = mapped_column(Text)
    mode: Mapped[str | None] = mapped_column(String(64))
    rx_mhz: Mapped[float | None] = mapped_column(Float)
    tx_mhz: Mapped[float | None] = mapped_column(Float)
    ctcss_rx: Mapped[str | None] = mapped_column(String(32))
    ctcss_tx: Mapped[str | None] = mapped_column(String(32))
    offset: Mapped[str | None] = mapped_column(String(32))
    description: Mapped[str | None] = mapped_column(Text)
    verified_at: Mapped[str | None] = mapped_column(String(32))
    source: Mapped[str] = mapped_column(String(128), nullable=False)
    source_layer: Mapped[str] = mapped_column(String(128), nullable=False)
    source_object_id: Mapped[str] = mapped_column(String(128), nullable=False)
    source_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    source_properties: Mapped[dict] = mapped_column(JSON, nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="active", index=True)
    imported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class MapFeatureRow(Base):
    """Drawn map object. ``data`` holds the client representation (points in
    lat/lon order, style, label); ``geom`` mirrors it for spatial queries."""
    __tablename__ = "map_features"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    event_id: Mapped[str] = mapped_column(ForeignKey("events.id"), nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String(24), nullable=False)
    geom = mapped_column(Geography(geometry_type="GEOMETRY", srid=4326), nullable=True)
    data: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_by: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_by: Mapped[str] = mapped_column(String(64), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revision: Mapped[int] = mapped_column(Integer, default=1, nullable=False)


class DeviceRow(Base):
    """Last contact of a device in an event: feeds the PCO view of devices
    that no longer report (« vue du dernier contact »)."""
    __tablename__ = "devices"
    event_id: Mapped[str] = mapped_column(ForeignKey("events.id"), primary_key=True)
    device_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    label: Mapped[str | None] = mapped_column(String(120))
    platform: Mapped[str | None] = mapped_column(String(32))
    app_version: Mapped[str | None] = mapped_column(String(32))
    battery_pct: Mapped[int | None] = mapped_column(Integer)
    pending_count: Mapped[int | None] = mapped_column(Integer)
    oldest_pending_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    tracking_enabled: Mapped[bool | None] = mapped_column()
    tracking_interval_s: Mapped[int | None] = mapped_column(Integer)
    # Operator's callsign (APRS positions attach to this device) and consent
    # to have positions transmitted on local radio (note « APRS »).
    callsign: Mapped[str | None] = mapped_column(String(16), index=True)
    aprs_tx_consent: Mapped[bool | None] = mapped_column()
    # Icon of the operator on the map (sarcade.profile.preferences.OPERATOR_ICONS).
    icon: Mapped[str | None] = mapped_column(String(16))
    last_contact_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_position_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class CommGroupRow(Base):
    """Communication group; ``data`` holds the canonical client fields."""
    __tablename__ = "comm_groups"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    event_id: Mapped[str] = mapped_column(ForeignKey("events.id"), nullable=False, index=True)
    data: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_by: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_by: Mapped[str] = mapped_column(String(64), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revision: Mapped[int] = mapped_column(Integer, default=1, nullable=False)


class AprsGroupRow(Base):
    """Group of radio amateur callsigns whose APRS positions are shown
    (e.g. « ADRASEC 78 »). Created by the organisation administrator."""
    __tablename__ = "aprs_groups"
    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    callsigns: Mapped[list] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_by: Mapped[str] = mapped_column(String(64), nullable=False)


class AprsStationClassRow(Base):
    """APRS station classified by hand as fixed (iGate, relay) or mobile in
    an event (« Équipes, stations APRS fixes », D, 30 Oct 2026); without a
    row the application decides from the symbol, SSID and movement."""
    __tablename__ = "aprs_station_classes"
    event_id: Mapped[str] = mapped_column(ForeignKey("events.id"), primary_key=True)
    device_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    kind: Mapped[str] = mapped_column(String(8), nullable=False)
    updated_by: Mapped[str] = mapped_column(String(64), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class WeatherCacheRow(Base):
    """Last forecast or vigilance fetched, served without Internet."""
    __tablename__ = "weather_cache"
    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    data: Mapped[dict] = mapped_column(JSON, nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class RouteObjectRow(Base):
    """Route, waypoint or passage (``kind``), last writer wins per object."""
    __tablename__ = "route_objects"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    event_id: Mapped[str] = mapped_column(ForeignKey("events.id"), nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String(24), nullable=False)
    route_id: Mapped[str | None] = mapped_column(String(64), index=True)
    data: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_by: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_by: Mapped[str] = mapped_column(String(64), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revision: Mapped[int] = mapped_column(Integer, default=1, nullable=False)


class CustomBasemapRow(Base):
    """Base map added by the administrator (Core, decision of 10 Oct 2026)."""
    __tablename__ = "custom_basemaps"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    data: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_by: Mapped[str] = mapped_column(String(64), nullable=False)


class SecurityEventRow(Base):
    """Security journal (note « Mises à jour et sécurité »): access, rights,
    administration, updates, suspicious behaviour. Never the content of
    messages, photos or positions. Each entry seals the previous one."""
    __tablename__ = "security_events"
    seq: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    category: Mapped[str] = mapped_column(String(24), nullable=False, index=True)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    outcome: Mapped[str] = mapped_column(String(16), nullable=False)
    actor: Mapped[str | None] = mapped_column(String(64))
    device_id: Mapped[str | None] = mapped_column(String(64))
    event_id: Mapped[str | None] = mapped_column(String(64))
    source_ip: Mapped[str | None] = mapped_column(String(64))
    details: Mapped[dict] = mapped_column(JSON, nullable=False)
    prev_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    hash: Mapped[str] = mapped_column(String(64), nullable=False)


class ServerSettingRow(Base):
    """Server-wide settings of the administration tool (maintenance windows,
    journal retention, minimal client version) and small server state."""
    __tablename__ = "server_settings"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[dict] = mapped_column(JSON, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ServerUpdateRow(Base):
    """History of server updates reported by the updater."""
    __tablename__ = "server_updates"
    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    from_version: Mapped[str | None] = mapped_column(String(32))
    version: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    detail: Mapped[str | None] = mapped_column(Text)


class ClientDeviceRow(Base):
    """Version of a client application, across events, for the minimal
    version imposed by the server (invitation, then obligation 2 h later,
    deferred to the end of an active event)."""
    __tablename__ = "client_devices"
    device_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    platform: Mapped[str | None] = mapped_column(String(32))
    app_version: Mapped[str | None] = mapped_column(String(32))
    invited_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    invited_for: Mapped[str | None] = mapped_column(String(32))
    deferred_logged: Mapped[bool] = mapped_column(default=False, nullable=False)
    refused_logged: Mapped[bool] = mapped_column(default=False, nullable=False)
    last_check_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class WakeTokenRow(Base):
    """Wake-up token of a device for an event (note « Réveil des appareils »):
    FCM registration token, Web Push subscription (JSON) or APNs token."""
    __tablename__ = "wake_tokens"
    __table_args__ = (UniqueConstraint("event_id", "device_id", "platform", name="uq_wake_token"),)
    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    event_id: Mapped[str] = mapped_column(ForeignKey("events.id"), nullable=False, index=True)
    device_id: Mapped[str] = mapped_column(String(64), nullable=False)
    platform: Mapped[str] = mapped_column(String(16), nullable=False)
    token: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class WakeKeyRow(Base):
    """Key pairs of the server for the wake-ups (Web Push VAPID), created at
    first use; never sent by any API."""
    __tablename__ = "wake_keys"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    private_pem: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class UserPreferencesRow(Base):
    """Personal settings of a user (favourites, preferred colour), keyed by
    the email checked by Cloudflare Access, lower case."""
    __tablename__ = "user_preferences"
    email: Mapped[str] = mapped_column(String(320), primary_key=True)
    data: Mapped[dict] = mapped_column(JSON, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
