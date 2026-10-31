"""Display on a Smart TV or a big screen (« Affichage sur Smart TV et grand
écran », validated on 29 Oct 2026). The TV opens tv.sarcade.org (no
Cloudflare Access: a TV cannot sign in), shows a short code and a QR code;
a PCO approves it from a phone for an event; the TV then receives a token
that only reads that event. Pure logic and small stores, testable alone."""
from __future__ import annotations

import hashlib
import math
import re
import secrets
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no 0/O, 1/I
CODE_TTL_S = 600
TOKEN_TTL_S = 24 * 3600
MAX_PENDING = 200
REQUESTS_PER_MINUTE = 10


@dataclass
class Pairing:
    pair_id: str
    code: str
    created: float
    event_id: str | None = None
    token: str | None = None
    approved_by: str | None = None
    # Public address of the TV (« validation automatique depuis le même
    # réseau », 30 Oct 2026): Cloudflare header, else the client address.
    ip: str | None = None
    # Default name sent by the TV page (brand or system of its browser and
    # resolution, « Écrans TV, identifier chaque écran », 30 Oct 2026).
    name: str = ""


@dataclass
class Store:
    pairings: dict[str, Pairing] = field(default_factory=dict)
    by_code: dict[str, str] = field(default_factory=dict)
    hits: dict[str, list[float]] = field(default_factory=dict)

    def allowed(self, client: str, now: float | None = None) -> bool:
        """At most REQUESTS_PER_MINUTE codes per minute and client address."""
        now = now or time.time()
        recent = [t for t in self.hits.get(client, []) if now - t < 60]
        if len(recent) >= REQUESTS_PER_MINUTE:
            self.hits[client] = recent
            return False
        self.hits[client] = [*recent, now]
        return True

    def _purge(self, now: float) -> None:
        for pid, p in list(self.pairings.items()):
            if now - p.created > CODE_TTL_S:
                self.pairings.pop(pid, None)
                self.by_code.pop(p.code, None)

    def new(self, now: float | None = None, ip: str | None = None, name: str = "") -> Pairing:
        now = now or time.time()
        self._purge(now)
        if len(self.pairings) >= MAX_PENDING:
            raise OverflowError("too_many_pairings")
        code = "".join(secrets.choice(CODE_ALPHABET) for _ in range(6))
        while code in self.by_code:
            code = "".join(secrets.choice(CODE_ALPHABET) for _ in range(6))
        p = Pairing(pair_id=secrets.token_urlsafe(18), code=code, created=now, ip=ip, name=name)
        self.pairings[p.pair_id] = p
        self.by_code[code] = p.pair_id
        return p

    def approve(self, code: str, event_id: str, now: float | None = None,
                approved_by: str | None = None) -> Pairing | None:
        now = now or time.time()
        self._purge(now)
        pid = self.by_code.get(code.strip().upper().replace(" ", ""))
        p = self.pairings.get(pid) if pid else None
        if p is None or p.token is not None:
            return None
        p.event_id, p.token, p.approved_by = event_id, secrets.token_urlsafe(32), approved_by
        return p

    def pending(self, code: str, now: float | None = None) -> Pairing | None:
        """Code still waiting (default name shown before approval)."""
        self._purge(now or time.time())
        pid = self.by_code.get(code.strip().upper().replace(" ", ""))
        p = self.pairings.get(pid) if pid else None
        return p if p is not None and p.token is None else None

    def nearby(self, ip: str | None, now: float | None = None) -> list[Pairing]:
        """Codes still waiting, shown by a TV behind the same public address
        (never validated here: the operator confirms)."""
        self._purge(now or time.time())
        if not ip:
            return []
        return sorted((p for p in self.pairings.values() if p.token is None and p.ip == ip),
                      key=lambda p: p.created)

    def status(self, pair_id: str, now: float | None = None) -> Pairing | None:
        self._purge(now or time.time())
        return self.pairings.get(pair_id)


store = Store()


# --- Name of a screen (« Écrans TV, identifier chaque écran », 30 Oct 2026) --

