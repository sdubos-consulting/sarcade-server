# Web client (browsers, ChromeOS PWA) served at "/", from the web-dev
# pre-release of sarcade-app. ADD checks the URL at every build and keeps its
# cache only while the published archive is unchanged: a new publication is
# picked up by a plain build, without --no-cache nor SARCADE_WEB_CACHEBUST.
# SARCADE_WEB=none: image without web client (API only, nothing downloaded).
ARG SARCADE_WEB=release
ARG SARCADE_SBOM=syft
FROM scratch AS web-none
FROM scratch AS web-release
ARG SARCADE_WEB_URL=https://github.com/sdubos-consulting/sarcade-app/releases/download/web-dev/sarcade-web.tar.gz
ADD ${SARCADE_WEB_URL} /sarcade-web.tar.gz
FROM web-${SARCADE_WEB} AS web

# Inventory of the components (SBOM, CycloneDX) of the image actually built,
# served at /sarcade-sbom.cdx.json (docker/make_sbom.py). syft comes from its
# official image (Docker Hub, like the Python base image), is mounted only for
# that step and never stays in the image. SARCADE_SBOM=none: no SBOM
# (declared at the top with SARCADE_WEB: a FROM only sees those arguments).
FROM scratch AS sbom-tool-none
FROM anchore/syft:v1.55.0 AS sbom-tool-syft
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

EXPOSE 8000
CMD ["sh", "-c", "alembic upgrade head && uvicorn sarcade.api.app:app --host 0.0.0.0 --port 8000"]
