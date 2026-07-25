from __future__ import annotations

import hashlib
import re
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import UTC, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import FastAPI, Header, HTTPException, Request, Response

from . import __version__
from .config import BridgeConfig, DeviceConfig, load_config
from .dashboards import build_dashboard_pages
from .home_assistant import HomeAssistantClient
from .mqtt_service import MqttService
from .renderer import DashboardRenderer
from .store import DeviceStore

DEVICE_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{2,63}$")
SUPPORTED_COMMANDS = {
    "refresh",
    "full-refresh",
    "next",
    "previous",
    "overview",
    "clear",
    "sleep",
    "power-off",
    "restart",
    "install",
}
SUPPORTED_BUTTONS = {"back", "confirm", "left", "right", "up", "down", "power"}
SUPPORTED_MODES = {"reader", "home_assistant", "trmnl", "opendisplay", "photo_frame"}
OTA_PARTITION_SIZE = 0x640000
MINIMUM_FIRMWARE_SIZE = 64 * 1024
USB_RECOVERY_MAX_CHECKIN_AGE_SECONDS = 600


def _firmware_version(value: str) -> tuple[int, int, int]:
    match = re.search(r"flexdisplay[.-](\d+)\.(\d+)\.(\d+)", value)
    if not match:
        match = re.search(r"(\d+)\.(\d+)\.(\d+)", value)
    return tuple(int(part) for part in match.groups()) if match else (0, 0, 0)


def _firmware_metadata_error(settings: BridgeConfig) -> str:
    firmware = settings.firmware
    if not firmware.version or not firmware.url:
        return "No firmware release is configured"
    if not firmware.url.startswith(("https://", "http://")):
        return "Firmware URL must use HTTP or HTTPS"
    if not re.fullmatch(r"[0-9a-f]{64}", firmware.sha256):
        return "Firmware SHA-256 must contain 64 lowercase hexadecimal characters"
    if firmware.size < MINIMUM_FIRMWARE_SIZE or firmware.size > OTA_PARTITION_SIZE:
        return "Firmware size is outside the OTA application partition safety limits"
    return ""


def _firmware_install_blockers(
    record: dict[str, Any],
    settings: BridgeConfig,
    store: DeviceStore,
) -> list[str]:
    firmware = settings.firmware
    error = _firmware_metadata_error(settings)
    blockers = [error] if error else []
    if error:
        return blockers
    if _firmware_version(firmware.version) <= _firmware_version(str(record.get("firmware") or "")):
        blockers.append("Device already runs this release or a newer release")
    if record.get("sd_ready") is not True:
        blockers.append("Device SD card is not ready")
    usb_connected = record.get("usb_connected") is True
    battery = record.get("battery_percent")
    if not usb_connected and (
        battery is None or float(battery) < firmware.minimum_battery_percent
    ):
        blockers.append(
            f"Connect USB or charge the device to {firmware.minimum_battery_percent}%"
        )
    if record.get("pending_commands") and "install" not in record["pending_commands"]:
        blockers.append("Another command is pending")
    if record.get("dispatched_commands") and "install" not in record["dispatched_commands"]:
        blockers.append("Waiting for the previous command acknowledgement")

    rollout = store.firmware_rollout()
    if rollout.get("target_version") == firmware.version:
        status = str(rollout.get("status") or "")
        canary_id = str(rollout.get("canary_device_id") or "")
        if status == "failed":
            failed_id = str(rollout.get("last_failed_device_id") or "a fleet device")
            blockers.append(
                f"Rollout paused after failure on {failed_id}; configure a new release before continuing"
            )
        elif (
            firmware.canary_required
            and status in {"awaiting_canary", "canary_active"}
            and canary_id
            and canary_id != record.get("device_id")
        ):
            blockers.append(f"Waiting for canary {canary_id} to boot and acknowledge")
    elif firmware.canary_required and firmware.require_usb_for_canary and not usb_connected:
        blockers.append("The first canary installation requires USB power")

    already_active = (
        "install" in (record.get("pending_commands") or [])
        or "install" in (record.get("dispatched_commands") or [])
    )
    if not already_active and store.active_firmware_installs() >= firmware.max_parallel:
        blockers.append(
            f"Maximum of {firmware.max_parallel} concurrent firmware install(s) reached"
        )
    return blockers


