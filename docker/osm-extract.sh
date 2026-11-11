#!/bin/sh
# OpenStreetMap extract for navigation: the regional file is downloaded and
# cut to the ADRASEC department plus 10 km (SARCADE_ROUTING_BBOX), so that
# Valhalla only builds tiles for that area. Refreshed at most once a week.
set -eu
OUT=/custom_files/sarcade-area.osm.pbf
if [ -f "$OUT" ] && [ -n "$(find "$OUT" -mtime -7)" ]; then
  echo "OSM extract up to date"
  exit 0
fi
apt-get update -qq && apt-get install -y -qq osmium-tool curl ca-certificates >/dev/null
curl -fsSL -o /tmp/region.osm.pbf "$SARCADE_OSM_PBF_URL"
osmium extract --overwrite -b "$SARCADE_ROUTING_BBOX" -o "$OUT" /tmp/region.osm.pbf
rm -f /tmp/region.osm.pbf
# Valhalla rebuilds its tiles when the input changes.
rm -rf /custom_files/valhalla_tiles /custom_files/valhalla_tiles.tar /custom_files/file_hashes.txt
echo "OSM extract written to $OUT"
