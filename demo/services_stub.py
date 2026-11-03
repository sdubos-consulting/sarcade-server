"""Stand-in for Open-Meteo, the Météo-France vigilance, Valhalla, the GitHub
releases API and a SIEM syslog collector (TCP, octet counting), for the demo
stack and the CI (no Internet, no routing tiles). Serves plausible data."""
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
import hashlib
import json
import os
import socketserver
import threading
from urllib.parse import urlparse

SIEM_RECEIVED: list[str] = []
# windows-dev pre-release of sarcade-app (lot 25, POST /admin/clients/windows/fetch).
WINDOWS_INSTALLER = b"MZ-SARCADE-demo-installer"
WINDOWS_MANIFEST = {"version": "0.1.0+960", "build": "main@demo000", "size": len(WINDOWS_INSTALLER),
                    "sha256": hashlib.sha256(WINDOWS_INSTALLER).hexdigest(), "installer": "sarcade-windows-setup.exe"}


def forecast(path):
    start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    n = 96
    times = [(start + timedelta(hours=i)).strftime("%Y-%m-%dT%H:%M") for i in range(n)]
    hourly = {"time": times, "temperature_2m": [9 + (i % 24) / 3 for i in range(n)],
              "apparent_temperature": [7.0] * n, "precipitation": [0.2] * n,
              "wind_speed_10m": [18.0] * n, "wind_gusts_10m": [55.0] * n,
              "wind_direction_10m": [240] * n, "weather_code": [95 if 14 <= i % 24 <= 17 else 3 for i in range(n)],
              "cloud_cover": [75] * n}
    if path == "/v1/forecast":
        hourly["precipitation_probability"] = [40] * n
        hourly["visibility"] = [12000] * n
    days = [(start + timedelta(days=d)) for d in range(4)]
    return {"latitude": 48.8, "longitude": 2.1, "elevation": 110.0, "hourly": hourly,
            "daily": {"time": [d.strftime("%Y-%m-%d") for d in days],
                      "sunrise": [(d + timedelta(hours=6, minutes=5)).strftime("%Y-%m-%dT%H:%M") for d in days],
                      "sunset": [(d + timedelta(hours=17, minutes=10)).strftime("%Y-%m-%dT%H:%M") for d in days]}}


VIGILANCE = {"product": {"periods": [{"echeance": "J", "timelaps": {"domain_ids": [
    {"domain_id": "78", "max_color_id": 3, "phenomenon_items": [
        {"phenomenon_id": "3", "phenomenon_max_color_id": 3}]},
    {"domain_id": "92", "max_color_id": 1, "phenomenon_items": []}]}}]}}


def polyline6(points):
    out, plat, plon = [], 0, 0
    for lat, lon in points:
        ilat, ilon = round(lat * 1e6), round(lon * 1e6)
        for delta in (ilat - plat, ilon - plon):
            v = ~(delta << 1) if delta < 0 else delta << 1
            while v >= 0x20:
                out.append(chr((0x20 | (v & 0x1F)) + 63))
                v >>= 5
            out.append(chr(v + 63))
        plat, plon = ilat, ilon
    return "".join(out)


