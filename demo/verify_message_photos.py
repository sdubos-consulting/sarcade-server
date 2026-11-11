"""Integration check of photos sent in messages.

Runs against a live server: idempotent upload with a client file id, retry,
conflict, invalid id, message with attachment, change feed, messages list,
logbook, download and invalid attachments.
Usage: SARCADE_URL=http://localhost:8000 python demo/verify_message_photos.py
"""
import os
import sys
import uuid
from datetime import datetime, timezone

import httpx

BASE = os.getenv("SARCADE_URL", "http://localhost:8000")
c = httpx.Client(timeout=10)
NOW = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
# Smallest valid JPEG header is enough: the server stores bytes as they are.
PHOTO = bytes.fromhex("ffd8ffe000104a46494600010100000100010000ffd9")


def check(cond, msg):
    if not cond:
        print(f"FAIL: {msg}")
        sys.exit(1)
    print(f"ok   {msg}")


event = c.post(f"{BASE}/api/v0.1/events", json={"name": "CI photos", "kind": "exercise"}).json()["id"]
fid = str(uuid.uuid4())


def upload(file_id, data=PHOTO):
    return c.post(f"{BASE}/api/v0.1/events/{event}/files",
                  data={"sender_id": "TERRAIN-01", "file_id": file_id, "mime_type": "image/jpeg"},
                  files={"file": ("photo-ci.jpg", data, "application/octet-stream")})


r = upload(fid)
check(r.status_code == 201 and r.json()["id"] == fid, "upload with client file id")
check(r.json()["mime_type"] == "image/jpeg", "declared mime type kept")
r = upload(fid)
check(r.status_code == 200 and r.json()["id"] == fid, "retried upload is idempotent")
check(len([f for f in c.get(f"{BASE}/api/v0.1/events/{event}/files").json() if f["id"] == fid]) == 1, "no duplicate file")
check(upload(fid, PHOTO + b"x").status_code == 409, "same id with other content is a conflict")
check(upload("../escape").status_code == 422, "unsafe file id refused")

mid = str(uuid.uuid4())
message = {"id": mid, "event_id": event, "sender_id": "TERRAIN-01", "recipient_ids": [], "priority": "urgent",
           "body": "Véhicule repéré", "created_at": NOW,
           "attachments": [{"file_id": fid, "name": "photo-ci.jpg", "mime_type": "image/jpeg", "size_bytes": len(PHOTO)}]}
op = {"operation_id": str(uuid.uuid4()), "event_id": event, "object_id": mid, "object_type": "message",
      "action": "create", "client_time": NOW, "payload": message}
check(c.post(f"{BASE}/api/v0.1/sync", json=[op]).json()[0]["status"] == "accepted", "message with photo accepted")

feed = c.get(f"{BASE}/api/v0.1/events/{event}/sync/changes?after=0").json()["changes"]
fed = [ch for ch in feed if ch["object_id"] == mid]
check(fed and fed[0]["payload"]["attachments"][0]["file_id"] == fid, "attachment in change feed")
listed = [m for m in c.get(f"{BASE}/api/v0.1/events/{event}/messages").json() if m["id"] == mid]
check(listed and listed[0]["attachments"][0]["file_id"] == fid, "attachment in messages list")
log = c.get(f"{BASE}/api/v0.1/events/{event}/logbook").json()
check(any(e["object_id"] == mid and "[1 photo]" in e["summary"] for e in log), "logbook mentions the photo")

d = c.get(f"{BASE}/api/v0.1/events/{event}/files/{fid}/content")
check(d.status_code == 200 and d.content == PHOTO, "photo downloaded intact")
check(d.headers.get("x-content-type-options") == "nosniff", "download is not sniffed")

bad = dict(message, id=str(uuid.uuid4()), attachments=[{"file_id": "../x"}])
bad_op = dict(op, operation_id=str(uuid.uuid4()), object_id=bad["id"], payload=bad)
check(c.post(f"{BASE}/api/v0.1/sync", json=[bad_op]).json()[0]["status"] == "rejected", "invalid attachment rejected")
# Subfolders of the event files.
F = f"{BASE}/api/v0.1/events/{event}/files"
r = c.post(f"{F}/folders", json={"path": "Procédures/Radio", "actor_id": "PCO"})
check(r.status_code == 201 and r.json()["folders"] == ["Procédures", "Procédures/Radio"], "folder created with its parent")
check(c.post(f"{F}/folders", json={"path": "Procédures"}).status_code == 409, "existing folder refused")
check(c.post(f"{F}/folders", json={"path": "A/B/C/D"}).status_code == 400, "folder deeper than 3 refused")
r = c.patch(f"{F}/{fid}", json={"folder": "Procédures/Radio", "actor_id": "PCO"})
check(r.status_code == 200 and r.json()["folder"] == "Procédures/Radio", "file moved into a folder")
check(c.delete(f"{F}/folders", params={"path": "Procédures"}).status_code == 409, "folder not empty kept")
r = c.patch(f"{F}/folders", json={"from": "Procédures", "to": "Docs"})
check(r.status_code == 200 and "Docs/Radio" in r.json()["folders"], "folder renamed with its subfolders")
listing = c.get(f"{F}?with_folders=true").json()
check([f["folder"] for f in listing["files"] if f["id"] == fid] == ["Docs/Radio"], "file follows its folder")
check(isinstance(c.get(F).json(), list), "plain file list kept for older applications")
print("message photos: all checks passed")