NAME_MAX = 60
IDENTIFY_S = 5
# Last request of each screen (epoch seconds, in memory: « dernière
# activité » of the list; lost on a restart of the server).
seen: dict[str, float] = {}


def clean_name(value) -> str:
    """Name typed or proposed: text, control characters removed, spaces
    folded, at most NAME_MAX characters (escaped wherever it is shown)."""
    if not isinstance(value, str):
        return ""
    text = "".join(ch if ch.isprintable() else " " for ch in value)
    return " ".join(text.split())[:NAME_MAX]


# --- Day / night mode of the TV page (« Écran TV, mode jour / nuit ») -------
# Pushed by « Envoyer ma vue » (setting Paramètres › Affichage of the device
# that sends); « auto » before any send: night between sunset and sunrise.

THEMES = ("day", "night", "auto")


def theme_night(theme: str | None, weather: dict | None, now: datetime) -> bool:
    """Night or not for the page. Auto without sun times: night (the
    former look of the TV)."""
    if theme == "day":
        return False
    if theme == "night":
        return True
    rise, sset = _time((weather or {}).get("sunrise")), _time((weather or {}).get("sunset"))
    if rise is None or sset is None:
        return True
    # Times of the nearest day: before its sunrise or after its sunset.
    return not rise <= now < sset


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def valid_display(displays: list[dict], token: str, now: float | None = None) -> dict | None:
    """The display of this token: not revoked, not expired."""
    now = now or time.time()
    h = token_hash(token)
    for d in displays:
        if d.get("hash") == h and not d.get("revoked") and d.get("expires", 0) > now:
            return d
    return None


def snapshot(event: dict, positions: list[dict], messages: list[dict]) -> dict:
    """What a TV may show: positions and the general messages only (never
    the direct chats or channels). The logbook is no longer shown on the TV
    (decision of the porteur, 29 Oct 2026)."""
    public = [m for m in messages if not m.get("recipient_ids")]
    return {"event": {"id": event["id"], "name": event.get("name", "")},
            "positions": positions, "messages": public[-12:]}


# --- SOS banner (29 Oct 2026) -----------------------------------------------
# The app sends « 🆘 DÉTRESSE — <who> — position … » (immediate) and
# « Fin de détresse — <who> » (urgent), see distress.body / distress.endBody.

ALERT_WINDOW = timedelta(minutes=10)
END_PREFIXES = ("fin de détresse", "end of distress")


def is_distress(m: dict) -> bool:
    return m.get("priority") == "immediate" and str(m.get("body") or "").lstrip().startswith("🆘")


def is_distress_end(m: dict) -> bool:
    return str(m.get("body") or "").strip().lower().startswith(END_PREFIXES)


def open_distress(messages: list[dict]) -> dict[str, dict]:
    """Distress not closed yet, by sender: the last « 🆘 » message of a sender
    not followed by a « Fin de détresse » of the same sender. General
    messages only, in chronological order."""
    out: dict[str, dict] = {}
    for m in sorted((m for m in messages if not m.get("recipient_ids")),
                    key=lambda m: _time(m.get("created_at")) or datetime.min.replace(tzinfo=UTC)):
        who = m.get("sender_id")
        if is_distress(m):
            out[who] = m
        elif is_distress_end(m):
            out.pop(who, None)
    return out


def alert_state(messages: list[dict], now: datetime, cleared_at: float | None = None) -> dict:
    """Red banner of a TV and the small SOS reminder of its top bar.

    - ``alert``: the last general immediate message of the last 10 minutes,
      unless it was cleared on this screen (``cleared_at``, epoch seconds)
      or it is a distress already closed by a « Fin de détresse » ;
    - ``sos``: senders whose distress is not closed (kept after a clear)."""
    public = [m for m in messages if not m.get("recipient_ids")]
    still = open_distress(public)
    alert = None
    for m in public:
        t = _time(m.get("created_at"))
        if m.get("priority") != "immediate" or t is None or now - t > ALERT_WINDOW:
            continue
        if cleared_at is not None and t.timestamp() <= cleared_at:
            continue
        if is_distress(m) and still.get(m.get("sender_id")) is not m:
            continue
        if alert is None or t >= _time(alert.get("created_at")):
            alert = m
    return {"alert": {k: alert.get(k) for k in ("sender_id", "body", "created_at")} if alert else None,
            "sos": sorted(still)}