def _usb_recovery_blockers(
    record: dict[str, Any],
    settings: BridgeConfig,
    store: DeviceStore,
) -> list[str]:
    """Explain why an operator cannot reconcile a USB-recovered canary."""
    target = settings.firmware.version
    rollout = store.firmware_rollout()
    blockers: list[str] = []
    if not target:
        blockers.append("No target firmware release is configured")
    if rollout.get("target_version") != target:
        blockers.append("The active rollout does not match the configured target")
    if rollout.get("status") != "canary_active":
        blockers.append("The rollout is not waiting for an active canary")
    if rollout.get("canary_device_id") != record.get("device_id"):
        blockers.append("This device is not the active canary")
    if record.get("firmware") != target:
        blockers.append("The device is not reporting the exact target firmware")
    if record.get("usb_connected") is not True:
        blockers.append("The device is not reporting USB power")
    if record.get("sd_ready") is not True:
        blockers.append("The device SD card is not ready")
    if record.get("pending_commands"):
        blockers.append("The device has pending commands")
    if list(record.get("dispatched_commands") or []) != ["install"]:
        blockers.append("The only dispatched command must be the stuck install")
    if not record.get("dispatched_command_id"):
        blockers.append("The stuck install has no durable command ID")
    try:
        seen = datetime.fromisoformat(str(record.get("last_seen")))
        age = (datetime.now(UTC) - seen).total_seconds()
        if age < 0 or age > USB_RECOVERY_MAX_CHECKIN_AGE_SECONDS:
            blockers.append("The device check-in is not recent enough")
    except (TypeError, ValueError):
        blockers.append("The device has no valid recent check-in")
    return blockers


