# Home Assistant add-on image for the ha-realtime-voice control plane.
# Build context: this directory (app/ is synced from ../../control-plane).
# Supervisor injects BUILD_FROM from build.yaml; default is for local smoke builds.
ARG BUILD_FROM=ghcr.io/home-assistant/amd64-base-python:3.12-alpine3.21
FROM ${BUILD_FROM}

ENV LANG=C.UTF-8 \
    LISTEN_HOST=0.0.0.0 \
    LISTEN_PORT=8787 \
    DATA_DIR=/data \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1

# jq reads /data/options.json at runtime.
RUN apk add --no-cache jq \
    && pip3 install --no-cache-dir --upgrade pip

WORKDIR /usr/src/ha_realtime_voice_cp

# app/ must contain the control-plane project (pyproject.toml + src/).
# Populate with: scripts/sync-addon-app.sh
COPY app/pyproject.toml app/README.md ./
COPY app/src ./src

RUN pip3 install --no-cache-dir .

COPY run.sh /run.sh
RUN chmod a+x /run.sh

# /data is mapped by Supervisor (options.json + ha_tokens.json).
WORKDIR /data
CMD ["/run.sh"]
