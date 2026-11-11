import os, sys, httpx

BASE=os.getenv("SARCADE_URL","http://server:8000")
event=os.environ["SARCADE_EVENT_ID"]
device=os.getenv("SARCADE_DEVICE_ID","pco-windows")

with httpx.Client(timeout=10) as c:
    h=c.get(f"{BASE}/health"); h.raise_for_status()
    messages=c.get(f"{BASE}/api/v0.1/events/{event}/messages"); messages.raise_for_status()
    logbook=c.get(f"{BASE}/api/v0.1/events/{event}/logbook"); logbook.raise_for_status()
    positions=c.get(f"{BASE}/api/v0.1/events/{event}/positions/latest"); positions.raise_for_status()

ms=messages.json(); lb=logbook.json(); ps=positions.json()
needle=os.getenv("SARCADE_OFFLINE_MARKER","OFFLINE-V01")
matched=[m for m in ms if needle in (m.get("body") or "")]
ids=[m.get("id") for m in matched]
duplicate_ids=len(ids)!=len(set(ids))
print(f"event={event}")
print(f"messages={len(ms)} offline_marker_messages={len(matched)} duplicate_ids={duplicate_ids}")
print(f"logbook={len(lb)} latest_positions={len(ps)}")
if not matched:
    print(f"FAIL: no message containing {needle!r} reached the server")
    sys.exit(2)
if duplicate_ids:
    print("FAIL: duplicate message IDs detected")
    sys.exit(3)
print("PASS: offline-created message reached server without duplicate ID")
