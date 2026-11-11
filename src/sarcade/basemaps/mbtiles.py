"""Offline tile packages (MBTiles, SQLite) served by the server on the
local network: clients download them before leaving for the field."""
from __future__ import annotations

from pathlib import Path
import sqlite3


class InvalidPackage(ValueError):
    pass


def metadata(path: Path) -> dict:
    """Name, bounds, zooms and tile count of a package; rejects files that
    are not MBTiles."""
    try:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    except sqlite3.Error as exc:
        raise InvalidPackage("not_mbtiles") from exc
    try:
        meta = dict(con.execute("SELECT name, value FROM metadata").fetchall())
        zooms = con.execute("SELECT MIN(zoom_level), MAX(zoom_level), COUNT(*) FROM tiles").fetchone()
    except sqlite3.Error as exc:
        raise InvalidPackage("not_mbtiles") from exc
    finally:
        con.close()
    return {"name": meta.get("name"), "format": meta.get("format", "png"), "bounds": meta.get("bounds"),
            "min_zoom": zooms[0], "max_zoom": zooms[1], "tiles": zooms[2], "size_bytes": path.stat().st_size}


def tile(path: Path, z: int, x: int, y: int) -> bytes | None:
    """XYZ tile (MBTiles rows are TMS: y flipped)."""
    tms_y = (1 << z) - 1 - y
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        row = con.execute("SELECT tile_data FROM tiles WHERE zoom_level=? AND tile_column=? AND tile_row=?",
                          (z, x, tms_y)).fetchone()
    finally:
        con.close()
    return row[0] if row else None
