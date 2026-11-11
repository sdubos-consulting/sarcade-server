# Base maps — v0.1

Spec: « Choix du fond de carte » (Obsidian vault). Core feature.

## Catalog

`GET /api/v0.1/basemaps` returns the built-in layers then the custom ones:

| id | Layer | Source |
|---|---|---|
| `osm` (default) | OpenStreetMap | tile.openstreetmap.org |
| `topo` | Topographic | OpenTopoMap |
| `ign-plan` | Plan IGN v2 | Géoplateforme WMTS `GEOGRAPHICALGRIDSYSTEMS.PLANIGNV2`, PM |
| `ign-photos` | IGN aerial photos | Géoplateforme WMTS `ORTHOIMAGERY.ORTHOPHOTOS`, PM |

Each entry carries `url` (XYZ template), `attribution`, `max_zoom`, `default`,
`offline_package` (metadata of the MBTiles package, if any) and
`available_offline`.

## Custom base maps (administration)

- `POST /basemaps` `{id, name, url, attribution, max_zoom, licence, usage}`:
  XYZ template with `{z}`, `{x}`, `{y}`, or no URL at all for a layer that
  only exists as an offline package.
- `DELETE /basemaps/{id}`: removes the layer and its package.

## Offline packages (MBTiles)

- `PUT /basemaps/{id}/package` (body: the `.mbtiles` file, limit
  `SARCADE_MAX_TILE_PACKAGE_BYTES`, 4 GiB by default) stored under
  `SARCADE_TILES_ROOT` (`/var/lib/sarcade/tiles`, volume `sarcade-tiles`).
- `GET /basemaps/{id}/package`: download for clients that keep the map on the
  device.
- `GET /basemaps/{id}/tiles/{z}/{x}/{y}`: tiles served by the Gateway on the
  local network when the Internet is down (TMS row flipped).

## Event default

The PCO sets the base map of an event with `PATCH /events/{id}/settings`
`{"basemap": "ign-plan"}`; each operator can still pick another one locally.

## To check

- IGN Géoplateforme usage conditions for a large number of clients.
- OpenTopoMap usage policy (fair use, no heavy prefetching).
