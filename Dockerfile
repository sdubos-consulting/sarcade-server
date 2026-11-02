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

# Web client (browsers, ChromeOS PWA) served at "/". Empty URL = API only.
# Change SARCADE_WEB_CACHEBUST to fetch a newer client without --no-cache.
ARG SARCADE_WEB_URL=https://github.com/sdubos-consulting/sarcade-app/releases/download/web-dev/sarcade-web.tar.gz
ARG SARCADE_WEB_CACHEBUST=0
COPY docker/fetch_web_client.py /tmp/fetch_web_client.py
RUN echo "cachebust ${SARCADE_WEB_CACHEBUST}" && python /tmp/fetch_web_client.py "$SARCADE_WEB_URL" /app/web && rm /tmp/fetch_web_client.py
ENV SARCADE_WEB_ROOT=/app/web

EXPOSE 8000
CMD ["sh", "-c", "alembic upgrade head && uvicorn sarcade.api.app:app --host 0.0.0.0 --port 8000"]
