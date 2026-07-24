FROM python:3.13-alpine

ARG BUILD_VERSION
ARG BUILD_ARCH

LABEL \
  io.hass.version="${BUILD_VERSION}" \
  io.hass.type="app" \
  io.hass.arch="${BUILD_ARCH}"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    FLEXDISPLAY_CONFIG=/config/config.yaml \
    FLEXDISPLAY_STATE_PATH=/data/flexdisplay-state.json \
    FLEXDISPLAY_HA_BASE_URL=http://supervisor/core \
    FLEXDISPLAY_HA_VERIFY_TLS=false

RUN apk add --no-cache tzdata

WORKDIR /app
COPY pyproject.toml ./
COPY flexdisplay_bridge ./flexdisplay_bridge
COPY config.example.yaml ./config.example.yaml
COPY app_runner.py ./app_runner.py
RUN pip install --no-cache-dir .

EXPOSE 8099
CMD ["python", "/app/app_runner.py"]
