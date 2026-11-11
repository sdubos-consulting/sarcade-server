from __future__ import annotations

from datetime import UTC, datetime
import hashlib
import json
import uuid
from typing import Any

REFERENCE_LAYERS = {
    "PH": "HIGH_POINT",
    # Layer renamed in the ADRASEC78 map (export of 23 Oct 2026).
    "Points hauts": "HIGH_POINT",
    "Infra 78": "RELAY",
    "Infra externe": "RELAY",
    "Relais / Transpondeurs OM": "RELAY",
}


def _iter_layers(layer: dict[str, Any]):
    yield layer
    for child in layer.get("layers") or []:
        yield from _iter_layers(child)


def _to_float(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace(",", ".")
    if not text or text == "-":
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _relay_subtype(props: dict[str, Any]) -> str:
    text = " ".join(
        str(props.get(key, ""))
        for key in ("name", "description", "callsign", "mode")
    ).lower()
    if "transpondeur" in text:
        return "transponder"
    if "rms" in text or "winlink" in text:
        return "rms"
    if "digi" in text or "digipeater" in text:
        return "digi"
    if "relais" in text or "repeater" in text:
        return "repeater"
    if any(token in text for token in ("cod", "pco", "local", "site")):
        return "site"
    return "fixed_radio"


# Former names of a layer: identifiers stay the same when a layer is renamed,
# so a new import updates the sites instead of duplicating them.
LAYER_ID_ALIASES = {"Points hauts": "PH"}


def _stable_id(source: str, layer: str, source_object_id: str) -> str:
    layer = LAYER_ID_ALIASES.get(layer, layer)
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"{source}:{layer}:{source_object_id}"))


def _source_hash(layer: str, feature: dict[str, Any]) -> str:
    canonical = {
        "layer": layer,
        "geometry": feature.get("geometry"),
        "properties": feature.get("properties") or {},
    }
    raw = json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def parse_umap_reference_sites(
    document: dict[str, Any],
    *,
    source: str = "umap:adrasec78",
) -> list[dict[str, Any]]:
    sites: list[dict[str, Any]] = []
    now = datetime.now(UTC)

    for root in document.get("layers") or []:
        for layer in _iter_layers(root):
            layer_name = (layer.get("properties") or {}).get("name")
            category = REFERENCE_LAYERS.get(layer_name)
            if category is None:
                continue

            for index, feature in enumerate(layer.get("features") or []):
                geometry = feature.get("geometry") or {}
                if geometry.get("type") != "Point":
                    continue
                coords = geometry.get("coordinates") or []
                if len(coords) < 2:
                    continue
                lon = _to_float(coords[0])
                lat = _to_float(coords[1])
                if lon is None or lat is None:
                    continue

                props = dict(feature.get("properties") or {})
                source_object_id = str(
                    feature.get("id")
                    or (props.get("_storage_options") or {}).get("id")
                    or f"index-{index}"
                )
                if category == "HIGH_POINT":
                    name = str(props.get("nom") or props.get("name") or "Point haut").strip()
                    site = {
                        "id": _stable_id(source, layer_name, source_object_id),
                        "category": category,
                        "subtype": str(props.get("type") or "Point haut").strip(),
                        "name": name,
                        # « PH 56 »: reference of the high point in the ADRASEC78 map.
                        "callsign": (str(props["reference"]).strip() or None) if props.get("reference") else None,
                        "lat": lat,
                        "lon": lon,
                        "alt_m": _to_float(props.get("altitude_m")),
                        "access": props.get("acces"),
                        "clearance": props.get("degagement"),
                        "mode": None,
                        "rx_mhz": None,
                        "tx_mhz": None,
                        "ctcss_rx": None,
                        "ctcss_tx": None,
                        "offset": None,
                        "description": props.get("commentaire") or props.get("description"),
                        "verified_at": props.get("date_verif"),
                    }
                else:
                    name = str(
                        props.get("name") or props.get("nom")
                        or props.get("callsign") or "Moyen radio"
                    ).strip()
                    ctcss_common = props.get("ctss") or props.get("ctcss")
                    site = {
                        "id": _stable_id(source, layer_name, source_object_id),
                        "category": category,
                        "subtype": _relay_subtype(props),
                        "name": name,
                        "callsign": props.get("callsign"),
                        "lat": lat,
                        "lon": lon,
                        "alt_m": _to_float(props.get("altitude_m")),
                        "access": props.get("acces"),
                        "clearance": props.get("degagement"),
                        "mode": props.get("mode"),
                        # Simplex (one frequency only, e.g. Digi-Vara F5ZYI-7):
                        # the same frequency in and out (decision of 23 Oct 2026).
                        "rx_mhz": _to_float(props.get("qrg_entree")) or _to_float(props.get("qrg_sortie")),
                        "tx_mhz": _to_float(props.get("qrg_sortie")) or _to_float(props.get("qrg_entree")),
                        "ctcss_rx": props.get("ctcss_entree") or ctcss_common,
                        "ctcss_tx": props.get("ctcss_sortie") or ctcss_common,
                        "offset": props.get("offset"),
                        "description": props.get("description") or props.get("commentaire"),
                        "verified_at": props.get("date_verif") or props.get("date_dernier_acces"),
                    }

                site.update({
                    "source": source,
                    "source_layer": layer_name,
                    "source_object_id": source_object_id,
                    "source_hash": _source_hash(layer_name, feature),
                    "source_properties": props,
                    "status": "active",
                    "imported_at": now,
                })
                sites.append(site)
    return sites