def _decorate_device(
    record: dict[str, Any],
    settings: BridgeConfig,
    store: DeviceStore | None = None,
) -> dict[str, Any]:
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
            planned_sleep = _integer(
                str(result.get("sleep_seconds")) if result.get("sleep_seconds") is not None else None,
                0,
                0,
                86400,
            )
            online_window = max(180, int(profile.refresh_interval_seconds * 1.5) + 60, planned_sleep + 300)
            online = age <= online_window
            sleep_action = str(result.get("sleep_action") or "")
            if sleep_action == "power_off":
                power_state = "powered_off"
            elif sleep_action == "scheduled":
                grace = 0 if result.get("wake_reason") == "scheduled_timer" else profile.manual_wake_grace_seconds
                power_state = "awake" if age <= grace else ("sleeping" if online else "offline")
            else:
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
    result["assigned_live_mode"] = profile.live_mode
    result["assigned_manual_sleep_seconds"] = profile.manual_sleep_seconds
    result["assigned_intelligent_sleep"] = profile.intelligent_sleep
    result["assigned_active_start"] = profile.active_start
    result["assigned_active_end"] = profile.active_end
    result["assigned_timezone"] = profile.timezone
    result["assigned_critical_battery_percent"] = profile.critical_battery_percent
    result["assigned_low_battery_percent"] = profile.low_battery_percent
    result["assigned_low_battery_multiplier"] = profile.low_battery_multiplier
    result["assigned_unchanged_image_multiplier"] = profile.unchanged_image_multiplier
    result["assigned_stay_awake_on_usb"] = profile.stay_awake_on_usb
    result["assigned_manual_wake_grace_seconds"] = profile.manual_wake_grace_seconds
    result["available_profiles"] = list(settings.profiles)
    result["available_modes"] = sorted(SUPPORTED_MODES)
    result["update_available"] = bool(
        settings.firmware.version
        and settings.firmware.url
        and _firmware_version(settings.firmware.version) > _firmware_version(str(result.get("firmware") or ""))
    )
    if store:
        blockers = _firmware_install_blockers(result, settings, store)
        rollout = store.firmware_rollout()
        result["firmware_install_blockers"] = blockers
        result["firmware_install_ready"] = result["update_available"] and not blockers
        result["firmware_rollout_status"] = rollout.get("status") or "not_started"
        result["firmware_canary_device_id"] = rollout.get("canary_device_id")
        result["firmware_canary_verified"] = (
            rollout.get("target_version") == settings.firmware.version
            and rollout.get("status") == "canary_verified"
        )
        recovery_blockers = _usb_recovery_blockers(result, settings, store)
        result["usb_recovery_verification_blockers"] = recovery_blockers
        result["usb_recovery_verification_ready"] = not recovery_blockers
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
        live_mode=bool(record.get("assigned_live_mode", base.live_mode)),
        manual_sleep_seconds=_integer(
            str(record.get("assigned_manual_sleep_seconds"))
            if record.get("assigned_manual_sleep_seconds") is not None
            else None,
            base.manual_sleep_seconds,
            60,
            86400,
        ),
        intelligent_sleep=bool(record.get("assigned_intelligent_sleep", base.intelligent_sleep)),
        active_start=str(record.get("assigned_active_start") or base.active_start),
        active_end=str(record.get("assigned_active_end") or base.active_end),
        timezone=str(record.get("assigned_timezone") or base.timezone),
        critical_battery_percent=_integer(
            str(record.get("assigned_critical_battery_percent"))
            if record.get("assigned_critical_battery_percent") is not None
            else None,
            base.critical_battery_percent,
            5,
            50,
        ),
        low_battery_percent=_integer(
            str(record.get("assigned_low_battery_percent"))
            if record.get("assigned_low_battery_percent") is not None
            else None,
            base.low_battery_percent,
            10,
            80,
        ),
        low_battery_multiplier=_integer(
            str(record.get("assigned_low_battery_multiplier"))
            if record.get("assigned_low_battery_multiplier") is not None
            else None,
            base.low_battery_multiplier,
            1,
            12,
        ),
        unchanged_image_multiplier=_integer(
            str(record.get("assigned_unchanged_image_multiplier"))
            if record.get("assigned_unchanged_image_multiplier") is not None
            else None,
            base.unchanged_image_multiplier,
            1,
            12,
        ),
        stay_awake_on_usb=bool(record.get("assigned_stay_awake_on_usb", base.stay_awake_on_usb)),
        manual_wake_grace_seconds=_integer(
            str(record.get("assigned_manual_wake_grace_seconds"))
            if record.get("assigned_manual_wake_grace_seconds") is not None
            else None,
            base.manual_wake_grace_seconds,
            0,
            600,
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


def _clock_minutes(value: str, fallback: int) -> int:
    try:
        hour, minute = (int(part) for part in value.split(":", 1))
    except (TypeError, ValueError):
        return fallback
    return hour * 60 + minute if 0 <= hour <= 23 and 0 <= minute <= 59 else fallback


def _sleep_plan(
    profile: DeviceConfig,
    battery_percent: float | None,
    usb_connected: bool,
    image_unchanged: bool,
    now: datetime | None = None,
) -> dict[str, Any]:
    current = now or datetime.now(UTC)
    try:
        zone = ZoneInfo(profile.timezone)
    except ZoneInfoNotFoundError:
        zone = ZoneInfo("UTC")
    local = current.astimezone(zone)

    def plan(action: str, seconds: int, reason: str) -> dict[str, Any]:
        bounded = max(0, min(86400, int(seconds)))
        return {
            "sleep_action": action,
            "sleep_seconds": bounded,
            "sleep_reason": reason,
            "next_wake_at": (
                (current + timedelta(seconds=bounded)).isoformat(timespec="seconds")
                if action == "scheduled" and bounded
                else None
            ),
            "image_unchanged": image_unchanged,
        }

    if profile.live_mode:
        return plan("awake", 0, "live_mode")
    if not profile.intelligent_sleep:
        return plan("awake", 0, "disabled")
    if usb_connected and profile.stay_awake_on_usb:
        return plan("awake", 0, "usb_connected")
    if battery_percent is not None and battery_percent <= profile.critical_battery_percent:
        return plan("power_off", 0, "critical_battery")

    start = _clock_minutes(profile.active_start, 6 * 60)
    end = _clock_minutes(profile.active_end, 22 * 60)
    minute = local.hour * 60 + local.minute
    always_active = start == end
    active = always_active or (start <= minute < end if start < end else minute >= start or minute < end)

    if not active:
        next_start = datetime.combine(local.date(), time(start // 60, start % 60), tzinfo=zone)
        if next_start <= local:
            next_start += timedelta(days=1)
        seconds = max(60, int((next_start - local).total_seconds()))
        return plan("scheduled", seconds, "outside_active_hours")

    seconds = profile.refresh_interval_seconds
    reason = "refresh_interval"
    if battery_percent is not None and battery_percent <= profile.low_battery_percent:
        seconds *= profile.low_battery_multiplier
        reason = "low_battery"
    if image_unchanged:
        seconds *= profile.unchanged_image_multiplier
        reason = "unchanged_image" if reason == "refresh_interval" else f"{reason}_unchanged"
    return plan("scheduled", max(60, seconds), reason)


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
        return {"devices": [_decorate_device(record, settings, store) for record in store.all()]}

    @app.get("/api/v1/devices/{device_id}")
    def device(device_id: str) -> dict[str, Any]:
        selected = _device_id(device_id)
        record = store.get(selected)
        if not record:
            raise HTTPException(status_code=404, detail="Device not found")
        return _decorate_device(record, settings, store)

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
        if command == "install":
            current = store.get(selected)
            if not current:
                raise HTTPException(status_code=404, detail="Device has not checked in")
            blockers = _firmware_install_blockers(current, settings, store)
            if blockers:
                raise HTTPException(status_code=409, detail="; ".join(blockers))
            try:
                record = store.queue_firmware_install(
                    selected,
                    settings.firmware.version,
                    canary_required=settings.firmware.canary_required,
                    max_parallel=settings.firmware.max_parallel,
                )
            except ValueError as err:
                raise HTTPException(status_code=409, detail=str(err)) from err
        else:
            record = store.queue_command(selected, command)
        return {"queued": command, "device": record}

    @app.delete("/api/v1/devices/{device_id}/commands")
    def cancel_commands(device_id: str, request: Request) -> dict[str, Any]:
        authorize(request)
        selected = _device_id(device_id)
        record = store.clear_commands(selected)
        if not record:
            raise HTTPException(status_code=404, detail="Device not found")
        return {"cancelled": True, "device": _decorate_device(record, settings, store)}

    @app.post("/api/v1/devices/{device_id}/firmware/verify-usb-recovery")
    def verify_usb_recovery(
        device_id: str,
        payload: dict[str, Any],
        request: Request,
    ) -> dict[str, Any]:
        """Reconcile a stuck canary after an independently verified USB flash."""
        authorize(request)
        selected = _device_id(device_id)
        current = store.get(selected)
        if not current:
            raise HTTPException(status_code=404, detail="Device has not checked in")
        blockers = _usb_recovery_blockers(current, settings, store)
        if blockers:
            raise HTTPException(status_code=409, detail="; ".join(blockers))
        target = str(payload.get("expected_target_version") or "")
        command_id = str(payload.get("expected_command_id") or "")
        if target != settings.firmware.version:
            raise HTTPException(status_code=409, detail="Expected target does not match configuration")
        try:
            record = store.verify_usb_recovery(
                selected,
                target,
                command_id,
                max_checkin_age_seconds=USB_RECOVERY_MAX_CHECKIN_AGE_SECONDS,
            )
        except ValueError as err:
            raise HTTPException(status_code=409, detail=str(err)) from err
        return {
            "verified": True,
            "verification_method": "usb_recovery",
            "device": _decorate_device(record, settings, store),
            "audit": record.get("last_usb_recovery_verification"),
        }

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
        if "live_mode" in payload:
            assignment["assigned_live_mode"] = bool(payload["live_mode"])
        if "refresh_interval_seconds" in payload:
            assignment["assigned_refresh_interval_seconds"] = _integer(
                str(payload["refresh_interval_seconds"]),
                900,
                60,
                86400,
            )
        if "manual_sleep_seconds" in payload:
            assignment["assigned_manual_sleep_seconds"] = _integer(
                str(payload["manual_sleep_seconds"]),
                900,
                60,
                86400,
            )
        if "intelligent_sleep" in payload:
            assignment["assigned_intelligent_sleep"] = bool(payload["intelligent_sleep"])
        if "active_start" in payload:
            minutes = _clock_minutes(str(payload["active_start"]), -1)
            if minutes < 0:
                raise HTTPException(status_code=400, detail="active_start must be HH:MM")
            assignment["assigned_active_start"] = f"{minutes // 60:02d}:{minutes % 60:02d}"
        if "active_end" in payload:
            minutes = _clock_minutes(str(payload["active_end"]), -1)
            if minutes < 0:
                raise HTTPException(status_code=400, detail="active_end must be HH:MM")
            assignment["assigned_active_end"] = f"{minutes // 60:02d}:{minutes % 60:02d}"
        if "timezone" in payload:
            timezone = str(payload["timezone"])
            try:
                ZoneInfo(timezone)
            except ZoneInfoNotFoundError as err:
                raise HTTPException(status_code=400, detail="Unknown timezone") from err
            assignment["assigned_timezone"] = timezone
        for field, minimum, maximum in (
            ("critical_battery_percent", 5, 50),
            ("low_battery_percent", 10, 80),
            ("low_battery_multiplier", 1, 12),
            ("unchanged_image_multiplier", 1, 12),
            ("manual_wake_grace_seconds", 0, 600),
        ):
            if field in payload:
                assignment[f"assigned_{field}"] = _integer(
                    str(payload[field]), 0, minimum, maximum
                )
        if "stay_awake_on_usb" in payload:
            assignment["assigned_stay_awake_on_usb"] = bool(payload["stay_awake_on_usb"])
        if not assignment:
            raise HTTPException(status_code=400, detail="No provisioning fields supplied")
        record = store.provision(selected, assignment)
        store.queue_command(selected, "refresh")
        return {"device": _decorate_device(record, settings, store)}

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
        x_flexdisplay_command_id: str | None = Header(default=None),
        x_flexdisplay_usb_connected: str | None = Header(default=None),
        x_flexdisplay_uptime_seconds: str | None = Header(default=None),
        x_flexdisplay_free_heap: str | None = Header(default=None),
        x_flexdisplay_min_free_heap: str | None = Header(default=None),
        x_flexdisplay_sd_ready: str | None = Header(default=None),
        x_flexdisplay_wake_reason: str | None = Header(default=None),
        x_flexdisplay_button_events: str | None = Header(default=None),
        x_flexdisplay_image_sha256: str | None = Header(default=None),
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
                    "assigned_live_mode": configured.live_mode,
                    "assigned_manual_sleep_seconds": configured.manual_sleep_seconds,
                    "assigned_intelligent_sleep": configured.intelligent_sleep,
                    "assigned_active_start": configured.active_start,
                    "assigned_active_end": configured.active_end,
                    "assigned_timezone": configured.timezone,
                    "assigned_critical_battery_percent": configured.critical_battery_percent,
                    "assigned_low_battery_percent": configured.low_battery_percent,
                    "assigned_low_battery_multiplier": configured.low_battery_multiplier,
                    "assigned_unchanged_image_multiplier": configured.unchanged_image_multiplier,
                    "assigned_stay_awake_on_usb": configured.stay_awake_on_usb,
                    "assigned_manual_wake_grace_seconds": configured.manual_wake_grace_seconds,
                },
            )
        record = store.record_button_events(device_id, _button_events(x_flexdisplay_button_events)) or record
        command_acknowledged = store.acknowledge(
            device_id,
            x_flexdisplay_command_result or "",
            x_flexdisplay_command_id or "",
        )
        profile: DeviceConfig = _effective_device(configured, record)
        commands = store.consume_commands(device_id)
        if commands:
            record = store.get(device_id) or record
        command_id = str(record.get("dispatched_command_id") or "") if commands else ""
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
        image_unchanged = bool(x_flexdisplay_image_sha256 and x_flexdisplay_image_sha256 == digest)
        sleep_plan = _sleep_plan(
            profile,
            _number(x_flexdisplay_battery_percent),
            bool(_boolean(x_flexdisplay_usb_connected)),
            image_unchanged,
        )
        if "power-off" in commands:
            sleep_plan = {
                **sleep_plan,
                "sleep_action": "power_off",
                "sleep_seconds": 0,
                "sleep_reason": "remote_command",
                "next_wake_at": None,
            }
        elif "sleep" in commands or "clear" in commands:
            sleep_plan = {
                **sleep_plan,
                "sleep_action": "scheduled",
                "sleep_seconds": profile.manual_sleep_seconds,
                "sleep_reason": "remote_command",
                "next_wake_at": (
                    datetime.now(UTC) + timedelta(seconds=profile.manual_sleep_seconds)
                ).isoformat(timespec="seconds"),
            }
        record = store.touch(device_id, sleep_plan)
        state = {
            **record,
            "name": profile.name,
            "last_image_sha256": digest,
            "refresh_interval_seconds": profile.refresh_interval_seconds,
        }
        mqtt.publish_device(device_id, profile, state)
        response.headers["ETag"] = f'"{digest}"'
        response.headers["X-FlexDisplay-Refresh-Interval"] = str(profile.refresh_interval_seconds)
        response.headers["X-FlexDisplay-Image-SHA256"] = digest
        response.headers["X-FlexDisplay-Image-Unchanged"] = "true" if image_unchanged else "false"
        response.headers["X-FlexDisplay-Sleep-Action"] = sleep_plan["sleep_action"]
        response.headers["X-FlexDisplay-Sleep-Seconds"] = str(sleep_plan["sleep_seconds"])
        response.headers["X-FlexDisplay-Sleep-Reason"] = sleep_plan["sleep_reason"]
        response.headers["X-FlexDisplay-Manual-Wake-Grace"] = str(profile.manual_wake_grace_seconds)
        if settings.provisioning.enabled:
            response.headers["X-FlexDisplay-Provisioned"] = "true"
            response.headers["X-FlexDisplay-Device-Name"] = _header_value(profile.name)
            response.headers["X-FlexDisplay-Area"] = _header_value(profile.area)
            response.headers["X-FlexDisplay-Profile"] = _header_value(profile.profile)
            response.headers["X-FlexDisplay-Assigned-Mode"] = _header_value(profile.mode)
            response.headers["X-FlexDisplay-Auto-Start"] = "true" if profile.auto_start else "false"
            response.headers["X-FlexDisplay-Live-Mode"] = "true" if profile.live_mode else "false"
        response.headers["X-FlexDisplay-Commands"] = ",".join(commands)
        if command_id:
            response.headers["X-FlexDisplay-Command-ID"] = command_id
        if x_flexdisplay_command_result:
            response.headers["X-FlexDisplay-Command-Acknowledged"] = (
                "true" if command_acknowledged else "false"
            )
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
