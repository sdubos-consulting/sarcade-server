import os, uuid
from datetime import datetime, timezone
import httpx

BASE=os.getenv("SARCADE_URL","http://localhost:8000")
c=httpx.Client(timeout=10)
event=c.post(f"{BASE}/api/v0.1/events",json={"name":"CI Demo","kind":"exercise"}).json()["id"]
opid=str(uuid.uuid4())
msgid=str(uuid.uuid4())
payload={"id":msgid,"event_id":event,"sender_id":"operator-1","recipient_ids":["user:pco"],
         "priority":"routine","body":"RAS secteur Nord","created_at":datetime.now(timezone.utc).isoformat()}
op={"operation_id":opid,"event_id":event,"object_id":msgid,"object_type":"message","action":"create",
    "client_time":datetime.now(timezone.utc).isoformat(),"payload":payload}
a=c.post(f"{BASE}/api/v0.1/sync",json=[op]);a.raise_for_status()
b=c.post(f"{BASE}/api/v0.1/sync",json=[op]);b.raise_for_status()
assert a.json()[0]["status"]=="accepted"
assert b.json()[0]["status"]=="duplicate"
messages=c.get(f"{BASE}/api/v0.1/events/{event}/messages").json()
assert sum(x["id"]==msgid for x in messages)==1
log=c.get(f"{BASE}/api/v0.1/events/{event}/logbook").json()
assert any(x["object_id"]==msgid for x in log)
print("PASS: accepted -> duplicate, one message, logbook populated")
