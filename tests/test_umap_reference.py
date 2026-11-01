from sarcade.modules.adrasec.reference.umap import parse_umap_reference_sites


def test_parses_two_reference_categories():
    document = {
        "layers": [
            {
                "properties": {"name": "Points hauts"},
                "layers": [{
                    "properties": {"name": "PH"},
                    "features": [{
                        "id": "ph1",
                        "geometry": {"type": "Point", "coordinates": [1.8, 48.7]},
                        "properties": {"nom": "Point A", "altitude_m": "180,5", "type": "Sommet"},
                    }],
                }],
            },
            {
                "properties": {"name": "Infra"},
                "layers": [{
                    "properties": {"name": "Infra 78"},
                    "features": [{
                        "id": "r1",
                        "geometry": {"type": "Point", "coordinates": [2.0, 48.8]},
                        "properties": {
                            "name": "Transpondeur Test",
                            "qrg_entree": "439.350",
                            "qrg_sortie": "145.475",
                            "ctcss_entree": "85.4",
                            "mode": "FM",
                        },
                    }],
                }],
            },
        ]
    }

    sites = parse_umap_reference_sites(document)
    assert len(sites) == 2

    high = next(s for s in sites if s["category"] == "HIGH_POINT")
    assert high["name"] == "Point A"
    assert high["alt_m"] == 180.5

    relay = next(s for s in sites if s["category"] == "RELAY")
    assert relay["subtype"] == "transponder"
    assert relay["rx_mhz"] == 439.35
    assert relay["tx_mhz"] == 145.475


def test_ignores_linestrings_in_reference_layers():
    document = {
        "layers": [{
            "properties": {"name": "Infra"},
            "layers": [{
                "properties": {"name": "Infra 78"},
                "features": [{
                    "id": "line1",
                    "geometry": {"type": "LineString", "coordinates": [[1.0, 48.0], [2.0, 49.0]]},
                    "properties": {"name": "Liaison"},
                }],
            }],
        }]
    }
    assert parse_umap_reference_sites(document) == []


def test_renamed_points_hauts_layer_keeps_identifiers():
    """Export of 23 Oct 2026: the « PH » layer is now named « Points hauts »
    (nested in a parent of the same name) and carries a « reference »."""
    feature = {
        "id": "ph56",
        "geometry": {"type": "Point", "coordinates": [1.9, 48.75]},
        "properties": {"reference": "PH 56", "nom": "Butte à l'Âne", "altitude_m": 186,
                       "date_verif": "2026-10-04", "degagement": "", "acces": ""},
    }

    def doc(name):
        return {"layers": [{"properties": {"name": "Points hauts"},
                            "layers": [{"properties": {"name": name}, "features": [feature]}]}]}

    new = parse_umap_reference_sites(doc("Points hauts"))
    old = parse_umap_reference_sites(doc("PH"))
    assert len(new) == 1 and new[0]["category"] == "HIGH_POINT"
    assert new[0]["callsign"] == "PH 56" and new[0]["alt_m"] == 186
    assert new[0]["id"] == old[0]["id"]


def test_simplex_frequency_used_in_and_out():
    document = {"layers": [{"properties": {"name": "Infra externe"}, "features": [{
        "id": "vara", "geometry": {"type": "Point", "coordinates": [2.6, 48.5]},
        "properties": {"name": "Digi-Vara F5ZYI-7", "qrg_entree": "145.275", "mode": "VARA FM"}}]}]}
    relay = parse_umap_reference_sites(document)[0]
    assert relay["rx_mhz"] == relay["tx_mhz"] == 145.275