def clean_view(view: dict | None, now: float | None = None) -> dict | None:
    """View copied from a phone: centre, zoom, base map. The tile address is
    used by the public TV page, so only an https template is accepted."""
    if view is None:
        return None
    if not isinstance(view, dict):
        raise ValueError("invalid_view")

    def num(key, lo, hi):
        v = view.get(key)
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not lo <= v <= hi:
            raise ValueError(f"invalid_{key}")
        return float(v)

    def text(key, maximum):
        v = view.get(key) or ""
        if not isinstance(v, str) or len(v) > maximum:
            raise ValueError(f"invalid_{key}")
        return v.strip()

    url = text("tile_url", 300) or None
    if url is not None and not (url.startswith("https://") and all(t in url for t in ("{z}", "{x}", "{y}"))
                                and not any(ch in url for ch in "\"'<> ")):
        raise ValueError("invalid_tile_url")
    return {"lat": num("lat", -90, 90), "lon": num("lon", -180, 180), "zoom": round(num("zoom", 1, 20), 2),
            "basemap": text("basemap", 40), "tile_url": url, "attribution": text("attribution", 200),
            **clean_display_options(view),
            "rev": int((now if now is not None else time.time()) * 1000)}



# --- « Envoyer ma vue », step 2: layers, opacities, messages panel --------

DEFAULT_LAYERS = {"operators": True, "aprs": True, "offline": True, "drawings": True, "bearings": True,
                  "highpoints": False, "relays": False}
# One opacity per layer (card « Panneau des couches, opacité incohérente »,
# 30 Oct 2026): APRS, high points and relays apart; a view of the former
# format (ops, draw, brg, ref) gives its group value to the new keys.
DEFAULT_OPACITY = {"ops": 1.0, "aprs": 1.0, "draw": 1.0, "brg": 1.0, "high": 1.0, "relay": 1.0}
OPACITY_LEGACY = {"aprs": "ops", "high": "ref", "relay": "ref"}
# Same threshold as the « opérateurs actifs » counter of the TV page.
OFFLINE_AFTER = timedelta(minutes=10)


def clean_display_options(view: dict) -> dict:
    """Layers, opacities and messages panel of a view; unknown keys are
    dropped, missing keys take the defaults."""
    layers = view.get("layers")
    if layers is None:
        layers = {}
    if not isinstance(layers, dict):
        raise ValueError("invalid_layers")
    out_layers = {}
    for key, default in DEFAULT_LAYERS.items():
        v = layers.get(key, default)
        if not isinstance(v, bool):
            raise ValueError("invalid_layers")
        out_layers[key] = v
    opacity = view.get("opacity")
    if opacity is None:
        opacity = {}
    if not isinstance(opacity, dict):
        raise ValueError("invalid_opacity")
    out_opacity = {}
    for key, default in DEFAULT_OPACITY.items():
        v = opacity.get(key, opacity.get(OPACITY_LEGACY.get(key, key), default))
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or not 0.1 <= v <= 1:
            raise ValueError("invalid_opacity")
        out_opacity[key] = round(float(v), 2)
    messages = view.get("messages", True)
    if not isinstance(messages, bool):
        raise ValueError("invalid_messages")
    sender = view.get("from") or ""
    if not isinstance(sender, str) or len(sender) > 64:
        raise ValueError("invalid_from")
    theme = view.get("theme") or "auto"
    if theme not in THEMES:
        raise ValueError("invalid_theme")
    return {"layers": out_layers, "opacity": out_opacity, "messages": messages, "from": sender.strip(), "theme": theme}


def display_options(view: dict | None) -> dict:
    """Options of a stored view (or the defaults: automatic framing, or a
    view stored before step 2)."""
    try:
        return clean_display_options(view or {})
    except ValueError:
        return clean_display_options({})


def _time(value) -> datetime | None:
    if isinstance(value, datetime):
        t = value
    elif isinstance(value, str):
        try:
            t = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    return t.replace(tzinfo=UTC) if t.tzinfo is None else t


