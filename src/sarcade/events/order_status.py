"""Status of the operator that follows his move orders (card « Ordres de
déplacement : statut mis à jour automatiquement », lot 27, choices
validated by the porteur on 3 Nov 2026, 17 h 21):

1. order accepted → « En route »;
2. arrived (button, or arrival detected and confirmed) → « Sur place »;
3. order refused or cancelled by the PCO → back to the status before the
   order (kept on the order, ``operator_status_before``);
4. « Terminé, disponible » of an arrived order (``finished_at`` set) →
   « Disponible »;
5. one rule for the phone, the PC and the car: applied by the server when
   the order changes, whoever sends the change and whenever it arrives
   (Outbox);
6. a status chosen by hand is never replaced before the next event of the
   order: the automatic status carries the time of the answer
   (``updated_at`` of the order), and the « newer wins » rule of
   sarcade.events.operator_status keeps a later manual choice;
7. the logbook tells why (« Statut de F4ABC : En route, ordre accepté »).

Pure functions; sarcade.api.routes applies them.
"""
from __future__ import annotations

# Field of the order data: {device_id: status or None} before the order.
BEFORE_FIELD = "operator_status_before"
# Field of the order data: time of « Terminé, disponible ».
FINISHED_FIELD = "finished_at"

REASONS = {"accepted": "ordre accepté", "arrived": "arrivée sur zone", "refused": "ordre refusé",
           "cancelled": "ordre annulé", "finished": "mission terminée"}


def transition(before: dict | None, after: dict) -> str | None:
    """Event of the order that moves the status: accepted, arrived, refused,
    cancelled, finished, or None (creation, note of the PCO, same answer)."""
    if before is None:
        return None
    old, new = before.get("status"), after.get("status")
    if new == "arrived" and old == "arrived":
        return "finished" if after.get(FINISHED_FIELD) and not before.get(FINISHED_FIELD) else None
    if new == old:
        return None
    return new if new in ("accepted", "arrived", "refused", "cancelled") else None


def target_status(event: str, saved: dict, device_id: str) -> tuple[bool, str | None]:
    """Status to set on [device_id] for [event]: (True, status) or (False,
    None) when nothing changes. Refused or cancelled: the status saved
    before the order, only for a device whose status the order changed."""
    if event == "accepted":
        return True, "en_route"
    if event == "arrived":
        return True, "sur_place"
    if event == "finished":
        return True, "disponible"
    if event in ("refused", "cancelled") and device_id in saved:
        return True, saved[device_id]
    return False, None


def keep_fields(before: dict | None, data: dict) -> dict:
    """Fields kept by the server across the changes of the order (the
    clients do not send them back): the statuses before the order, and
    « Terminé » while the order stays arrived."""
    out = {k: v for k, v in data.items() if k not in (BEFORE_FIELD, FINISHED_FIELD)}
    if before and isinstance(before.get(BEFORE_FIELD), dict):
        out[BEFORE_FIELD] = dict(before[BEFORE_FIELD])
    if out.get("status") == "arrived":
        finished = (before or {}).get(FINISHED_FIELD) if (before or {}).get("status") == "arrived" else None
        finished = finished or data.get(FINISHED_FIELD)
        if finished:
            out[FINISHED_FIELD] = finished
    return out
