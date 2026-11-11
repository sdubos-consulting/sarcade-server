import os, time, uuid, math
from datetime import datetime, timezone
import httpx

BASE=os.getenv("SARCADE_URL","http://localhost:8000")
client=httpx.Client(timeout=5)

def wait():
    for _ in range(60):
        try:
            if client.get(f"{BASE}/health").status_code==200:return
        except Exception: pass
        time.sleep(1)
    raise SystemExit("SARCADE Server unavailable")

def create_event():
    r=client.post(f"{BASE}/api/v0.1/events",json={"name":"Exercice SARCADE Demo","kind":"exercise","summary":"Recette automatique"})
    r.raise_for_status(); return r.json()["id"]

def position(event,device,lat,lon):
    p={"id":str(uuid.uuid4()),"event_id":event,"device_id":device,"lat":lat,"lon":lon,
       "accuracy_m":5.0,"time":datetime.now(timezone.utc).isoformat()}
    r=client.post(f"{BASE}/api/v0.1/positions",json=p)
    if r.status_code >= 400:
        print(f"Position rejected: {r.status_code} {r.text}")
    r.raise_for_status()

wait()
event=os.getenv("SARCADE_EVENT_ID") or create_event()
print(f"EVENT_ID={event}")
print("Mode: existing event" if os.getenv("SARCADE_EVENT_ID") else "Mode: new event")
origin=(48.8566,2.3522)
print("Keep SARCADE App open: positions are broadcast live over WebSocket.")
for step in range(20):
    for n in range(4):
        angle=(step+n*5)/12
        position(event,f"operator-{n+1}",origin[0]+0.006*math.sin(angle),origin[1]+0.009*math.cos(angle))
    print(f"step {step+1}/20 - 4 positions sent")
    time.sleep(1)
print(f"Open http://localhost:8000/docs ; event={event}")