def apply_view(snap: dict, view: dict | None, now: datetime, *, weather: dict | None = None,
               drawings: list | None = None, bearings: list | None = None,
               references: list | None = None) -> dict:
    """Keeps in the snapshot only what the layers of the view show."""
    opts = display_options(view)
    layers = opts["layers"]

    def shown(p: dict) -> bool:
        if p.get("source") == "aprs":
            return layers["aprs"]
        if not layers["operators"]:
            return False
        t = _time(p.get("time"))
        return layers["offline"] or (t is not None and now - t <= OFFLINE_AFTER)

    out = dict(snap)
    out["positions"] = [p for p in snap.get("positions", []) if shown(p)]
    # « messages » only hides the panel: the immediate-message banner stays.
    out["options"] = opts
    out["weather"] = weather
    if drawings is not None:
        out["drawings"] = drawings if layers["drawings"] else []
    if bearings is not None:
        out["bearings"] = bearings if layers["bearings"] else []
        out["zones"] = probable_zones(out["bearings"])
    if references is not None:
        out["references"] = [r for r in references
                             if (r.get("category") == "HIGH_POINT" and layers["highpoints"])
                             or (r.get("category") == "RELAY" and layers["relays"])]
    return out


# Colours of the searches, the same as the application (`beaconColor` /
# `searchColor` of lib/src/features/bearings/bearing_models.dart): fixed
# beacon colours (406 MHz violet, 121,5 MHz red, exercise 121,375 MHz blue),
# else a palette without violet, red or plain blue, stable from the name.
BEACON_406, BEACON_1215, BEACON_EXERCISE = "#9B4DFF", "#E53935", "#1E88E5"
SEARCH_COLORS = ("#3DD6F5", "#FFB020", "#4ADE80", "#FF7AB6", "#FFE066")
_FREQ = re.compile(r"(\d{3})(?:[.,](\d+))?")


def beacon_color(search: str) -> str | None:
    for m in _FREQ.finditer(search or ""):
        try:
            f = float(f"{m.group(1)}.{m.group(2) or '0'}")
        except ValueError:
            continue
        if 406 <= f < 407:
            return BEACON_406
        if abs(f - 121.375) < 0.001:
            return BEACON_EXERCISE
        if abs(f - 121.5) < 0.001:
            return BEACON_1215
    return None


def search_color(search: str) -> str:
    b = beacon_color(search)
    if b is not None:
        return b
    if not search:
        return SEARCH_COLORS[0]
    h = 0
    units = search.encode("utf-16-le")        # Dart codeUnits are UTF-16
    for i in range(0, len(units), 2):
        h = (h * 31 + int.from_bytes(units[i:i + 2], "little")) & 0x7FFFFFFF
    return SEARCH_COLORS[h % len(SEARCH_COLORS)]


def tv_drawing(feature: dict) -> dict:
    """Map feature as drawn by the TV (no author, no history)."""
    keep = ("id", "kind", "points", "radius_m", "color", "stroke_width", "label", "opacity")
    return {k: feature.get(k) for k in keep if feature.get(k) is not None}


def tv_bearing(feature: dict) -> dict | None:
    b = feature.get("bearing") or {}
    points = feature.get("points") or []
    if not points or b.get("azimuth_deg") is None:
        return None
    search = b.get("search") or ""
    return {"id": feature.get("id"), "lat": points[0][0], "lon": points[0][1],
            "azimuth_deg": b["azimuth_deg"], "length_m": b.get("length_m") or 10_000,
            "cone_deg": b.get("cone_deg") if b.get("cone_deg") is not None else 5,
            "search": search, "label": feature.get("label") or "", "exercise": bool(b.get("exercise")),
            "strength": b.get("strength") or 0, "zone": b.get("zone") or "auto",
            "color": search_color(search)}


# --- Probable zone and crossings (card « Écran TV, zone probable et cônes
# absents », 30 Oct 2026): same rule as `crossing` / `probableZone` of the
# application, computed by the server for the TV.

EARTH_RADIUS_M = 6371008.8


