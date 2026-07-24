from __future__ import annotations

import hashlib
import re
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Request, Response

from . import __version__
from .config import BridgeConfig, DeviceConfig, load_config
from .home_assistant import HomeAssistantClient
from .mqtt_service import MqttService
from .renderer import DashboardRenderer
from .store import DeviceStore

DEVICE_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{2,63}$")


def _integer(value: str | None, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value) if value is not None else default
    except ValueError:
        parsed = default
    return max(minimum, min(maximum, parsed))


def _number(value: str | None) -> float | None:
    try:
        return float(value) if value not in (None, "") else None
    except ValueError:
        return None


def _device_id(value: str | None) -> str:
    selected = value or "UNKNOWN"
    if not DEVICE_ID_PATTERN.fullmatch(selected):
        raise HTTPException(status_code=400, detail="Invalid X-FlexDisplay-ID")
    return selected


def create_app(config: BridgeConfig | None = None) -> FastAPI:
    settings = config or load_config()
    store = DeviceStore(settings.state_path)
    ha = HomeAssistantClient(settings.home_assistant)
    renderer = DashboardRenderer()

    def queue_from_mqtt(device_id: str, command: str) -> None:
        if DEVICE_ID_PATTERN.fullmatch(device_id) and command in {"refresh", "next"}:
            store.queue_command(device_id, command)

    mqtt = MqttService(settings.mqtt, queue_from_mqtt)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        del app
        mqtt.start()
        yield
        mqtt.stop()

    app = FastAPI(title="FlexDisplay Home Assistant Bridge", version=__version__, lifespan=lifespan)
    app.state.config = settings
    app.state.store = store
    app.state.mqtt = mqtt

    @app.get("/healthz")
    def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "version": __version__,
            "home_assistant_configured": ha.configured,
            "mqtt_enabled": settings.mqtt.enabled,
            "mqtt_connected": mqtt.connected,
        }

    @app.get("/api/v1/devices")
    def devices() -> dict[str, Any]:
        return {"devices": store.all()}

    @app.get("/api/v1/devices/{device_id}")
    def device(device_id: str) -> dict[str, Any]:
        selected = _device_id(device_id)
        record = store.get(selected)
        if not record:
            raise HTTPException(status_code=404, detail="Device not found")
        return record

    def authorize(request: Request) -> None:
        if settings.api_key and request.headers.get("X-FlexDisplay-Bridge-Key") != settings.api_key:
            raise HTTPException(status_code=401, detail="Bridge API key required")

    @app.post("/api/v1/devices/{device_id}/commands/{command}")
    def command(device_id: str, command: str, request: Request) -> dict[str, Any]:
        authorize(request)
        selected = _device_id(device_id)
        if command not in {"refresh", "next"}:
            raise HTTPException(status_code=400, detail="Unsupported command")
        record = store.queue_command(selected, command)
        return {"queued": command, "device": record}

    @app.get("/api/v1/screen")
    def screen(
        response: Response,
        x_flexdisplay_id: str | None = Header(default=None),
        x_flexdisplay_width: str | None = Header(default=None),
        x_flexdisplay_height: str | None = Header(default=None),
        x_flexdisplay_model: str | None = Header(default=None),
        x_flexdisplay_firmware: str | None = Header(default=None),
        x_flexdisplay_battery_percent: str | None = Header(default=None),
        x_flexdisplay_battery_voltage: str | None = Header(default=None),
        x_flexdisplay_rssi: str | None = Header(default=None),
        x_flexdisplay_mode: str | None = Header(default=None),
    ):
        device_id = _device_id(x_flexdisplay_id)
        width = _integer(x_flexdisplay_width, 480, 240, 1200)
        height = _integer(x_flexdisplay_height, 800, 240, 1600)
        model = x_flexdisplay_model or ("X4" if device_id.startswith("X4-") else "X3")
        telemetry = {
            "model": model,
            "width": width,
            "height": height,
            "firmware": x_flexdisplay_firmware or "0.4.0",
            "battery_percent": _number(x_flexdisplay_battery_percent),
            "battery_voltage": _number(x_flexdisplay_battery_voltage),
            "rssi": _number(x_flexdisplay_rssi),
            "mode": x_flexdisplay_mode or "home_assistant",
        }
        record = store.touch(device_id, telemetry)
        profile: DeviceConfig = settings.device(device_id, width, height, model)
        commands = store.consume_commands(device_id)
        if commands:
            record = store.get(device_id) or record
        entity_states, ha_error = ha.fetch(profile.entities)
        image = renderer.render(
            title=settings.title,
            device={**record, "name": profile.name},
            width=width,
            height=height,
            entities=entity_states,
            ha_error=ha_error,
        )
        digest = hashlib.sha256(image).hexdigest()
        state = {
            **record,
            "name": profile.name,
            "last_image_sha256": digest,
            "refresh_interval_seconds": profile.refresh_interval_seconds,
        }
        mqtt.publish_device(device_id, profile, state)
        response.headers["ETag"] = f'"{digest}"'
        response.headers["X-FlexDisplay-Refresh-Interval"] = str(profile.refresh_interval_seconds)
        response.headers["X-FlexDisplay-Commands"] = ",".join(commands)
        return Response(content=image, media_type="image/png", headers=dict(response.headers))

    return app


app = create_app()
