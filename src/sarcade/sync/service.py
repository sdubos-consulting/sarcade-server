from datetime import UTC, datetime
from sqlalchemy import select
from sqlalchemy.orm import Session
from sarcade.db.models import SyncOperationRow

VALID_TYPES = {"position", "poi", "message", "ack", "map_feature", "comm_group",
               "route", "route_waypoint", "route_passage", "road_closure", "itinerary", "move_order",
               # Status of the operator in one touch (lot 24).
               "operator_status",
               # Reaction on a message (lot 29, group 7).
               "reaction"}

def record_operation(db: Session, *, event_id: str, operation_id: str, object_id: str,
                     object_type: str, action: str, payload: dict, client_time: datetime) -> tuple[str, int]:
    existing = db.scalar(select(SyncOperationRow).where(SyncOperationRow.operation_id == operation_id))
    if existing:
        return "duplicate", existing.seq
    if object_type not in VALID_TYPES:
        return "rejected", 0
    row = SyncOperationRow(operation_id=operation_id,event_id=event_id,object_id=object_id,
        object_type=object_type,action=action,payload=payload,client_time=client_time,
        server_time=datetime.now(UTC),status="accepted")
    db.add(row); db.flush()
    return "accepted", row.seq