def destination(lat: float, lon: float, distance_m: float, bearing_deg: float) -> tuple[float, float]:
    d, b = distance_m / EARTH_RADIUS_M, math.radians(bearing_deg)
    p1, l1 = math.radians(lat), math.radians(lon)
    p2 = math.asin(math.sin(p1) * math.cos(d) + math.cos(p1) * math.sin(d) * math.cos(b))
    l2 = l1 + math.atan2(math.sin(b) * math.sin(d) * math.cos(p1), math.cos(d) - math.sin(p1) * math.sin(p2))
    return math.degrees(p2), (math.degrees(l2) + 540) % 360 - 180


def distance_m(a: tuple[float, float], b: tuple[float, float]) -> float:
    dlat, dlon = math.radians(b[0] - a[0]), math.radians(b[1] - a[1])
    h = math.sin(dlat / 2) ** 2 + math.cos(math.radians(a[0])) * math.cos(math.radians(b[0])) * math.sin(dlon / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(min(1, math.sqrt(h)))


def _end(b: dict) -> tuple[float, float]:
    return destination(b["lat"], b["lon"], float(b["length_m"]), float(b["azimuth_deg"]))


def crossing(a: dict, b: dict) -> tuple[float, float] | None:
    """Intersection of two bearing segments, planar around them; None when
    parallel or not crossing."""
    k = math.cos((a["lat"] + b["lat"]) / 2 * math.pi / 180)

    def xy(p):
        return p[1] * k, p[0]
    (x1, y1), (x2, y2) = xy((a["lat"], a["lon"])), xy(_end(a))
    (x3, y3), (x4, y4) = xy((b["lat"], b["lon"])), xy(_end(b))
    d = (x2 - x1) * (y4 - y3) - (y2 - y1) * (x4 - x3)
    if abs(d) < 1e-12:
        return None
    t = ((x3 - x1) * (y4 - y3) - (y3 - y1) * (x4 - x3)) / d
    u = ((x3 - x1) * (y2 - y1) - (y3 - y1) * (x2 - x1)) / d
    if t < 0 or t > 1 or u < 0 or u > 1:
        return None
    return y1 + t * (y2 - y1), (x1 + t * (x2 - x1)) / k


def format_distance(m: float) -> str:
    """« 850 m », « 1,25 km », « 12,4 km » (formatDistance of the app)."""
    if m < 1000:
        return f"{round(m)} m"
    return f"{m / 1000:.{2 if m < 10000 else 1}f} km".replace(".", ",")


def round_distance(m: float) -> str:
    """Rounded for a label (card « Zone probable », 31 Oct 2026): « ±700 m »
    rather than « ±699 m », « 1,3 km », « 12 km »."""
    if m < 1000:
        return f"{round(m / 10) * 10:.0f} m"
    if m < 10000:
        return f"{m / 1000:.1f} km".replace(".", ",")
    return f"{round(m / 1000)} km"


# Robust probable zone (card « Zone probable : libellé trop gros et relevés
# incohérents », validated 31 Oct 2026, 11 h 07). Same rule as
# `probableZone` of the application (bearing_models.dart), keep in step.
MIN_CROSSING_ANGLE_DEG = 20.0   # nearly parallel bearings cross badly
MIN_TOLERANCE_M = 100.0
# Automatic setting aside stops at this number of bearings kept.
MIN_KEPT = 3
MIN_RADIUS_M = 50.0
ZONE_SHARE = 0.9                 # radius holding ~90 % of the weighted crossings


def _weight(b: dict) -> float:
    """Precision of a bearing: narrow cone and strong signal weigh more."""
    cone = max(1.0, float(b.get("cone_deg") if b.get("cone_deg") is not None else 5))
    return (1.0 + float(b.get("strength") or 0) / 9.0) / (cone * cone)


def _angle(a: dict, b: dict) -> float:
    d = abs(float(a["azimuth_deg"]) - float(b["azimuth_deg"])) % 180
    return min(d, 180 - d)


def _crossings(group: list[dict], used: set[int]) -> list[tuple[tuple[float, float], float]]:
    out = []
    ids = sorted(used)
    for x in range(len(ids)):
        for y in range(x + 1, len(ids)):
            a, b = group[ids[x]], group[ids[y]]
            ang = _angle(a, b)
            if ang < MIN_CROSSING_ANGLE_DEG:
                continue
            c = crossing(a, b)
            if c is not None:
                out.append((c, _weight(a) * _weight(b) * math.sin(math.radians(ang))))
    return out


def _centre(pts: list[tuple[tuple[float, float], float]]) -> tuple[float, float]:
    w = sum(p[1] for p in pts)
    return sum(p[0][0] * p[1] for p in pts) / w, sum(p[0][1] * p[1] for p in pts) / w


def _radius(centre: tuple[float, float], pts: list[tuple[tuple[float, float], float]]) -> float:
    dist = sorted((distance_m(centre, p[0]), p[1]) for p in pts)
    total, acc = sum(w for _, w in dist), 0.0
    for d, w in dist:
        acc += w
        if acc >= ZONE_SHARE * total:
            return max(MIN_RADIUS_M, d)
    return max(MIN_RADIUS_M, dist[-1][0])


def _miss(b: dict, centre: tuple[float, float]) -> tuple[float, float]:
    """Distance from the bearing line to [centre] and distance along it."""
    k = math.cos(math.radians(b["lat"]))
    dx = (centre[1] - b["lon"]) * k * 111_320.0
    dy = (centre[0] - b["lat"]) * 110_540.0
    az = math.radians(float(b["azimuth_deg"]))
    ux, uy = math.sin(az), math.cos(az)
    along = dx * ux + dy * uy
    if along <= 0:
        return math.hypot(dx, dy), 0.0
    return abs(dx * uy - dy * ux), along


def robust_zone(group: list[dict]) -> dict:
    """Zone of one search: bearings set aside by hand (« out ») are left
    out; then, with 4 or more, each bearing is tested against the zone of
    the others and the worst one beyond its error cone is set aside, again
    until all agree. Never below MIN_KEPT bearings (31 Oct 2026, 23 h 47:
    2 set aside out of 4 left a zone on a single crossing). Bearings put
    back by hand (« in ») are always kept."""
    used = {i for i, b in enumerate(group) if b.get("zone") != "out"}
    auto: list[int] = []
    while len(used) > MIN_KEPT:
        worst, worst_ratio = None, 1.0
        for i in sorted(used):
            if group[i].get("zone") == "in":
                continue
            others = _crossings(group, used - {i})
            if not others:
                continue
            centre = _centre(others)
            miss, along = _miss(group[i], centre)
            cone = float(group[i].get("cone_deg") if group[i].get("cone_deg") is not None else 5)
            tol = max(MIN_TOLERANCE_M, along * math.tan(math.radians(max(cone, 1.0)))) + 0.5 * _radius(centre, others)
            if miss / tol > worst_ratio:
                worst, worst_ratio = i, miss / tol
        if worst is None:
            break
        used.discard(worst)
        auto.append(worst)
    pts = _crossings(group, used)
    zone: dict = {"used": len(used), "crossings": [[round(p[0][0], 6), round(p[0][1], 6)] for p in pts],
                  "excluded": [group[i].get("id") for i in sorted(set(range(len(group))) - used)],
                  "auto_excluded": [group[i].get("id") for i in auto]}
    if pts:
        centre = _centre(pts)
        zone.update(centre=centre, radius_m=_radius(centre, pts))
    return zone


def zone_label(radius_m: float, used: int, excluded: int) -> str:
    """« ±700 m · 4 relevés », « · 1 écarté » when one was set aside.
    Two bearings make one crossing, not a measured precision:
    « zone approximative · 2 relevés »."""
    if used < MIN_KEPT:
        text = f"zone approximative · {used} relevé{'s' if used > 1 else ''}"
    else:
        text = f"±{round_distance(radius_m)} · {used} relevé{'s' if used > 1 else ''}"
    if excluded:
        text += f" · {excluded} écarté{'s' if excluded > 1 else ''}"
    return text


def probable_zones(bearings: list[dict]) -> list[dict]:
    """Crossings of each search and its probable zone (robust_zone)."""
    groups: dict[str, list[dict]] = {}
    for b in bearings:
        groups.setdefault(b.get("search") or "", []).append(b)
    out = []
    for search, group in sorted(groups.items()):
        z = robust_zone(group)
        zone = {"search": search, "color": search_color(search), "exercise": any(b.get("exercise") for b in group),
                "bearings": z["used"], "crossings": z["crossings"], "excluded": z["excluded"]}
        if "centre" in z:
            (lat, lon), radius = z["centre"], z["radius_m"]
            zone.update(lat=round(lat, 6), lon=round(lon, 6), radius_m=round(radius, 1),
                        label=zone_label(radius, z["used"], len(z["excluded"])))
        out.append(zone)
    return out


# --- Weather in the top bar -------------------------------------------------

VIGILANCE = {2: "yellow", 3: "orange", 4: "red"}


def _icon(code, cloud, night: bool) -> str:
    code = int(code) if isinstance(code, (int, float)) and not isinstance(code, bool) else None
    if code in (95, 96, 99):
        return "storm"
    if code is not None and (71 <= code <= 77 or code in (85, 86)):
        return "snow"
    if code in (45, 48):
        return "fog"
    if code is not None and (51 <= code <= 67 or 80 <= code <= 82):
        return "rain"
    if code == 3:
        return "cloud"
    if code in (1, 2):
        clear = False
    elif code == 0:
        clear = True
    elif isinstance(cloud, (int, float)):
        if cloud >= 70:
            return "cloud"
        clear = cloud < 25
    else:
        clear = True
    if night:
        return "moon" if clear else "moon_cloud"
    return "sun" if clear else "sun_cloud"


def is_night(forecast: dict, now: datetime) -> bool:
    """Between sunset and sunrise, from the sun times of the forecast."""
    days = []
    for d in forecast.get("daily") or []:
        rise, sset = _time(d.get("sunrise")), _time(d.get("sunset"))
        if rise and sset:
            days.append((rise, sset))
    if not days:
        return False
    return not any(rise <= now < sset for rise, sset in days)


def weather_now(forecast: dict | None, vigilance: dict | None, now: datetime) -> dict | None:
    """Temperature and icon of the current hour, vigilance colour; None
    without a forecast for this hour."""
    if not forecast:
        return None
    hour = now.replace(minute=0, second=0, microsecond=0)
    current = None
    for h in forecast.get("hourly") or []:
        t = _time(h.get("time"))
        if t is not None and hour <= t + timedelta(hours=1) and t <= now and (current is None or t > current[0]):
            current = (t, h)
    if current is None:
        return None
    h = current[1]
    temp = h.get("temperature_c")
    if isinstance(temp, bool) or not isinstance(temp, (int, float)):
        return None
    color = (vigilance or {}).get("color_id")
    out = {"temp_c": round(float(temp), 1), "icon": _icon(h.get("weather_code"), h.get("cloud_cover_pct"), is_night(forecast, now)),
           "vigilance": VIGILANCE.get(color) if isinstance(color, int) else None}
    out.update(sun_times(forecast, now))
    return out


def sun_times(forecast: dict, now: datetime) -> dict:
    """Sunrise and sunset of the day, ISO in UTC (shown in local time by the
    TV, 29 Oct 2026: they replace the weather icon); empty when unknown."""
    days = []
    for d in forecast.get("daily") or []:
        rise, sset = _time(d.get("sunrise")), _time(d.get("sunset"))
        if rise and sset:
            days.append((rise, sset))
    if not days:
        return {}
    rise, sset = min(days, key=lambda d: abs((d[0] - now).total_seconds()))
    return {"sunrise": rise.isoformat(), "sunset": sset.isoformat()}


@dataclass
class WeatherCache:
    """Weather of each event computed at most every 10 minutes: the TV polls
    every 10 s, the forecast changes every hour."""
    ttl_s: float = 600
    entries: dict = field(default_factory=dict)

    def get(self, event_id: str, compute, now: float | None = None):
        now = now if now is not None else time.time()
        hit = self.entries.get(event_id)
        if hit is not None and now - hit[0] < self.ttl_s:
            return hit[1]
        value = compute()
        self.entries[event_id] = (now, value)
        return value


weather_cache = WeatherCache()
