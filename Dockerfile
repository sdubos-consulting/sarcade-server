# Web client (browsers, ChromeOS PWA) served at "/". Card « GitHub de nouveau
# actif (10 novembre 2026) », choice 2: no more web-dev pre-release; by
# default (SARCADE_WEB=local) the archive sarcade-web.tar.gz placed next to
# this file, built on the i9 by sarcade-app tool/build-web.ps1 and passed by
# tool/nas/publish-registry.ps1 (docs/nas-update-from-gitlab.md). Without
# that archive the image is API only (docker/fetch_web_client.py, non-fatal),
# so the demo and end-to-end stacks build without it.
# SARCADE_WEB=none: image without web client (API only, nothing downloaded).
# SARCADE_WEB=release: archive of the latest major release of GitHub
# (tool/publish-github.ps1), or SARCADE_WEB_URL; ADD checks the URL at every
# build and keeps its cache only while the archive is unchanged.
ARG SARCADE_WEB=local
ARG SARCADE_SBOM=syft
# Files of /telechargements (Windows installer, Android APK, sources), built
# on the i9 by tool/nas/publish-registry.ps1 and placed in downloads/ next to
# this file (SARCADE_DOWNLOADS=local); none: page without files.
ARG SARCADE_DOWNLOADS=none
# Offline base maps (MBTiles) built on the i9 by tool/tiles/build-offline-
# basemaps.ps1, placed in tiles/ next to this file (SARCADE_TILES=local).
ARG SARCADE_TILES=none
FROM scratch AS web-none
FROM scratch AS web-release
ARG SARCADE_WEB_URL=https://github.com/sdubos-consulting/sarcade-app/releases/latest/download/sarcade-web.tar.gz
ADD ${SARCADE_WEB_URL} /sarcade-web.tar.gz
FROM scratch AS web-local
# COPY, not ADD: ADD would unpack a local archive. pyproject.toml is always
# there, so the pattern may match nothing: no archive, API only.
COPY pyproject.toml sarcade-web.tar.g[z] /
FROM web-${SARCADE_WEB} AS web
FROM scratch AS downloads-none
FROM scratch AS downloads-local
COPY downloads/ /downloads/
FROM downloads-${SARCADE_DOWNLOADS} AS downloads
FROM scratch AS tiles-none
FROM scratch AS tiles-local
COPY tiles/ /tiles/
FROM tiles-${SARCADE_TILES} AS tiles

# Inventory of the components (SBOM, CycloneDX) of the image actually built,
# served at /sarcade-sbom.cdx.json (docker/make_sbom.py). syft comes from its
# official image (Docker Hub, like the Python base image), is mounted only for
# that step and never stays in the image. SARCADE_SBOM=none: no SBOM
# (declared at the top with SARCADE_WEB: a FROM only sees those arguments).
FROM scratch AS sbom-tool-none
FROM anchore/syft:v1.57.0 AS sbom-tool-syft
FROM sbom-tool-${SARCADE_SBOM} AS sbom-tool

FROM python:3.15-slim

WORKDIR /app
# libexpat: needed by pyosmium (road graph of navigation on the device).
RUN apt-get update && apt-get install -y --no-install-recommends libexpat1 && rm -rf /var/lib/apt/lists/*
# Dependencies pinned by uv.lock and checked against their hashes, so that
# every build is reproducible (note « Mises à jour et sécurité »).
COPY pyproject.toml uv.lock ./
RUN pip install --no-cache-dir uv==0.11.32 \
    && uv export --frozen --no-dev --no-emit-project --format requirements-txt -o /tmp/requirements.txt \
    && pip install --no-cache-dir --require-hashes -r /tmp/requirements.txt \
    && pip uninstall -y uv && rm /tmp/requirements.txt
COPY src ./src
COPY migrations ./migrations
COPY alembic.ini .
RUN pip install --no-cache-dir --no-deps -e .
ARG SARCADE_VERSION=""
ENV SARCADE_VERSION=${SARCADE_VERSION}
# Commit and label of the reference version, read by /version (lot 28).
ARG SARCADE_COMMIT=""
ARG SARCADE_TAG=""
ENV SARCADE_COMMIT=${SARCADE_COMMIT} SARCADE_TAG=${SARCADE_TAG}

# Web client, downloaded by the "web" stage above. SARCADE_WEB_CACHEBUST:
# manual forcing of this step (any new value), normally useless.
ARG SARCADE_WEB_CACHEBUST=0
COPY docker/fetch_web_client.py /tmp/fetch_web_client.py
RUN --mount=type=bind,from=web,target=/tmp/web-src \
    echo "cachebust ${SARCADE_WEB_CACHEBUST}" \
    && python /tmp/fetch_web_client.py /tmp/web-src/sarcade-web.tar.gz /app/web \
    && rm /tmp/fetch_web_client.py
ENV SARCADE_WEB_ROOT=/app/web

# Last step, once everything is installed: the SBOM lists the Python packages,
# the system packages and the web client. A failure never stops the build
# (message in the log, file absent, the server answers 404).
COPY docker/make_sbom.py /tmp/make_sbom.py
RUN --mount=type=bind,from=sbom-tool,target=/tmp/sbom-tool \
    python /tmp/make_sbom.py /tmp/sbom-tool /app/sbom/sarcade-image.json \
    ; rm -f /tmp/make_sbom.py
ENV SARCADE_SBOM_FILE=/app/sbom/sarcade-image.json

# Offline base maps: rebuilt once a month, so this layer stays cached
# between two publications (sarcade.api.basemaps.image_tiles_root).
RUN --mount=type=bind,from=tiles,target=/tmp/tiles \
    mkdir -p /app/tiles \
    && if [ -d /tmp/tiles/tiles ]; then cp /tmp/tiles/tiles/*.mbtiles /app/tiles/ 2>/dev/null || true; fi
ENV SARCADE_TILES_IMAGE_ROOT=/app/tiles

# Very last layer: the files of /telechargements change at every
# publication, everything above stays cached (sarcade.api.downloads).
RUN --mount=type=bind,from=downloads,target=/tmp/dl \
    mkdir -p /app/downloads \
    && if [ -d /tmp/dl/downloads ]; then cp -r /tmp/dl/downloads/. /app/downloads/; fi
ENV SARCADE_DOWNLOADS_ROOT=/app/downloads

EXPOSE 8000
CMD ["sh", "-c", "alembic upgrade head && uvicorn sarcade.api.app:app --host 0.0.0.0 --port 8000"]
