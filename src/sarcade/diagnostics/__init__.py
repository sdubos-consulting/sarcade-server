"""Server block of « Paramètres › Diagnostic » (lot 5, card « Diagnostic
étoffé », validated on 30 Oct 2026, 2 h 58): general state, version,
uptime, components in green / orange / red, processor, memory and disk seen
from the container (alert above 80 %), last automatic update and last
backup when known.

No heavy dependency: the figures are read from ``/proc`` and
``os.statvfs`` (Linux); elsewhere they are null.
"""
from __future__ import annotations

import os
import time
from datetime import UTC, datetime
from pathlib import Path

ALERT_PCT = 80.0
STARTED = time.time()
PROC = Path("/proc")
# Last request seen through Cloudflare (header cf-ray), set by the app.
last_cloudflare_at: float | None = None


def seen_cloudflare(headers) -> None:
    global last_cloudflare_at
    if headers.get("cf-ray"):
        last_cloudflare_at = time.time()


def uptime_s(now: float | None = None) -> int:
    return int((now or time.time()) - STARTED)


def _read(path: Path) -> str | None:
    try:
        return path.read_text()
    except OSError:
        return None


def cpu_times(text: str | None) -> tuple[int, int] | None:
    """(busy, total) jiffies of the first ``cpu`` line of /proc/stat."""
    for line in (text or "").splitlines():
        if line.startswith("cpu "):
            v = [int(x) for x in line.split()[1:]]
            idle = v[3] + (v[4] if len(v) > 4 else 0)
            return sum(v) - idle, sum(v)
    return None


def cpu_pct(a: tuple[int, int] | None, b: tuple[int, int] | None) -> float | None:
    if not a or not b or b[1] <= a[1]:
        return None
    return round(100 * (b[0] - a[0]) / (b[1] - a[1]), 1)


def memory(meminfo: str | None) -> dict | None:
    """Used / total from /proc/meminfo (MemAvailable), in MB and %."""
    values = {}
    for line in (meminfo or "").splitlines():
        name, _, rest = line.partition(":")
        parts = rest.split()
        if parts and parts[0].isdigit():
            values[name] = int(parts[0])
    total, avail = values.get("MemTotal"), values.get("MemAvailable")
    if not total or avail is None:
        return None
    used = total - avail
    return {"used_mb": used // 1024, "total_mb": total // 1024, "pct": round(100 * used / total, 1)}


def disk(path: str) -> dict | None:
    try:
        st = os.statvfs(path)
    except (OSError, AttributeError):
        return None
    total = st.f_blocks * st.f_frsize
    free = st.f_bavail * st.f_frsize
    if total <= 0:
        return None
    used = total - free
    return {"path": path, "used_gb": round(used / 1e9, 1), "total_gb": round(total / 1e9, 1),
            "pct": round(100 * used / total, 1)}


def load_average() -> list[float] | None:
    text = _read(PROC / "loadavg")
    try:
        return [float(x) for x in text.split()[:3]] if text else None
    except ValueError:
        return None


def resources(disk_path: str, sample_s: float = 0.2) -> dict:
    a = cpu_times(_read(PROC / "stat"))
    if a is not None and sample_s > 0:
        time.sleep(sample_s)
    b = cpu_times(_read(PROC / "stat"))
    out = {"cpu_pct": cpu_pct(a, b), "cpu_count": os.cpu_count(), "load": load_average(),
           "memory": memory(_read(PROC / "meminfo")), "disk": disk(disk_path)}
    pcts = [out["cpu_pct"], (out["memory"] or {}).get("pct"), (out["disk"] or {}).get("pct")]
    out["alert"] = any(p is not None and p > ALERT_PCT for p in pcts)
    return out


def overall(components: dict[str, str]) -> str:
    """ok (all green), degraded (an orange or a red other than the base), down (base red)."""
    if components.get("database") == "red":
        return "down"
    return "ok" if all(v in ("green", "off") for v in components.values()) else "degraded"


def iso(ts: float | None) -> str | None:
    return None if ts is None else datetime.fromtimestamp(ts, UTC).isoformat().replace("+00:00", "Z")
