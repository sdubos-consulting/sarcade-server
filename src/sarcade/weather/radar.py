# SPDX-License-Identifier: AGPL-3.0-only
# © SDUBOS Consulting et contributeurs SARCADE
"""Rain radar (card « Météo : radar de pluie, aussi en calque sur la
carte », decisions of 6 Nov 2026): the radar mosaic of Météo-France
(rain amount over 5 minutes, mainland France, 500 m), fetched by the server
with the key of the NAS (``SARCADE_METEOFRANCE_API_KEY``, API « Radar » of
the Météo-France portal), kept one hour and served as map tiles: the
devices never contact Météo-France, the key stays on the server.

Fetched only while someone shows the layer (15 minutes after the last
request), every 5 minutes. Licence: Licence Ouverte (Etalab 2.0), source
« Météo-France » shown on the map.
"""
from __future__ import annotations

import asyncio
import gzip
import io
import logging
import math
import os
import time
from collections import OrderedDict
from datetime import UTC, datetime, timedelta

import numpy as np

from . import hdf5lite

log = logging.getLogger(__name__)

URL = ("https://public-api.meteofrance.fr/public/DPRadar/v1/mosaiques/METROPOLE/"
       "observations/LAME_D_EAU/produit?maille=500")
FETCH_EVERY_S = 300
WATCH_S = 900
KEEP = timedelta(minutes=65)
STALE = timedelta(minutes=20)
SOURCE = "Météo-France"

#: Rain rate classes (mm/h), lower bounds, and their colours (RGBA).
CLASSES = [
    (0.1, (155, 215, 255, 150)),
    (1.0, (60, 150, 245, 175)),
    (2.0, (20, 90, 220, 190)),
    (4.0, (40, 180, 70, 200)),
    (8.0, (240, 220, 40, 210)),
    (16.0, (250, 150, 30, 220)),
    (32.0, (230, 40, 30, 230)),
    (64.0, (200, 30, 200, 235)),
]
_PALETTE = np.zeros((256, 4), dtype=np.uint8)
for _k, (_low, _rgba) in enumerate(CLASSES, start=1):
    _PALETTE[_k] = _rgba
_BOUNDS = np.array([c[0] for c in CLASSES])


def legend() -> list[dict]:
    out = []
    for k, (low, rgba) in enumerate(CLASSES):
        high = CLASSES[k + 1][0] if k + 1 < len(CLASSES) else None
        out.append({"min": low, "max": high, "color": "#{:02X}{:02X}{:02X}".format(*rgba[:3])})
    return out


class Frame:
    """One mosaic: rain classes (uint8, 0 = nothing), its grid and time."""

    def __init__(self, at: datetime, classes: np.ndarray, projdef: str, x0: float, y0: float, dx: float, dy: float):
        self.at, self.classes, self.projdef = at, classes, projdef
        self.x0, self.y0, self.dx, self.dy = x0, y0, dx, dy
        self._transformer = None

    @property
    def id(self) -> str:
        return self.at.strftime("%Y%m%d%H%M")

    def transformer(self):
        if self._transformer is None:
            from pyproj import Transformer
            self._transformer = Transformer.from_crs("EPSG:4326", self.projdef, always_xy=True)
        return self._transformer


def _attr(f: hdf5lite.File, names: list[str], key: str, default=None):
    for n in names:
        try:
            node = f.root[n] if n else f.root
        except KeyError:
            continue
        if key in node.attrs:
            v = node.attrs[key]
            return v.item() if hasattr(v, "item") and getattr(v, "size", 2) == 1 else v
    return default


