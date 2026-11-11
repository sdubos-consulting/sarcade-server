import sqlite3

import pytest

from sarcade.basemaps import catalog, mbtiles


def make_mbtiles(path, tiles):
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE metadata (name text, value text)")
    con.execute("CREATE TABLE tiles (zoom_level integer, tile_column integer, tile_row integer, tile_data blob)")
    con.executemany("INSERT INTO metadata VALUES (?, ?)", [("name", "Yvelines"), ("format", "png"),
                                                           ("bounds", "1.31,48.35,2.37,49.18")])
    con.executemany("INSERT INTO tiles VALUES (?, ?, ?, ?)", tiles)
    con.commit()
    con.close()


def test_built_in_catalog_matches_the_decisions():
    ids = [b["id"] for b in catalog.BUILT_IN]
    assert ids == ["osm", "topo", "ign-plan", "ign-photos"]
    assert [b["id"] for b in catalog.BUILT_IN if b["default"]] == ["osm"]
    plan = catalog.BUILT_IN[2]["url"]
    assert plan.startswith("https://data.geopf.fr/wmts?") and "LAYER=GEOGRAPHICALGRIDSYSTEMS.PLANIGNV2" in plan
    assert "TILEMATRIX={z}&TILEROW={y}&TILECOL={x}" in plan
    assert all(b["attribution"] for b in catalog.BUILT_IN)


def test_custom_basemap_validation():
    ok = catalog.validate_custom({"id": "Prefecture-78", "name": "Carte préfecture", "attribution": "© Préfecture",
                                  "url": "https://tiles.example.org/{z}/{x}/{y}.png"})
    assert ok["id"] == "prefecture-78" and ok["custom"] is True
    assert catalog.validate_custom({"id": "pack-only", "name": "Paquet", "attribution": "© X"})["url"] is None
    for bad, reason in [({"id": "osm"}, "invalid_id"), ({"id": "a b"}, "invalid_id"),
                        ({"url": "https://x/{z}/{x}.png"}, "invalid_url"), ({"attribution": ""}, "invalid_attribution")]:
        payload = {"id": "custom-1", "name": "N", "attribution": "© A", **bad}
        with pytest.raises(catalog.InvalidBasemap, match=reason):
            catalog.validate_custom(payload)


def test_mbtiles_metadata_and_xyz_tiles(tmp_path):
    path = tmp_path / "yvelines.mbtiles"
    # zoom 1: XYZ y=0 is TMS row 1
    make_mbtiles(path, [(1, 1, 1, b"north"), (1, 1, 0, b"south"), (2, 0, 0, b"z2")])
    meta = mbtiles.metadata(path)
    assert meta["name"] == "Yvelines" and meta["min_zoom"] == 1 and meta["max_zoom"] == 2 and meta["tiles"] == 3
    assert mbtiles.tile(path, 1, 1, 0) == b"north"
    assert mbtiles.tile(path, 1, 1, 1) == b"south"
    assert mbtiles.tile(path, 1, 0, 0) is None


def test_non_mbtiles_rejected(tmp_path):
    path = tmp_path / "x.mbtiles"
    path.write_bytes(b"not a database")
    with pytest.raises(mbtiles.InvalidPackage):
        mbtiles.metadata(path)


def test_package_of_the_image_when_none_placed(tmp_path, monkeypatch):
    """Card « Cartes hors connexion sur le téléphone » (9 Nov 2026): the
    package built on the i9 and baked into the image is read when the
    administrator placed none; a placed one wins; uploads go to the volume."""
    from sarcade.api import basemaps as api
    volume, image = tmp_path / "volume", tmp_path / "image"
    volume.mkdir(); image.mkdir()
    monkeypatch.setenv("SARCADE_TILES_ROOT", str(volume))
    monkeypatch.setenv("SARCADE_TILES_IMAGE_ROOT", str(image))
    assert api.package_path("osm") == volume / "osm.mbtiles"
    make_mbtiles(image / "osm.mbtiles", [(10, 1, 2, b"image")])
    assert api.package_path("osm") == image / "osm.mbtiles"
    assert api.volume_path("osm") == volume / "osm.mbtiles"
    make_mbtiles(volume / "osm.mbtiles", [(10, 1, 2, b"volume")])
    assert api.package_path("osm") == volume / "osm.mbtiles"
    import pytest
    from fastapi import HTTPException
    with pytest.raises(HTTPException):
        api.package_path("../etc")
