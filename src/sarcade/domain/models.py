"""Initial SARCADE Server domain models.

These lightweight dataclasses mirror sarcade-protocol v0.1.
Persistence mappings will be introduced with the database layer.
"""
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass(slots=True)
class Organization:
    id: str
    name: str
    status: str = "active"


@dataclass(slots=True)
class User:
    id: str
    display_name: str
    status: str = "active"
    locale: str = "fr-FR"
    callsign: str | None = None


@dataclass(slots=True)
class Event:
    id: str
    name: str
    kind: str
    status: str
    created_at: datetime
    version: int = 0


@dataclass(slots=True)
class Team:
    id: str
    event_id: str
    name: str
    status: str = "available"
    version: int = 0


@dataclass(slots=True)
class Position:
    id: str
    event_id: str
    device_id: str
    lat: float
    lon: float
    time: datetime
    alt_m: float | None = None
    accuracy_m: float | None = None


@dataclass(slots=True)
class SyncOperation:
    operation_id: str
    object_id: str
    object_type: str
    action: str
    client_time: datetime
    payload: dict[str, Any] = field(default_factory=dict)
    base_version: int | None = None
    retry_count: int = 0