def decode(data: bytes, factor: int = 2) -> Frame:
    """ODIM-H5 mosaic of rain amount (5 min) to rain classes; [factor]
    reduces the grid (the strongest cell is kept)."""
    if data[:2] == b"\x1f\x8b":
        data = gzip.decompress(data)
    f = hdf5lite.read(data)
    path = next((p for p, n in f.root.walk() if n.is_dataset and p.rsplit("/", 1)[-1] == "data"), None)
    if path is None:
        raise hdf5lite.Unsupported("no data in the mosaic")
    parts = path.strip("/").split("/")
    groups = ["/".join(parts[:k]) for k in range(len(parts) - 1, -1, -1)]
    what = [g + "/what" if g else "what" for g in groups]
    where = [g + "/where" if g else "where" for g in groups]
    raw = f.root[path].read()
    gain = float(_attr(f, what, "gain", 1.0))
    offset = float(_attr(f, what, "offset", 0.0))
    nodata = _attr(f, what, "nodata")
    undetect = _attr(f, what, "undetect")
    values = raw.astype(np.float32) * gain + offset
    # Météo-France gives hundredths of millimetre without a gain.
    if gain == 1.0 and offset == 0.0 and raw.dtype.kind in "ui":
        values /= 100.0
    rate = values * 12.0  # mm in 5 min → mm/h
    missing = np.zeros(raw.shape, dtype=bool)
    if nodata is not None:
        missing |= raw == nodata
    if undetect is not None:
        rate[raw == undetect] = 0.0
    cls = np.searchsorted(_BOUNDS, rate, side="right").astype(np.uint8)
    cls[missing] = 0
    if factor > 1:
        h, w = (cls.shape[0] // factor) * factor, (cls.shape[1] // factor) * factor
        cls = cls[:h, :w].reshape(h // factor, factor, w // factor, factor).max(axis=(1, 3))
    projdef = str(_attr(f, where, "projdef", ""))
    if not projdef:
        raise hdf5lite.Unsupported("no projection")
    xscale = float(_attr(f, where, "xscale")) * factor
    yscale = float(_attr(f, where, "yscale", xscale / factor)) * factor
    from pyproj import Transformer
    to_grid = Transformer.from_crs("EPSG:4326", projdef, always_xy=True)
    ul_lon, ul_lat = _attr(f, where, "UL_lon"), _attr(f, where, "UL_lat")
    if ul_lon is not None and ul_lat is not None:
        x0, y0 = to_grid.transform(float(ul_lon), float(ul_lat))
    else:
        llx, lly = to_grid.transform(float(_attr(f, where, "LL_lon")), float(_attr(f, where, "LL_lat")))
        x0, y0 = llx, lly + raw.shape[0] * yscale / factor
    date = str(_attr(f, what + ["what"], "enddate", "") or _attr(f, ["what"], "date", ""))
    hour = str(_attr(f, what + ["what"], "endtime", "") or _attr(f, ["what"], "time", ""))
    try:
        at = datetime.strptime(date + hour[:4], "%Y%m%d%H%M").replace(tzinfo=UTC)
    except ValueError:
        at = datetime.now(UTC).replace(second=0, microsecond=0)
    return Frame(at, cls, projdef, x0, y0, xscale, yscale)


# --- tiles ---------------------------------------------------------------------

def _tile_lonlat(z: int, x: int, y: int, size: int = 256):
    n = 2 ** z
    px = (np.arange(size) + 0.5) / size
    lon = (x + px) / n * 360.0 - 180.0
    merc = math.pi * (1 - 2 * (y + px) / n)
    lat = np.degrees(np.arctan(np.sinh(merc)))
    return np.meshgrid(lon, lat)


def tile_png(frame: Frame, z: int, x: int, y: int, size: int = 256) -> bytes:
    """PNG of the rain classes on one Web Mercator tile (transparent
    outside the mosaic)."""
    from PIL import Image
    lon, lat = _tile_lonlat(z, x, y, size)
    gx, gy = frame.transformer().transform(lon, lat)
    col = np.floor((gx - frame.x0) / frame.dx).astype(np.int64)
    row = np.floor((frame.y0 - gy) / frame.dy).astype(np.int64)
    h, w = frame.classes.shape
    inside = (col >= 0) & (col < w) & (row >= 0) & (row < h)
    cls = np.zeros((size, size), dtype=np.uint8)
    cls[inside] = frame.classes[row[inside], col[inside]]
    rgba = _PALETTE[cls]
    buf = io.BytesIO()
    Image.fromarray(rgba, "RGBA").save(buf, "PNG", optimize=False)
    return buf.getvalue()


EMPTY_PNG: bytes | None = None


def empty_png() -> bytes:
    global EMPTY_PNG
    if EMPTY_PNG is None:
        from PIL import Image
        buf = io.BytesIO()
        Image.new("RGBA", (256, 256), (0, 0, 0, 0)).save(buf, "PNG")
        EMPTY_PNG = buf.getvalue()
    return EMPTY_PNG


# --- state and fetching -----------------------------------------------------------

class Radar:
    def __init__(self):
        self.frames: OrderedDict[str, Frame] = OrderedDict()
        self.watched_at = 0.0
        self.fetched_at = 0.0
        self.error: str | None = None
        self.error_at: datetime | None = None
        self._tiles: OrderedDict[tuple, bytes] = OrderedDict()

    @staticmethod
    def key() -> str:
        return os.getenv("SARCADE_METEOFRANCE_API_KEY", "").strip()

    def watch(self, now: float | None = None) -> None:
        self.watched_at = now if now is not None else time.monotonic()

    def watched(self, now: float | None = None) -> bool:
        now = now if now is not None else time.monotonic()
        return self.watched_at > 0 and now - self.watched_at < WATCH_S

    def add(self, frame: Frame) -> None:
        self.frames[frame.id] = frame
        newest = max(f.at for f in self.frames.values())
        for k in [k for k, f in self.frames.items() if newest - f.at > KEEP]:
            del self.frames[k]
        self.frames = OrderedDict(sorted(self.frames.items()))

    def status(self, now: datetime | None = None) -> dict:
        now = now or datetime.now(UTC)
        if not self.key():
            state = "no_key"
        elif self.frames:
            state = "ok"
        elif self.error:
            state = "error"
        else:
            state = "waiting"
        latest = next(reversed(self.frames.values())).at if self.frames else None
        return {"state": state, "source": SOURCE, "legend": legend(),
                "frames": [{"id": f.id, "time": f.at.isoformat().replace("+00:00", "Z")} for f in self.frames.values()],
                "stale": latest is not None and now - latest > STALE,
                "error": self.error,
                "error_at": self.error_at.isoformat().replace("+00:00", "Z") if self.error_at else None}

    def tile(self, frame_id: str, z: int, x: int, y: int) -> bytes | None:
        frame = self.frames.get(frame_id)
        if frame is None:
            return None
        if z < 3 or z > 14:
            return empty_png()
        k = (frame_id, z, x, y)
        hit = self._tiles.get(k)
        if hit is None:
            hit = tile_png(frame, z, x, y)
            self._tiles[k] = hit
            while len(self._tiles) > 600:
                self._tiles.popitem(last=False)
        else:
            self._tiles.move_to_end(k)
        return hit

    async def fetch(self, client=None) -> None:
        import httpx
        own = client is None
        client = client or httpx.AsyncClient(timeout=30)
        try:
            r = await client.get(URL, headers={"apikey": self.key(), "accept": "*/*"})
            if r.status_code != 200:
                raise RuntimeError(f"HTTP {r.status_code}")
            frame = await asyncio.to_thread(decode, r.content)
            self.add(frame)
            self.error, self.error_at = None, None
        except Exception as exc:  # noqa: BLE001 - shown in the status
            self.error, self.error_at = f"{type(exc).__name__}: {exc}"[:200], datetime.now(UTC)
            log.warning("radar fetch failed: %s", self.error)
        finally:
            self.fetched_at = time.monotonic()
            if own:
                await client.aclose()

    def due(self, now: float | None = None) -> bool:
        now = now if now is not None else time.monotonic()
        return bool(self.key()) and self.watched(now) and (self.fetched_at == 0 or now - self.fetched_at >= FETCH_EVERY_S)


radar = Radar()


async def loop() -> None:
    while True:
        try:
            if radar.due():
                await radar.fetch()
        except Exception as exc:  # noqa: BLE001
            log.warning("radar loop: %s", exc)
        await asyncio.sleep(20)


def start(tasks: list) -> None:
    if os.getenv("SARCADE_RADAR", "1") != "0":
        tasks.append(asyncio.create_task(loop()))
