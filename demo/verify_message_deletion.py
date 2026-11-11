"""Integration check of the deletion of messages for everyone (29 Oct 2026):
the author deletes his message, another operator cannot, the PCO deletes any
message, content and photos disappear for the operators, the logbook keeps
a trace.

Usage: SARCADE_URL=http://localhost:8000 python demo/verify_message_deletion.py
"""
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone

import httpx

BASE = os.getenv("SARCADE_URL", "http://localhost:8000")
API = f"{BASE}/api/v0.1"
c = httpx.Client(timeout=10)
PHOTO = bytes.fromhex("ffd8ffe000104a46494600010100000100010000ffd9")


def check(cond, msg):
    if not cond:
        print(f"::error::FAIL: {msg}")
        sys.exit(1)
    print(f"ok   {msg}")


def iso(t):
    return t.replace(microsecond=0).isoformat().replace("+00:00", "Z")


NOW = datetime.now(timezone.utc)
event = c.post(f"{API}/events", json={"name": "CI suppression", "kind": "exercise"}).json()["id"]


def sync(op):
    return c.post(f"{API}/sync", json=[op]).json()[0]


def send(sender, when=NOW, attachments=(), reply_to=None):
    mid = str(uuid.uuid4())
    p = {"id": mid, "event_id": event, "sender_id": sender, "recipient_ids": [], "priority": "routine",
         "body": "Texte secret", "created_at": iso(when), "attachments": list(attachments)}
    if reply_to:
        p["reply_to"] = reply_to
    r = sync({"operation_id": str(uuid.uuid4()), "event_id": event, "object_id": mid, "object_type": "message",
              "action": "create", "client_time": iso(when), "payload": p})
    check(r["status"] == "accepted", f"message from {sender} accepted")
    return mid


def delete(mid, actor):
    return sync({"operation_id": str(uuid.uuid4()), "event_id": event, "object_id": mid, "object_type": "message",
                 "action": "delete", "client_time": iso(NOW), "payload": {"deleted_by": actor}})


fid = str(uuid.uuid4())
up = c.post(f"{API}/events/{event}/files", data={"sender_id": "TEL-01", "file_id": fid, "mime_type": "image/jpeg"},
            files={"file": ("p.jpg", PHOTO, "application/octet-stream")})
check(up.status_code == 201, "photo uploaded")
m1 = send("TEL-01", attachments=[{"file_id": fid, "name": "p.jpg", "mime_type": "image/jpeg", "size_bytes": len(PHOTO)}])
check(delete(m1, "TEL-02")["status"] == "rejected", "another operator cannot delete")
check(delete(m1, "TEL-01")["status"] == "accepted", "author deletes for everyone")
listed = {m["id"]: m for m in c.get(f"{API}/events/{event}/messages").json()}
check(listed[m1].get("deleted") and listed[m1]["body"] == "" and listed[m1]["attachments"] == [], "content hidden")
feed = [ch for ch in c.get(f"{API}/events/{event}/sync/changes").json()["changes"] if ch["object_id"] == m1]
check([ch["action"] for ch in feed] == ["create", "delete"], "deletion in change feed")
check(all("Texte secret" not in str(ch["payload"]) for ch in feed), "no content in change feed")
check(c.get(f"{API}/events/{event}/files/{fid}/content").status_code == 410, "photo no longer served")
old = send("TEL-01", when=NOW - timedelta(days=3))
check(delete(old, "TEL-01")["status"] == "rejected", "author refused after 2 days")
check(delete(old, "PCO")["status"] == "accepted", "PCO deletes any message")
log = c.get(f"{API}/events/{event}/logbook").json()
check(sum(1 for e in log if e["kind"] == "message_deleted") == 2, "deletions in the logbook")
check(all("Texte secret" not in e["summary"] for e in log), "no content in the logbook")
kept = c.get(f"{API}/events/{event}/messages/deleted").json()
check(len(kept) == 2 and kept[0]["body"] == "Texte secret", "original kept for the PCO")
# Reply to a message (31 Oct 2026).
r1 = send("TEL-02", reply_to=old)
check({m["id"]: m for m in c.get(f"{API}/events/{event}/messages").json()}[r1].get("reply_to") == old, "reply keeps the quoted message")
r2 = send("TEL-02", reply_to="unknown-id")
check("reply_to" not in {m["id"]: m for m in c.get(f"{API}/events/{event}/messages").json()}[r2], "unknown quoted message dropped")
print("message deletion and reply: all checks passed")
