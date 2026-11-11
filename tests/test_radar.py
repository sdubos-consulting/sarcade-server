# SPDX-License-Identifier: AGPL-3.0-only
# © SDUBOS Consulting et contributeurs SARCADE
"""Rain radar of Météo-France as map tiles (lot 29)."""
import gzip
import io
import math
from datetime import UTC, datetime
from pathlib import Path

import httpx
import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from sarcade.api.app import app
from sarcade.weather import hdf5lite
from sarcade.weather import radar as radar_mod
from sarcade.weather.calls import calls


@pytest.fixture(autouse=True)
def _no_pause():
    """A 403 of a test pauses the radar 6 hours (card « Météo : limiter les
    appels », 10 Nov 2026): every test starts without pause."""
    calls.reset()
    yield
    calls.reset()


MOSAIC = (Path(__file__).parent / "data" / "radar_mosaic.h5").read_bytes()


def _tile_of(lon, lat, z):
    n = 2 ** z
    x = int((lon + 180) / 360 * n)
    y = int((1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2 * n)
    return x, y


def test_small_hdf5_reader_reads_the_odim_mosaic():
    f = hdf5lite.read(MOSAIC)
    assert f.root["what"].attrs["object"] == "COMP"
    assert f.root["dataset1/data1/what"].attrs["quantity"] == "ACRR"
    a = f.root["dataset1/data1/data"].read()
    assert a.shape == (300, 300) and a[150, 150] == 100 and a[0, 0] == 65535


def test_decode_gives_rain_classes_time_and_grid():
    fr = radar_mod.decode(gzip.compress(MOSAIC), factor=1)
    assert fr.at == datetime(2026, 11, 6, 1, 15, tzinfo=UTC)
    assert fr.id == "202611060115"
    # 1 mm in 5 min = 12 mm/h: class of 8 to 16 mm/h; 0.12 mm/h: lightest.
    assert fr.classes[150, 150] == 5 and fr.classes[205, 205] == 1
    assert fr.classes[0, 0] == 0 and fr.classes[50, 50] == 0
    assert fr.dx == 1000.0


def test_reduced_grid_keeps_the_strongest_cell():
    fr = radar_mod.decode(MOSAIC, factor=2)
    assert fr.classes.shape == (150, 150) and fr.classes[75, 75] == 5 and fr.dx == 2000.0


def test_tile_over_paris_shows_the_rain_and_is_empty_elsewhere():
    fr = radar_mod.decode(MOSAIC, factor=1)
    x, y = _tile_of(2.35, 48.85, 9)
    img = np.array(Image.open(io.BytesIO(radar_mod.tile_png(fr, 9, x, y))))
    assert img.shape == (256, 256, 4)
    assert (img[..., 3] > 0).any()
    assert tuple(img[img[..., 3] > 200][0][:3]) == radar_mod.CLASSES[4][1][:3]
    far = np.array(Image.open(io.BytesIO(radar_mod.tile_png(fr, 9, *_tile_of(-60.0, 15.0, 9)))))
    assert not (far[..., 3] > 0).any()


def test_frames_kept_one_hour_and_status(monkeypatch):
    monkeypatch.setenv("SARCADE_METEOFRANCE_API_KEY", "cle")
    r = radar_mod.Radar()
    for hour, minute in ((1, 15), (1, 30), (2, 30)):
        fr = radar_mod.decode(MOSAIC)
        fr.at = datetime(2026, 11, 6, hour, minute, tzinfo=UTC)
        r.add(fr)
    ids = list(r.frames)
    assert ids == sorted(ids) and len(ids) == 2
    st = r.status(now=datetime(2026, 11, 6, 2, 35, tzinfo=UTC))
    assert st["state"] == "ok" and st["source"] == "Météo-France" and not st["stale"]
    assert st["legend"][0] == {"min": 0.1, "max": 1.0, "color": "#9BD7FF"}


def test_nothing_fetched_without_key_or_without_viewer(monkeypatch):
    monkeypatch.delenv("SARCADE_METEOFRANCE_API_KEY", raising=False)
    r = radar_mod.Radar()
    r.watch(1000.0)
    assert not r.due(1001.0) and r.status()["state"] == "no_key"
    monkeypatch.setenv("SARCADE_METEOFRANCE_API_KEY", "cle")
    assert r.due(1001.0)
    assert not r.due(1000.0 + radar_mod.WATCH_S + 1)


@pytest.mark.anyio
async def test_fetch_with_the_key_of_the_server(monkeypatch):
    monkeypatch.setenv("SARCADE_METEOFRANCE_API_KEY", "cle-nas")
    seen = {}

    def handler(request):
        seen["key"] = request.headers.get("apikey")
        return httpx.Response(200, content=gzip.compress(MOSAIC))
    r = radar_mod.Radar()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        await r.fetch(client)
    assert seen["key"] == "cle-nas" and list(r.frames) == ["202611060115"] and r.error is None

    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda req: httpx.Response(403))) as client:
        await r.fetch(client)
    assert r.error == "RuntimeError: HTTP 403" and r.status()["state"] == "ok"


def test_api_status_and_tiles(monkeypatch):
    monkeypatch.setenv("SARCADE_METEOFRANCE_API_KEY", "cle")
    fresh = radar_mod.Radar()
    monkeypatch.setattr("sarcade.api.radar.radar", fresh)
    client = TestClient(app)
    assert client.get("/api/v0.1/weather/radar").json()["state"] == "waiting"
    assert fresh.watched()
    fresh.add(radar_mod.decode(MOSAIC))
    st = client.get("/api/v0.1/weather/radar").json()
    fid = st["frames"][0]["id"]
    x, y = _tile_of(2.35, 48.85, 8)
    r = client.get(f"/api/v0.1/weather/radar/{fid}/8/{x}/{y}.png")
    assert r.status_code == 200 and r.headers["content-type"] == "image/png"
    assert "immutable" in r.headers["cache-control"]
    assert client.get(f"/api/v0.1/weather/radar/000000000000/8/{x}/{y}.png").status_code == 404
    assert client.get(f"/api/v0.1/weather/radar/{fid}/2/9/9.png").status_code == 404


@pytest.mark.parametrize("libver", ["earliest", "latest"])
def test_small_hdf5_reader_on_both_file_versions(libver):
    f = hdf5lite.read((Path(__file__).parent / "data" / f"hdf5lite_{libver}.h5").read_bytes())
    a = np.arange(30 * 25, dtype="<i4").reshape(30, 25) - 100
    assert f.root.attrs["texte"] == "variable é" and f.root.attrs["fixe"] == "fixe"
    assert list(f.root.attrs["vecteur"]) == [1.5, 2.5]
    assert (f.root["gz"].read() == a).all()
    assert (f.root["shuf"].read() == a.astype(">f8")).all()
    assert (f.root["contig"].read() == a.astype("u1")).all()
    # Many groups (several symbol nodes) in the old format; compact links in the new.
    n = 40 if libver == "earliest" else 6
    assert len(f.root["groupes"].children) == n and f.root[f"groupes/g{n - 3:02d}"].attrs["i"] == n - 3
    with pytest.raises(hdf5lite.Unsupported):
        hdf5lite.read(b"pas un fichier HDF5" * 100)
