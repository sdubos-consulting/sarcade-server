"""HTTP middleware: journals administration actions and suspicious
behaviour (bursts of requests, repeated refused accesses)."""
from __future__ import annotations

from collections import defaultdict, deque
import logging
import re
import time

from starlette.concurrency import run_in_threadpool

from . import journal

log = logging.getLogger("sarcade.security")

# Administration actions (method, path) journaled with their outcome.
ADMIN_ROUTES = [
    (("POST",), re.compile(r"^/api/v0\.1/events$"), "event_created"),
    (("PATCH",), re.compile(r"^/api/v0\.1/events/(?P<event>[^/]+)/settings$"), "event_settings_changed"),
    (("POST",), re.compile(r"^/api/v0\.1/events/(?P<event>[^/]+)/close$"), "event_closed"),
    (("POST", "PUT", "DELETE"), re.compile(r"^/api/v0\.1/aprs/groups(/[^/]+)?$"), "aprs_groups_changed"),
    (("POST", "DELETE"), re.compile(r"^/api/v0\.1/basemaps(/[^/]+)?$"), "basemap_changed"),
    (("PUT",), re.compile(r"^/api/v0\.1/basemaps/[^/]+/package$"), "basemap_package_uploaded"),
    (("POST",), re.compile(r"^/api/v0\.1/reference-sites/import/.*$"), "reference_data_imported"),
    (("GET",), re.compile(r"^/api/v0\.1/events/(?P<event>[^/]+)/routes/[^/]+\.gpx$"), "data_exported"),
]
REFUSED = {401, 403, 404, 405}
WINDOW_S = 60
REFUSED_THRESHOLD = 50
REQUEST_THRESHOLD = 1200
ALERT_SILENCE_S = 600


class Watch:
    def __init__(self):
        self.requests: dict[str, deque] = defaultdict(deque)
        self.refused: dict[str, deque] = defaultdict(deque)
        self.alerted: dict[tuple[str, str], float] = {}

    @staticmethod
    def _push(q: deque, now: float) -> int:
        q.append(now)
        while q and q[0] < now - WINDOW_S:
            q.popleft()
        return len(q)

    def observe(self, ip: str, status: int, now: float | None = None) -> list[tuple[str, int]]:
        """Returns the alerts to journal (kind, count)."""
        now = now or time.monotonic()
        alerts = []
        n = self._push(self.requests[ip], now)
        if n >= REQUEST_THRESHOLD and self._may_alert(ip, "request_burst", now):
            alerts.append(("request_burst", n))
        if status in REFUSED:
            r = self._push(self.refused[ip], now)
            if r >= REFUSED_THRESHOLD and self._may_alert(ip, "refused_burst", now):
                alerts.append(("refused_burst", r))
        if len(self.requests) > 10_000:  # bounded memory
            for key in [k for k, q in self.requests.items() if not q or q[-1] < now - WINDOW_S]:
                self.requests.pop(key, None)
                self.refused.pop(key, None)
        return alerts

    def _may_alert(self, ip: str, kind: str, now: float) -> bool:
        last = self.alerted.get((ip, kind))
        if last is not None and now - last < ALERT_SILENCE_S:
            return False
        self.alerted[(ip, kind)] = now
        return True


watch = Watch()


def admin_action(method: str, path: str):
    for methods, pattern, action in ADMIN_ROUTES:
        if method in methods:
            m = pattern.match(path)
            if m:
                return action, m.groupdict().get("event")
    return None


async def middleware(request, call_next):
    response = await call_next(request)
    ip = request.client.host if request.client else None
    path, method, status = request.url.path, request.method, response.status_code
    try:
        for kind, count in watch.observe(ip or "-", status):
            await run_in_threadpool(journal.record_now, "suspicious", kind, "info", source_ip=ip,
                                    details={"count": count, "window_s": WINDOW_S, "last_path": path[:120]})
        found = admin_action(method, path)
        if found and status != 404:
            action, event_id = found
            outcome = "success" if status < 400 else ("refused" if status in (401, 403, 409) else "failure")
            await run_in_threadpool(journal.record_now, "admin", action, outcome, source_ip=ip, event_id=event_id,
                                    actor=request.headers.get("x-sarcade-actor"),
                                    details={"method": method, "path": path[:160], "status": status})
    except Exception as exc:  # noqa: BLE001 — the journal never breaks a request
        log.warning("security journal not written: %s", exc)
    return response
