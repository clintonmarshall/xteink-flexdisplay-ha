from __future__ import annotations

import hashlib
import re
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Request, Response

from . import __version__
from .config import BridgeConfig, DeviceConfig, load_config
from .dashboards import build_dashboard_pages
from .home_assistant import HomeAssistantClient
from .mqtt_service import MqttService
from .renderer import DashboardRenderer
from .store import DeviceStore

DEVICE_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{2,63}$")
SUPPORTED_COMMANDS = {"refresh", "next", "previous", "overview", "restart", "install"}
SUPPORTED_BUTTONS = {"back", "confirm", "left", "right", "up", "down", "power"}
SUPPORTED_MODES = {"reader", "home_assistant", "trmnl", "opendisplay", "photo_frame"}


def _firmware_version(value: str) -> tuple[int, int, int]:
    match = re.search(r"flexdisplay[.-](\d+)\.(\d+)\.(\d+)", value)
    if not match:
        match = re.search(r"(\d+)\.(\d+)\.(\d+)", value)
    return tuple(int(part) for part in match.groups()) if match else (0, 0, 0)


def _decorate_device(record: dict[str, Any], settings: BridgeConfig) -> dict[str, Any]:
    result = dict(record)
    last_seen = result.get("last_seen")
    online = False
    power_state = "offline"
    if isinstance(last_seen, str):
        try:
            seen = datetime.fromisoformat(last_seen)
            age = (datetime.now(UTC) - seen).total_seconds()
            profile = _effective_device(
                settings.device(
                    str(result.get("device_id") or ""),
                    int(result.get("width") or 480),
                    int(result.get("height") or 800),
                    str(result.get("model") or ""),
                ),
                result,
            )
            online_window = max(180, int(profile.refresh_interval_seconds * 1.5) + 60)
            online = age <= online_window
            power_state = "awake" if age <= 90 else ("sleeping" if online else "offline")
        except ValueError:
            pass
    result["online"] = online
    result["power_state"] = power_state
    result["latest_firmware"] = settings.firmware.version or result.get("firmware", "")
    profile = _effective_device(
        settings.device(
            str(result.get("device_id") or ""),
            int(result.get("width") or 480),
            int(result.get("height") or 800),
            str(result.get("model") or ""),
        ),
        result,
    )
    result["name"] = profile.name
    result["area"] = profile.area
    result["assigned_profile"] = profile.profile
    result["assigned_mode"] = profile.mode
    result["assigned_auto_start"] = profile.auto_start
    result["assigned_refresh_interval_seconds"] = profile.refresh_interval_seconds
    result["available_profiles"] = list(settings.profiles)
    result["available_modes"] = sorted(SUPPORTED_MODES)
    result["update_available"] = bool(
        settings.firmware.version
        and settings.firmware.url
        and _firmware_version(settings.firmware.version) > _firmware_version(str(result.get("firmware") or ""))
    )
    return result


def _effective_device(base: DeviceConfig, record: dict[str, Any]) -> DeviceConfig:
    return replace(
        base,
        name=str(record.get("assigned_name") or base.name),
        area=str(record.get("assigned_area") or base.area),
        profile=str(record.get("assigned_profile") or base.profile),
        mode=str(record.get("assigned_mode") or base.mode),
        auto_start=bool(record.get("assigned_auto_start", base.auto_start)),
        refresh_interval_seconds=_integer(
            str(record.get("assigned_refresh_interval_seconds"))
            if record.get("assigned_refresh_interval_seconds") is not None
            else None,
            base.refresh_interval_seconds,
            60,
            86400,
        ),
    )


def _header_value(value: Any) -> str:
    return str(value or "").replace("\r", " ").replace("\n", " ")[:160]


def _integer(value: str | None, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value) if value is not None else default
    except ValueError:
        parsed = default
    return max(minimum, min(maximum, parsed))


def _optional_integer(value: str | None, minimum: int, maximum: int) -> int | None:
    if value in (None, ""):
        return None
    try:
        return max(minimum, min(maximum, int(value)))
    except ValueError:
        return None


def _boolean(value: str | None) -> bool | None:
    if value is None:
        return None
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _button_events(value: str | None) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for encoded in (value or "").split(";"):
        parts = encoded.split(",")
        if len(parts) != 4 or parts[1] not in SUPPORTED_BUTTONS or parts[2] != "pressed":
            continue
        try:
            sequence = max(0, int(parts[0]))
            uptime_ms = max(0, int(parts[3]))
        except ValueError:
            continue
        result.append(
            {
                "sequence": sequence,
                "button": parts[1],
                "action": parts[2],
                "uptime_ms": uptime_ms,
            }
        )
    return result[:16]


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


