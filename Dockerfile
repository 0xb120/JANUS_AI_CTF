# JANUS backend + kiosk frontend. Model weights are never baked into the image:
# the compose stack provisions them into named volumes (see docker-compose.yml).

FROM python:3.12-slim AS build

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /build
COPY pyproject.toml ./
COPY src ./src
RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install ".[speech]"


FROM python:3.12-slim

ENV PATH=/opt/venv/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOME=/tmp \
    HF_HUB_DISABLE_TELEMETRY=1

RUN groupadd --system --gid 10001 janus \
    && useradd --system --uid 10001 --gid janus --home-dir /app --shell /usr/sbin/nologin janus \
    && mkdir -p /data /models \
    && chown janus:janus /data /models

COPY --from=build /opt/venv /opt/venv
WORKDIR /app
COPY configs ./configs
COPY scripts/prepare_speech_model.py ./scripts/
COPY docker/entrypoint.sh docker/prepare-models.sh ./docker/
RUN chmod 0755 ./docker/*.sh

USER janus
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=20s --start-period=90s --retries=3 \
    CMD ["python", "-c", "import json, sys, urllib.request; r = urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=15); sys.exit(0 if json.load(r)['status'] == 'ok' else 1)"]

ENTRYPOINT ["/app/docker/entrypoint.sh"]
