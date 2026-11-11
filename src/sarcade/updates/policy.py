"""Pure rules of the updates (note « Mises à jour et sécurité »):
maintenance windows, minimal client version, versions comparison."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
import re
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

CLIENT_GRACE = timedelta(hours=2)  # decision of 10 Oct 2026
ACTIVE_EVENT_IDLE = timedelta(hours=24)
DEFAULTS = {
    "maintenance": {"timezone": "Europe/Paris", "auto_updates_suspended": False,
                    "windows": [{"days": [1], "start": "03:00", "end": "05:00"}]},
    "journal_retention_days": 365,
    # Last version published on the stores (Play Store, TestFlight), shown
    # as « Nouvelle version disponible » on phones; never an obligation.
    "clients": {"min_version": None, "releases": {}},
    # Navigation level 1 (decision of 10 Oct 2026): application opened
    # directly by « Open in… », and hiding of the apps that send data.
    "navigation": {"app": "operator", "hide_tracking_apps": False},
    # APRS-IS server (decision of 26 Oct 2026): empty = SARCADE_APRS_IS_HOST
    # / SARCADE_APRS_IS_PORT of the .env, else euro.aprs2.net:14580.
    "aprs": {"is_host": None, "is_port": None},
}
NAVIGATION_APPS = ("operator", "organic_maps", "osmand", "apple_maps", "google_maps", "waze")
TRACKING_APPS = ("google_maps", "waze")
_HOST = re.compile(r"^(?=.{1,253}$)[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?(\.[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*$")
_HHMM = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")
_VERSION = re.compile(r"^v?\d+(\.\d+){0,3}([-+][0-9A-Za-z.\-+]*)?$")


class InvalidSettings(ValueError):
    pass


def parse_version(v: str | None) -> tuple:
    """« 1.2.3 », « v1.2 », « 1.2.3+45 » → comparable tuple; a pre-release
    (« 1.2.0-rc1 ») sorts before the release."""
    if not v:
        return ()
    v = v.strip().lstrip("vV")
    core = re.split(r"[-+]", v, maxsplit=1)[0]
    nums = []
    for part in core.split("."):
        m = re.match(r"\d+", part)
        nums.append(int(m.group()) if m else 0)
    while len(nums) < 3:
        nums.append(0)
    pre = "-" in v and v.index("-") < (v.index("+") if "+" in v else len(v))
    return tuple(nums) + (0 if pre else 1,)


def is_older(version: str | None, minimum: str | None) -> bool:
    if not minimum:
        return False
    if not version:
        return True
    return parse_version(version) < parse_version(minimum)


def validate(settings: dict) -> dict:
    """Validates the administration settings and fills the defaults."""
    out = {k: (dict(v) if isinstance(v, dict) else v) for k, v in DEFAULTS.items()}
    m = settings.get("maintenance", out["maintenance"])
    if not isinstance(m, dict):
        raise InvalidSettings("invalid_maintenance")
    tz = m.get("timezone", "Europe/Paris")
    try:
        ZoneInfo(tz)
    except (ZoneInfoNotFoundError, ValueError, TypeError):
        raise InvalidSettings("invalid_timezone")
    windows = m.get("windows", [])
    if not isinstance(windows, list) or len(windows) > 14:
        raise InvalidSettings("invalid_windows")
    clean = []
    for w in windows:
        if not isinstance(w, dict):
            raise InvalidSettings("invalid_windows")
        days = w.get("days")
        if not isinstance(days, list) or not days or any(isinstance(d, bool) or d not in range(7) for d in days):
            raise InvalidSettings("invalid_window_days")
        start, end = w.get("start"), w.get("end")
        if not (isinstance(start, str) and _HHMM.match(start) and isinstance(end, str) and _HHMM.match(end)) \
                or start == end:
            raise InvalidSettings("invalid_window_hours")
        clean.append({"days": sorted(set(days)), "start": start, "end": end})
    out["maintenance"] = {"timezone": tz, "windows": clean,
                          "auto_updates_suspended": bool(m.get("auto_updates_suspended", False))}
    days = settings.get("journal_retention_days", 365)
    if isinstance(days, bool) or not isinstance(days, int) or not 30 <= days <= 3650:
        raise InvalidSettings("invalid_journal_retention_days")
    out["journal_retention_days"] = days
    c = settings.get("clients", {}) or {}
    mv = c.get("min_version")
    if mv is not None and (not isinstance(mv, str) or not _VERSION.match(mv.strip())):
        raise InvalidSettings("invalid_min_version")
    out["clients"] = {"min_version": mv.strip() if mv else None, "releases": validate_releases(c.get("releases"))}
    nav = settings.get("navigation", {}) or {}
    if not isinstance(nav, dict):
        raise InvalidSettings("invalid_navigation")
    app = nav.get("app", "operator")
    if app not in NAVIGATION_APPS:
        raise InvalidSettings("invalid_navigation_app")
    hide = nav.get("hide_tracking_apps", False)
    if not isinstance(hide, bool):
        raise InvalidSettings("invalid_hide_tracking_apps")
    if hide and app in TRACKING_APPS:
        raise InvalidSettings("navigation_app_hidden")
    out["navigation"] = {"app": app, "hide_tracking_apps": hide}
    a = settings.get("aprs", {}) or {}
    if not isinstance(a, dict):
        raise InvalidSettings("invalid_aprs")
    host = a.get("is_host")
    if isinstance(host, str):
        host = host.strip() or None
    if host is not None and (not isinstance(host, str) or not _HOST.match(host)):
        raise InvalidSettings("invalid_aprs_is_host")
    port = a.get("is_port")
    if port is not None and (isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535):
        raise InvalidSettings("invalid_aprs_is_port")
    out["aprs"] = {"is_host": host, "is_port": port}
    return out


STORE_PLATFORMS = ("android", "ios")


def validate_releases(releases) -> dict:
    """{"android": {"version": "0.1.0", "build": 415, "url": None}, "ios": …}."""
    if releases is None:
        return {}
    if not isinstance(releases, dict) or set(releases) - set(STORE_PLATFORMS):
        raise InvalidSettings("invalid_releases")
    out = {}
    for platform, r in releases.items():
        if r is None:
            continue
        if not isinstance(r, dict):
            raise InvalidSettings("invalid_releases")
        v, build, url = r.get("version"), r.get("build"), r.get("url")
        if not isinstance(v, str) or not _VERSION.match(v.strip()):
            raise InvalidSettings("invalid_release_version")
        if build is not None and (isinstance(build, bool) or not isinstance(build, int) or build < 0):
            raise InvalidSettings("invalid_release_build")
        if url is not None and (not isinstance(url, str) or not url.startswith(("https://", "itms-beta://", "market://"))):
            raise InvalidSettings("invalid_release_url")
        out[platform] = {"version": v.strip(), "build": build, "url": url}
    return out


def parse_release(value: str | None) -> dict | None:
    """« 0.1.0+415 » (environment variable) → {"version", "build", "url"}."""
    if not value or not value.strip():
        return None
    v = value.strip()
    core, _, build = v.partition("+")
    if not _VERSION.match(core) or (build and not build.isdigit()):
        return None
    return {"version": core, "build": int(build) if build else None, "url": None}


def _minutes(hhmm: str) -> int:
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def in_window(maintenance: dict, now: datetime) -> bool:
    """Days: 0 = Monday … 6 = Sunday, local time of the installation. A window
    ending before it starts runs over midnight (day = day of the start)."""
    local = now.astimezone(ZoneInfo(maintenance.get("timezone", "Europe/Paris")))
    minute = local.hour * 60 + local.minute
    today, yesterday = local.weekday(), (local.weekday() - 1) % 7
    for w in maintenance.get("windows", []):
        s, e = _minutes(w["start"]), _minutes(w["end"])
        if s < e:
            if today in w["days"] and s <= minute < e:
                return True
        else:
            if (today in w["days"] and minute >= s) or (yesterday in w["days"] and minute < e):
                return True
    return False


def next_window(maintenance: dict, now: datetime) -> datetime | None:
    """Start of the next window (UTC), within 8 days."""
    tz = ZoneInfo(maintenance.get("timezone", "Europe/Paris"))
    local = now.astimezone(tz)
    best = None
    for offset in range(8):
        day = (local + timedelta(days=offset)).date()
        for w in maintenance.get("windows", []):
            if day.weekday() not in w["days"]:
                continue
            h, m = (int(x) for x in w["start"].split(":"))
            start = datetime(day.year, day.month, day.day, h, m, tzinfo=tz)
            if start > now and (best is None or start < best):
                best = start
    return best.astimezone(UTC) if best else None


def install_decision(*, available: str | None, installed: str, maintenance: dict, active_events: int,
                     install_requested: bool, now: datetime) -> tuple[bool, str]:
    """Whether the updater installs now. Never during an active event; in a
    maintenance window, or right away when the administrator asked for it."""
    if not available or not is_older(installed, available):
        return False, "up_to_date"
    if active_events:
        return False, "active_event"
    if install_requested:
        return True, "requested_by_administrator"
    if maintenance.get("auto_updates_suspended"):
        return False, "suspended"
    if in_window(maintenance, now):
        return True, "maintenance_window"
    return False, "outside_window"


def client_status(*, app_version: str | None, min_version: str | None, invited_at: datetime | None,
                  now: datetime, in_active_event: bool) -> tuple[str, datetime | None]:
    """« ok », « invited » (2 h to update), « deferred » (the obligation waits
    for the end of the active event) or « required »."""
    if not is_older(app_version, min_version):
        return "ok", None
    deadline = (invited_at or now) + CLIENT_GRACE
    if now < deadline:
        return "invited", deadline
    if in_active_event:
        return "deferred", deadline
    return "required", deadline


def event_is_active(ended_at: datetime | None, last_activity: datetime | None, now: datetime) -> bool:
    """An event not closed with activity in the last 24 hours. An event left
    open and forgotten must not block the updates forever."""
    return ended_at is None and last_activity is not None and now - last_activity < ACTIVE_EVENT_IDLE