def route(body):
    """Dog-leg path between consecutive locations (north, then east), or 400
    when a location is south of 40°N (« no route » for the tests). A request
    avoiding closures adds a detour point."""
    locs = [(l["lat"], l["lon"]) for l in body.get("locations", [])]
    if any(lat < 40 for lat, _ in locs):
        return 400, {"error": "No path could be found for input"}
    legs, total = [], 0.0
    detour = bool(body.get("exclude_polygons"))
    for (la1, lo1), (la2, lo2) in zip(locs, locs[1:]):
        pts = [(la1, lo1), (la2, lo1)] + ([(la2 + 0.002, (lo1 + lo2) / 2)] if detour else []) + [(la2, lo2)]
        km = (abs(la2 - la1) * 111 + abs(lo2 - lo1) * 73) * (1.1 if detour else 1.0)
        total += km
        legs.append({"shape": polyline6(pts), "summary": {"length": km, "time": km * 60},
                     "maneuvers": [{"instruction": "Partez vers le nord.", "type": 1, "length": km / 2,
                                    "time": km * 30, "begin_shape_index": 0},
                                   {"instruction": "Tournez à droite.", "type": 10, "length": km / 2,
                                    "time": km * 30, "begin_shape_index": 1},
                                   {"instruction": "Vous êtes arrivé.", "type": 4, "length": 0, "time": 0,
                                    "begin_shape_index": len(pts) - 1}]})
    out = {"trip": {"legs": legs, "summary": {"length": total, "time": total * 60}}}
    if body.get("alternates") and len(locs) == 2:
        (la1, lo1), (la2, lo2) = locs
        km = (abs(la2 - la1) * 111 + abs(lo2 - lo1) * 73) * 1.25
        east_first = [(la1, lo1), (la1, lo2), (la2, lo2)]
        alt = {"shape": polyline6(east_first), "summary": {"length": km, "time": km * 66},
               "maneuvers": [{"instruction": "Partez vers l'est.", "type": 1, "length": km, "time": km * 66,
                              "begin_shape_index": 0},
                             {"instruction": "Vous êtes arrivé.", "type": 4, "length": 0, "time": 0,
                              "begin_shape_index": 2}]}
        # Second variant identical to the main itinerary: SARCADE must drop it.
        out["alternates"] = [{"trip": {"legs": [alt], "summary": {"length": km, "time": km * 66}}},
                             {"trip": {"legs": legs, "summary": {"length": total, "time": total * 60}}}]
    return 200, out


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        if urlparse(self.path).path != "/route":
            self.send_response(404)
            self.end_headers()
            return
        length = int(self.headers.get("Content-Length", "0"))
        status, body = route(json.loads(self.rfile.read(length) or b"{}"))
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        path = urlparse(self.path).path
        if path in ("/v1/meteofrance", "/v1/forecast"):
            body = forecast(path)
        elif path == "/status":
            body = {"version": "stub"}
        elif path == "/vigilance" and self.headers.get("apikey"):
            body = VIGILANCE
        elif path == "/releases/latest":
            body = {"tag_name": "v9.9.0", "html_url": "https://example.invalid/releases/v9.9.0"}
        elif path == "/windows-dev/sarcade-windows-version.json":
            body = WINDOWS_MANIFEST
        elif path == "/windows-dev/sarcade-windows-setup.exe":
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Length", str(len(WINDOWS_INSTALLER)))
            self.end_headers()
            self.wfile.write(WINDOWS_INSTALLER)
            return
        elif path == "/geocodage/reverse":
            body = {"type": "FeatureCollection", "features": [{"geometry": {"type": "Point",
                    "coordinates": [1.9097, 48.8232]}, "properties": {"street": "Rue des Côteaux de Chatron",
                    "label": "Rue des Côteaux de Chatron 78640 Saint-Germain-de-la-Grange",
                    "city": "Saint-Germain-de-la-Grange", "postcode": "78640"}}]}
        elif path == "/altimetrie/elevation.json":
            body = {"elevations": [112.4]}
        elif path == "/siem/received":
            body = list(SIEM_RECEIVED)
        else:
            self.send_response(404)
            self.end_headers()
            return
        data = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


class SyslogHandler(socketserver.StreamRequestHandler):
    """Syslog over TCP with octet counting framing (RFC 6587 / RFC 5425)."""
    def handle(self):
        while True:
            length = b""
            while (c := self.rfile.read(1)) and c != b" ":
                length += c
            if not c or not length.isdigit():
                return
            SIEM_RECEIVED.append(self.rfile.read(int(length)).decode())


if __name__ == "__main__":
    socketserver.ThreadingTCPServer.allow_reuse_address = True
    siem = socketserver.ThreadingTCPServer(("0.0.0.0", int(os.getenv("SIEM_PORT", "6514"))), SyslogHandler)
    threading.Thread(target=siem.serve_forever, daemon=True).start()
    HTTPServer(("0.0.0.0", int(os.getenv("PORT", "8080"))), Handler).serve_forever()