def _valid_command(command: str) -> bool:
    return command in SUPPORTED_COMMANDS or bool(re.fullmatch(r"page-[1-9][0-9]?", command))


def _auto_rotate_due(record: dict[str, Any], seconds: int) -> bool:
    if seconds <= 0:
        return False
    changed_at = record.get("dashboard_page_changed_at")
    if not isinstance(changed_at, str):
        return False
    try:
        return (datetime.now(UTC) - datetime.fromisoformat(changed_at)).total_seconds() >= seconds
    except ValueError:
        return False


def create_app(config: BridgeConfig | None = None) -> FastAPI:
    settings = config or load_config()
    store = DeviceStore(settings.state_path)
    ha = HomeAssistantClient(settings.home_assistant)
    renderer = DashboardRenderer()

    def queue_from_mqtt(device_id: str, command: str) -> None:
        if DEVICE_ID_PATTERN.fullmatch(device_id) and command in SUPPORTED_COMMANDS:
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
        return {"devices": [_decorate_device(record, settings) for record in store.all()]}

    @app.get("/api/v1/devices/{device_id}")
    def device(device_id: str) -> dict[str, Any]:
        selected = _device_id(device_id)
        record = store.get(selected)
        if not record:
            raise HTTPException(status_code=404, detail="Device not found")
        return _decorate_device(record, settings)

    @app.get("/api/v1/devices/{device_id}/events")
    def device_events(device_id: str) -> dict[str, Any]:
        selected = _device_id(device_id)
        record = store.get(selected)
        if not record:
            raise HTTPException(status_code=404, detail="Device not found")
        return {"events": record.get("recent_button_events", [])}

    def authorize(request: Request) -> None:
        if settings.api_key and request.headers.get("X-FlexDisplay-Bridge-Key") != settings.api_key:
            raise HTTPException(status_code=401, detail="Bridge API key required")

    @app.post("/api/v1/devices/{device_id}/commands/{command}")
    def command(device_id: str, command: str, request: Request) -> dict[str, Any]:
        authorize(request)
        selected = _device_id(device_id)
        if not _valid_command(command):
            raise HTTPException(status_code=400, detail="Unsupported command")
        if command == "install" and (not settings.firmware.version or not settings.firmware.url):
            raise HTTPException(status_code=409, detail="No firmware release is configured")
        record = store.queue_command(selected, command)
        return {"queued": command, "device": record}

    @app.put("/api/v1/devices/{device_id}/provision")
    def provision_device(device_id: str, payload: dict[str, Any], request: Request) -> dict[str, Any]:
        authorize(request)
        selected = _device_id(device_id)
        assignment: dict[str, Any] = {}
        if "name" in payload:
            assignment["assigned_name"] = _header_value(payload["name"]) or selected
        if "area" in payload:
            assignment["assigned_area"] = _header_value(payload["area"])
        if "profile" in payload:
            profile = str(payload["profile"])
            if profile not in settings.profiles:
                raise HTTPException(status_code=400, detail="Unknown dashboard profile")
            assignment["assigned_profile"] = profile
        if "mode" in payload:
            mode = str(payload["mode"])
            if mode not in SUPPORTED_MODES:
                raise HTTPException(status_code=400, detail="Unsupported device mode")
            assignment["assigned_mode"] = mode
        if "auto_start" in payload:
            assignment["assigned_auto_start"] = bool(payload["auto_start"])
        if "refresh_interval_seconds" in payload:
            assignment["assigned_refresh_interval_seconds"] = _integer(
                str(payload["refresh_interval_seconds"]),
                900,
                60,
                86400,
            )
        if not assignment:
            raise HTTPException(status_code=400, detail="No provisioning fields supplied")
        record = store.provision(selected, assignment)
        store.queue_command(selected, "refresh")
        return {"device": _decorate_device(record, settings)}

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
        x_flexdisplay_command_result: str | None = Header(default=None),
        x_flexdisplay_usb_connected: str | None = Header(default=None),
        x_flexdisplay_uptime_seconds: str | None = Header(default=None),
        x_flexdisplay_free_heap: str | None = Header(default=None),
        x_flexdisplay_min_free_heap: str | None = Header(default=None),
        x_flexdisplay_sd_ready: str | None = Header(default=None),
        x_flexdisplay_wake_reason: str | None = Header(default=None),
        x_flexdisplay_button_events: str | None = Header(default=None),
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
            "usb_connected": _boolean(x_flexdisplay_usb_connected),
            "uptime_seconds": _optional_integer(x_flexdisplay_uptime_seconds, 0, 31_536_000),
            "free_heap": _optional_integer(x_flexdisplay_free_heap, 0, 1_000_000),
            "min_free_heap": _optional_integer(x_flexdisplay_min_free_heap, 0, 1_000_000),
            "sd_ready": _boolean(x_flexdisplay_sd_ready),
            "wake_reason": x_flexdisplay_wake_reason or None,
        }
        record = store.touch(device_id, telemetry)
        configured = settings.device(device_id, width, height, model)
        if settings.provisioning.enabled:
            record = store.ensure_provisioning(
                device_id,
                {
                    "assigned_name": configured.name,
                    "assigned_area": configured.area,
                    "assigned_profile": configured.profile,
                    "assigned_mode": configured.mode,
                    "assigned_auto_start": configured.auto_start,
                    "assigned_refresh_interval_seconds": configured.refresh_interval_seconds,
                },
            )
        record = store.record_button_events(device_id, _button_events(x_flexdisplay_button_events)) or record
        store.acknowledge(device_id, x_flexdisplay_command_result or "")
        profile: DeviceConfig = _effective_device(configured, record)
        commands = store.consume_commands(device_id)
        if commands:
            record = store.get(device_id) or record
        entity_states, ha_error = ha.fetch(profile.entities)
        dashboard_profile = settings.profile(profile)
        pages = build_dashboard_pages(
            entity_states,
            record,
            dashboard_profile.pages if dashboard_profile else (),
        )
        page_index = int(record.get("dashboard_page_index") or 0) % len(pages)
        if "next" in commands:
            page_index = (page_index + 1) % len(pages)
        elif "previous" in commands:
            page_index = (page_index - 1) % len(pages)
        elif "overview" in commands:
            page_index = 0
        else:
            requested_page = next(
                (command for command in commands if command.startswith("page-")),
                None,
            )
            if requested_page:
                page_index = (int(requested_page.removeprefix("page-")) - 1) % len(pages)
            elif dashboard_profile and _auto_rotate_due(record, dashboard_profile.auto_rotate_seconds):
                page_index = (page_index + 1) % len(pages)
        page = pages[page_index]
        record = store.set_dashboard_page(
            device_id,
            page_index,
            len(pages),
            page.title,
            [candidate.title for candidate in pages],
            profile.profile,
        ) or record
        image = renderer.render(
            title=page.title,
            device={**record, "name": profile.name},
            width=width,
            height=height,
            entities=page.entities,
            page_index=page_index,
            page_count=len(pages),
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
        if settings.provisioning.enabled:
            response.headers["X-FlexDisplay-Provisioned"] = "true"
            response.headers["X-FlexDisplay-Device-Name"] = _header_value(profile.name)
            response.headers["X-FlexDisplay-Area"] = _header_value(profile.area)
            response.headers["X-FlexDisplay-Profile"] = _header_value(profile.profile)
            response.headers["X-FlexDisplay-Assigned-Mode"] = _header_value(profile.mode)
            response.headers["X-FlexDisplay-Auto-Start"] = "true" if profile.auto_start else "false"
        response.headers["X-FlexDisplay-Commands"] = ",".join(commands)
        response.headers["X-FlexDisplay-Page"] = str(page_index + 1)
        response.headers["X-FlexDisplay-Page-Count"] = str(len(pages))
        response.headers["X-FlexDisplay-Page-Title"] = page.title
        if settings.firmware.version:
            response.headers["X-FlexDisplay-Latest-Firmware"] = settings.firmware.version
        if "install" in commands:
            response.headers["X-FlexDisplay-Firmware-URL"] = settings.firmware.url
            response.headers["X-FlexDisplay-Firmware-SHA256"] = settings.firmware.sha256
            response.headers["X-FlexDisplay-Firmware-Size"] = str(settings.firmware.size)
            response.headers["X-FlexDisplay-Firmware-Min-Battery"] = str(
                settings.firmware.minimum_battery_percent
            )
        return Response(content=image, media_type="image/png", headers=dict(response.headers))

    return app


app = create_app()
