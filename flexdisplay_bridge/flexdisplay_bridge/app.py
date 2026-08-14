from __future__ import annotations

import asyncio
import hashlib
import hmac
import io
import json
import logging
import os
import re
from contextlib import asynccontextmanager, suppress
from dataclasses import replace
from datetime import UTC, datetime, time, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import Body, FastAPI, Header, HTTPException, Request, Response
from fastapi.responses import FileResponse, RedirectResponse
from PIL import Image

from . import __version__
from .button_actions import (
    BUTTONS as CONFIGURABLE_BUTTONS,
)
from .button_actions import (
    GESTURES,
    ButtonActionValidationError,
    mappings_payload,
    normalize_mappings,
    resolve_action,
)
from .button_actions import (
    MODE as BUTTON_ACTION_MODE,
)
from .config import BridgeConfig, DeviceConfig, EntityConfig, FirmwareConfig, load_config
from .content_channels import (
    ContentChannelStore,
    ContentChannelValidationError,
    ContentPage,
    parse_channel,
)
from .content_pack import (
    MAX_PACK_BYTES,
    MAX_QUICK_CARD_REQUEST_BYTES,
    ContentPackAccessError,
    ContentPackConflictError,
    ContentPackError,
    ContentPackStore,
)
from .content_renderer import render_content_page
from .dashboard_assets import (
    MAX_BADGE_PHOTO_BYTES,
    DashboardAssetStore,
    DashboardAssetValidationError,
)
from .dashboard_store import (
    BADGE_THEMES,
    DashboardProfileStore,
    DashboardValidationError,
    parse_profile,
    profile_payload,
)
from .dashboards import build_dashboard_pages, select_active_pages
from .device_capabilities import (
    DeviceCapabilityDescriptor,
    resolve_device_capabilities,
)
from .firmware_mirror import FirmwareMirror, FirmwareMirrorError
from .flexhub_client import FlexHubClient, FlexHubClientError
from .home_assistant import HomeAssistantClient
from .loading_screen import (
    MAX_LOGO_BYTES,
    LoadingScreenStore,
    LoadingScreenValidationError,
)
from .meshtastic_console import (
    MeshtasticConsoleStore,
    MeshtasticConsoleValidationError,
)
from .mqtt_service import MqttService
from .photo_frame import (
    MAX_IMAGE_BYTES,
    PhotoFrameMediaStore,
    PhotoFrameValidationError,
)
from .renderer import DashboardRenderer
from .rook_interactions import (
    RookBroker,
    RookInteractionError,
    build_page_interactions,
    normalize_notification_actions,
)
from .screen_history import ScreenHistoryError, ScreenHistoryStore
from .store import DeviceStore
from .voice_assistant import (
    HomeAssistantVoiceClient,
    VoiceAssistantError,
    display_text,
    encode_voice_response,
)

LOGGER = logging.getLogger(__name__)

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
    "restart-app",
    "test-chime",
    "volume-up",
    "volume-down",
    "mute",
    "unmute",
    "brightness-up",
    "brightness-down",
    "install",
}
SUPPORTED_BUTTONS = {
    "back",
    "confirm",
    "left",
    "right",
    "up",
    "down",
    "home",
    "side_previous",
    "side_next",
    "power",
}
SUPPORTED_MODES = {"reader", "home_assistant", "trmnl", "opendisplay", "photo_frame"}
FLEET_POLICY_PRESETS: dict[str, dict[str, Any]] = {
    "battery_saver": {
        "label": "Battery Saver",
        "description": "Long refresh intervals and aggressive scheduled sleep.",
        "settings": {
            "live_mode": False,
            "intelligent_sleep": True,
            "stay_awake_on_usb": False,
            "refresh_interval_seconds": 3600,
            "manual_sleep_seconds": 1800,
            "manual_wake_grace_seconds": 30,
            "low_battery_multiplier": 6,
            "unchanged_image_multiplier": 4,
            "rendering_profile": "standard",
            "open_display_transport_policy": "ble_only",
        },
    },
    "balanced": {
        "label": "Balanced",
        "description": "Responsive dashboards with battery-aware scheduled sleep.",
        "settings": {
            "live_mode": False,
            "intelligent_sleep": True,
            "stay_awake_on_usb": True,
            "refresh_interval_seconds": 900,
            "manual_sleep_seconds": 900,
            "manual_wake_grace_seconds": 60,
            "low_battery_multiplier": 4,
            "unchanged_image_multiplier": 2,
            "rendering_profile": "standard",
            "open_display_transport_policy": "auto",
        },
    },
    "usb_kiosk": {
        "label": "USB Kiosk",
        "description": "Always awake on USB with frequent dashboard refreshes.",
        "settings": {
            "live_mode": True,
            "intelligent_sleep": False,
            "stay_awake_on_usb": True,
            "refresh_interval_seconds": 300,
            "manual_sleep_seconds": 900,
            "manual_wake_grace_seconds": 120,
            "low_battery_multiplier": 2,
            "unchanged_image_multiplier": 1,
            "rendering_profile": "standard",
            "open_display_transport_policy": "lan_preferred",
        },
    },
    "always_on_color": {
        "label": "Always-on Colour",
        "description": "Mains-powered Android, LCD, and OLED dashboards with push refresh and no battery sleep scaling.",
        "settings": {
            "live_mode": True,
            "intelligent_sleep": False,
            "stay_awake_on_usb": True,
            "refresh_interval_seconds": 60,
            "manual_sleep_seconds": 900,
            "manual_wake_grace_seconds": 120,
            "low_battery_multiplier": 1,
            "unchanged_image_multiplier": 1,
            "rendering_profile": "standard",
            "open_display_transport_policy": "lan_preferred",
        },
    },
    "x4_photo": {
        "label": "X4 Photo Quality",
        "description": "Experimental full-refresh rendering tuned for X4 photographs and shaded artwork.",
        "settings": {
            "live_mode": False,
            "intelligent_sleep": True,
            "stay_awake_on_usb": True,
            "refresh_interval_seconds": 1800,
            "manual_sleep_seconds": 900,
            "manual_wake_grace_seconds": 60,
            "low_battery_multiplier": 4,
            "unchanged_image_multiplier": 2,
            "rendering_profile": "photo",
            "open_display_transport_policy": "auto",
        },
    },
}
OTA_PARTITION_SIZE = 0x640000
MINIMUM_FIRMWARE_SIZE = 64 * 1024
USB_RECOVERY_MAX_CHECKIN_AGE_SECONDS = 600
FIRMWARE_PROGRESS_STAGES = {
    "preflight",
    "downloading",
    "validating",
    "flashing",
    "rebooting",
    "failed",
    "cancelled",
}


def _flexhub_proxy_status(error: FlexHubClientError) -> int:
    return error.status_code if error.status_code in {409, 413, 429, 503} else 502


def _valid_external_usb_evidence(device_id: str, evidence: Any) -> bool:
    """Validate a recent macOS USB observation that matches the device identity."""
    if not isinstance(evidence, dict):
        return False
    serial = re.sub(r"[^0-9A-F]", "", str(evidence.get("serial") or "").upper())
    device_suffix = re.sub(r"[^0-9A-F]", "", device_id.upper())[-6:]
    if (
        evidence.get("source") != "macos_ioreg"
        or not serial.endswith(device_suffix)
        or not str(evidence.get("port") or "").startswith("/dev/cu.usbmodem")
        or not re.fullmatch(r"[0-9a-f]{64}", str(evidence.get("backup_sha256") or ""))
    ):
        return False
    try:
        observed = datetime.fromisoformat(str(evidence.get("observed_at") or ""))
        age = (datetime.now(UTC) - observed).total_seconds()
        return 0 <= age <= USB_RECOVERY_MAX_CHECKIN_AGE_SECONDS
    except (TypeError, ValueError):
        return False


def _firmware_version(value: str) -> tuple[int, int, int]:
    match = re.search(r"flexdisplay[.-](\d+)\.(\d+)\.(\d+)", value)
    if not match:
        match = re.search(r"(\d+)\.(\d+)\.(\d+)", value)
    return tuple(int(part) for part in match.groups()) if match else (0, 0, 0)


def _safe_display_url(value: str) -> str:
    """Strip credentials and query data from an operational display URL."""
    from urllib.parse import urlsplit, urlunsplit

    selected = str(value or "").strip()
    if not selected:
        return ""
    try:
        parsed = urlsplit(selected)
        parsed_port = parsed.port
    except ValueError:
        return ""
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return ""
    host = parsed.hostname
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    port = f":{parsed_port}" if parsed_port is not None else ""
    return urlunsplit((parsed.scheme, f"{host}{port}", parsed.path, "", ""))


def _safe_host_port(value: str, port: int) -> str:
    """Return a broker endpoint without userinfo, paths, or query data."""
    from ipaddress import ip_address
    from urllib.parse import urlsplit

    selected = str(value or "").strip()
    if not selected:
        return f"port {port}"
    try:
        literal = selected.strip("[]")
        try:
            host = str(ip_address(literal))
        except ValueError:
            parsed = urlsplit(selected if "://" in selected else f"//{selected}")
            host = parsed.hostname or ""
    except ValueError:
        host = ""
    if not host:
        return "Configured endpoint"
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    return f"{host}:{port}"


def _home_assistant_apps_url(request: Request) -> str:
    """Link to Home Assistant from either ingress or the Bridge's direct port."""
    from urllib.parse import urlsplit, urlunsplit

    configured = _safe_display_url(os.getenv("FLEXDISPLAY_HA_UI_URL", ""))
    if configured:
        return f"{configured.rstrip('/')}/config/apps"

    forwarded_host = str(request.headers.get("x-forwarded-host") or "").split(
        ",", 1
    )[0].strip()
    forwarded_proto = str(request.headers.get("x-forwarded-proto") or "").split(
        ",", 1
    )[0].strip()
    selected = (
        f"{forwarded_proto or request.url.scheme}://{forwarded_host}"
        if forwarded_host
        else str(request.base_url)
    )
    try:
        parsed = urlsplit(selected)
        host = parsed.hostname or ""
        port = parsed.port
    except ValueError:
        return "/config/apps"
    if not host or port in {None, 80, 443, 8123}:
        return "/config/apps"
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    return urlunsplit(
        (forwarded_proto or parsed.scheme or "http", f"{host}:8123", "/config/apps", "", "")
    )


def _public_flexhub_error(value: Any) -> str:
    """Map untrusted network errors to a small secret-free status vocabulary."""
    selected = str(value or "").lower()
    if not selected:
        return ""
    if "not configured" in selected:
        return "No FlexHub endpoint is configured."
    if "waiting for flexhub" in selected:
        return "FlexHub is configured but has not connected yet."
    if "401" in selected or "access pin" in selected:
        return "FlexHub rejected the configured access PIN."
    if "404" in selected or "not found" in selected:
        return "The FlexHub status API is unavailable at the configured endpoint."
    if "redirect" in selected:
        return "The FlexHub endpoint redirected; configure the hub's direct base address."
    if "timed out" in selected or "timeout" in selected:
        return "The FlexHub connection timed out."
    if "non-json" in selected or "invalid status" in selected:
        return "FlexHub returned an invalid status response."
    if "meshtastic web page" in selected:
        return "The configured endpoint does not expose the FlexHub status API."
    return "FlexHub could not be reached at the configured endpoint."


def _public_firmware_error(value: Any) -> str:
    """Return a useful firmware error without reflecting request URLs or paths."""
    selected = str(value or "").lower()
    if not selected:
        return ""
    if "sha-256" in selected or "sha256" in selected:
        return "Firmware mirror verification failed its SHA-256 check."
    if "size mismatch" in selected:
        return "Firmware mirror verification found an unexpected file size."
    if "manifest is incomplete" in selected:
        return "The firmware release manifest is incomplete."
    if "timed out" in selected or "timeout" in selected:
        return "The firmware mirror download timed out."
    return "The firmware mirror could not prepare the configured release."


def _public_mirror_status(status: dict[str, Any]) -> dict[str, Any]:
    """Allowlist the unauthenticated firmware health fields."""
    return {
        "enabled": bool(status.get("enabled")),
        "ready": bool(status.get("ready")),
        "state": str(status.get("state") or "idle"),
        "version": str(status.get("version") or ""),
        "size": int(status.get("size") or 0),
        "source": str(status.get("source") or ""),
        "last_error": _public_firmware_error(status.get("last_error")),
        "last_error_at": status.get("last_error_at"),
        "last_ready_at": status.get("last_ready_at"),
        "next_retry_at": status.get("next_retry_at"),
    }


def _is_note4(record: dict[str, Any] | str) -> bool:
    model = record if isinstance(record, str) else str(record.get("model") or "")
    return resolve_device_capabilities(model).firmware.provider == "note4"


def _is_android_display(record: dict[str, Any] | str) -> bool:
    model = record if isinstance(record, str) else str(record.get("model") or "")
    return resolve_device_capabilities(model).family == "android_receiver"


def _device_capabilities(record: dict[str, Any] | str):
    if isinstance(record, str):
        return resolve_device_capabilities(record)
    model = str(record.get("model") or "")
    def reported_size(name: str) -> int | None:
        value = record.get(name)
        try:
            return int(value) if value is not None else None
        except (TypeError, ValueError):
            return None

    descriptor = resolve_device_capabilities(
        model,
        capabilities=record.get("transfer_capabilities") or (),
        width=(
            int(record["width"])
            if record.get("width") is not None
            else None
        ),
        height=(
            int(record["height"])
            if record.get("height") is not None
            else None
        ),
        board_id=str(record.get("board_id") or ""),
        hardware_revision=str(record.get("hardware_revision") or ""),
        mcu_family=str(record.get("mcu_family") or ""),
        flash_size_bytes=reported_size("flash_size_bytes"),
        psram_size_bytes=reported_size("psram_size_bytes"),
    )
    model_reported = record.get("model_reported")
    if descriptor.supports_xteink_ota and model_reported is not True:
        device_id = str(record.get("device_id") or "").upper()
        trusted_prefixes = (
            ("X3-",) if descriptor.model_key == "x3" else ("X4-",)
        )
        if not device_id.startswith(trusted_prefixes):
            return resolve_device_capabilities(
                "UNKNOWN",
                capabilities=record.get("transfer_capabilities") or (),
                width=descriptor.display.width,
                height=descriptor.display.height,
            )
    return descriptor


def _device_identity(record: dict[str, Any]) -> dict[str, Any]:
    """Describe how Bridge identity was established and whether it conflicts."""
    descriptor = _device_capabilities(record)
    device_id = str(record.get("device_id") or "").upper()
    prefix_model = (
        "x3"
        if device_id.startswith("X3-")
        else "x4"
        if device_id.startswith("X4-")
        else "note4"
        if device_id.startswith("N4-")
        else ""
    )
    reported = record.get("model_reported") is True
    source = (
        "reported"
        if reported
        else "configured"
        if record.get("last_seen") is None and descriptor.known_model
        else "inferred_id"
        if prefix_model and prefix_model == descriptor.model_key
        else "capability_inferred"
        if descriptor.family == "generic_embedded"
        else "unclassified"
    )
    confidence = (
        "authoritative"
        if reported
        else "configured"
        if source == "configured"
        else "legacy_inferred"
        if source in {"inferred_id", "capability_inferred"}
        else "unknown"
    )
    conflict = bool(reported and prefix_model and prefix_model != descriptor.model_key)
    return {
        "source": source,
        "confidence": confidence,
        "reported": reported,
        "model": str(record.get("model") or "UNKNOWN"),
        "model_key": descriptor.model_key,
        "family": descriptor.family,
        "label": descriptor.label,
        "firmware_owner": descriptor.firmware.provider,
        "firmware_artifact_family": descriptor.firmware.artifact_family,
        "reported_firmware_artifact": str(
            record.get("reported_firmware_artifact") or ""
        ),
        "hardware": {
            "board_id": descriptor.hardware.board_id,
            "hardware_revision": descriptor.hardware.hardware_revision,
            "mcu_family": descriptor.hardware.mcu_family,
            "flash_size_bytes": descriptor.hardware.flash_size_bytes,
            "psram_size_bytes": descriptor.hardware.psram_size_bytes,
            "reported_identity_complete": (
                descriptor.hardware.reported_identity_complete
            ),
            "management_profile": descriptor.hardware.management_profile,
        },
        "id_prefix_model": prefix_model,
        "conflict": conflict,
        "conflict_detail": (
            f"Device ID suggests {prefix_model}, but the device reported "
            f"{descriptor.model_key}. Reported identity is authoritative."
            if conflict
            else ""
        ),
        "last_changed_at": record.get("last_identity_changed_at"),
        "previous_model": str(record.get("last_identity_previous_model") or ""),
    }


def _device_timeline(
    record: dict[str, Any],
    *,
    include_checkins: bool = False,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Merge bounded device histories into one reverse-chronological timeline."""
    events: list[dict[str, Any]] = []
    for item in record.get("identity_history") or []:
        events.append(
            {
                "type": "identity",
                "at": item.get("at"),
                "title": "Device identity changed",
                "status": item.get("source") or "observed",
                "detail": (
                    f"{item.get('from_model') or 'Unclassified'} to "
                    f"{item.get('to_model') or 'Unknown'}"
                ),
            }
        )
    for item in record.get("management_history") or []:
        events.append(
            {
                "type": "management",
                "at": item.get("at"),
                "title": str(item.get("action") or "Management action"),
                "status": "succeeded" if item.get("success") else "failed",
                "detail": str(item.get("detail") or ""),
            }
        )
    for item in record.get("command_history") or []:
        events.append(
            {
                "type": "command",
                "at": item.get("completed_at"),
                "title": "Device command completed",
                "status": str(item.get("result") or "completed").split(":", 1)[0],
                "detail": str(item.get("result") or ""),
                "command_id": str(item.get("command_id") or ""),
            }
        )
    for item in record.get("firmware_progress_history") or []:
        events.append(
            {
                "type": "firmware",
                "at": item.get("at"),
                "title": "Firmware update",
                "status": str(item.get("stage") or "unknown"),
                "detail": str(item.get("detail") or ""),
                "percent": item.get("percent"),
                "command_id": str(item.get("command_id") or ""),
            }
        )
    for item in record.get("reset_history") or []:
        events.append(
            {
                "type": "reset",
                "at": item.get("at"),
                "title": "Device restarted",
                "status": str(item.get("reason") or "unknown"),
                "detail": str(item.get("wake_reason") or ""),
                "boot_id": str(item.get("boot_id") or ""),
            }
        )
    if include_checkins:
        for item in record.get("checkin_history") or []:
            events.append(
                {
                    "type": "checkin",
                    "at": item.get("at"),
                    "title": "Device check-in",
                    "status": "online",
                    "detail": "Telemetry received by the Bridge.",
                }
            )
    if record.get("provisioning_updated_at"):
        events.append(
            {
                "type": "provisioning",
                "at": record.get("provisioning_updated_at"),
                "title": "Provisioning updated",
                "status": str(record.get("assigned_policy_name") or "custom"),
                "detail": "Bridge-backed assignments changed.",
            }
        )
    events.sort(key=lambda item: str(item.get("at") or ""), reverse=True)
    return events[: max(1, min(200, int(limit)))]


def _capability_normalized_config(
    config: DeviceConfig,
    descriptor: DeviceCapabilityDescriptor,
) -> DeviceConfig:
    """Return safe effective defaults for the device capability contract."""
    modes = descriptor.management.modes
    mode = config.mode
    if mode not in modes:
        mode = (
            "home_assistant"
            if "home_assistant" in modes
            else modes[0]
            if modes
            else "home_assistant"
        )
    changes: dict[str, Any] = {"mode": mode}
    if not descriptor.management.supports_sleep_policy:
        changes.update(
            {
                "live_mode": True,
                "intelligent_sleep": False,
                "stay_awake_on_usb": True,
                "low_battery_multiplier": 1,
                "unchanged_image_multiplier": 1,
            }
        )
    if not descriptor.management.supports_opendisplay_policy:
        changes["open_display_transport_policy"] = "auto"
    return replace(config, **changes)


def _transfer_capabilities(record: dict[str, Any]) -> set[str]:
    return {
        str(value).strip().lower()
        for value in (record.get("transfer_capabilities") or [])
        if str(value).strip()
    }


def _is_always_on_color_display(record: dict[str, Any]) -> bool:
    capabilities = _transfer_capabilities(record)
    color = bool(record.get("color_available")) or "color" in capabilities
    explicitly_always_on = bool(
        capabilities.intersection({"always-on-color", "always-on", "mains-powered"})
    )
    return color and (_is_android_display(record) or explicitly_always_on)


def _supports_mqtt_screen_refresh(record: dict[str, Any]) -> bool:
    return bool(
        _transfer_capabilities(record).intersection(
            {"mqtt-screen-refresh", "mqtt-refresh", "push-refresh-mqtt"}
        )
    )


def _display_runtime(record: dict[str, Any]) -> dict[str, str]:
    capabilities = _transfer_capabilities(record)
    if _is_android_display(record):
        technology = "lcd"
        delivery = "long_poll"
    else:
        technology = (
            "oled"
            if "oled" in capabilities
            else "lcd"
            if capabilities.intersection({"lcd", "tft", "always-on-color"})
            else "eink"
        )
        delivery = "mqtt" if _supports_mqtt_screen_refresh(record) else "poll"
    always_on = _is_always_on_color_display(record)
    return {
        "display_technology": technology,
        "power_class": "always_on_color" if always_on else "battery_managed",
        "refresh_delivery": delivery,
        "policy_overlay": "always_on_color" if always_on else "",
    }


def _device_firmware(settings: BridgeConfig, record: dict[str, Any] | str) -> FirmwareConfig:
    firmware_capabilities = _device_capabilities(record).firmware
    if _device_capabilities(record).model_key == "x4_pro":
        # X4 Pro never inherits the legacy X3/X4 image. The dedicated channel
        # is deliberately empty until an exact board/revision manifest lands.
        return getattr(settings, "x4_pro_firmware", FirmwareConfig())
    provider = firmware_capabilities.provider
    if provider == "xteink":
        return settings.firmware
    if provider == "note4":
        return settings.note4_firmware
    # Android applications, generic ESP displays, and unknown devices never
    # inherit the X3/X4 release merely because they share the Bridge protocol.
    return FirmwareConfig()


def _firmware_metadata_error(
    settings: BridgeConfig,
    firmware: FirmwareConfig | None = None,
) -> str:
    firmware = firmware or settings.firmware
    if not firmware.version or not firmware.url:
        return "No firmware release is configured"
    if (
        firmware is settings.firmware
        and firmware.url == "packaged"
        and not firmware.mirror_enabled
    ):
        return "Packaged X3/X4 firmware requires the local mirror"
    if firmware.url != "packaged" and not firmware.url.startswith(("https://", "http://")):
        return "Firmware URL must use HTTP or HTTPS"
    if not re.fullmatch(r"[0-9a-f]{64}", firmware.sha256):
        return "Firmware SHA-256 must contain 64 lowercase hexadecimal characters"
    if firmware.size < MINIMUM_FIRMWARE_SIZE or firmware.size > OTA_PARTITION_SIZE:
        return "Firmware size is outside the OTA application partition safety limits"
    return ""


def _firmware_artifact_blockers(
    record: dict[str, Any] | str,
    firmware: FirmwareConfig,
) -> list[str]:
    """Require a complete exact manifest before admitting X4 Pro firmware."""

    descriptor = _device_capabilities(record)
    if descriptor.model_key != "x4_pro":
        return []
    blockers: list[str] = []
    if firmware.artifact_family != "x4pro_s3":
        blockers.append("X4 Pro requires its dedicated firmware artifact family")
        return blockers
    if descriptor.firmware.artifact_family != firmware.artifact_family:
        blockers.append("This X4 Pro hardware revision has no admitted firmware artifact")
        return blockers
    if not firmware.version or not firmware.url or not firmware.sha256 or not firmware.size:
        blockers.append("No X4 Pro firmware artifact is configured")

    if isinstance(record, str):
        hardware: dict[str, Any] = {}
    else:
        hardware = record

    checks: tuple[tuple[str, Any, tuple[Any, ...], str], ...] = (
        ("model", descriptor.model_key, firmware.compatible_models, "model"),
        (
            "board_id",
            hardware.get("board_id"),
            firmware.compatible_board_ids,
            "board ID",
        ),
        (
            "hardware_revision",
            hardware.get("hardware_revision"),
            firmware.compatible_hardware_revisions,
            "hardware revision",
        ),
        (
            "mcu_family",
            hardware.get("mcu_family"),
            firmware.compatible_mcu_families,
            "MCU family",
        ),
        (
            "flash_size_bytes",
            hardware.get("flash_size_bytes"),
            firmware.compatible_flash_sizes,
            "flash size",
        ),
        (
            "psram_size_bytes",
            hardware.get("psram_size_bytes"),
            firmware.compatible_psram_sizes,
            "PSRAM size",
        ),
        (
            "reported_firmware_artifact",
            hardware.get("reported_firmware_artifact"),
            (firmware.artifact_family,),
            "reported firmware artifact",
        ),
    )
    for field, reported, allowed, label in checks:
        if not allowed:
            blockers.append(f"X4 Pro artifact manifest has no admitted {label}")
            continue
        if reported in (None, ""):
            blockers.append(f"Device did not report its {label}")
            continue
        if field in {"flash_size_bytes", "psram_size_bytes"}:
            try:
                matches = int(reported) in {int(value) for value in allowed}
            except (TypeError, ValueError):
                matches = False
        elif field == "reported_firmware_artifact":
            matches = str(reported).strip() in {
                str(value).strip() for value in allowed
            }
        else:
            normalized_reported = re.sub(
                r"[^a-z0-9]+", "-", str(reported).strip().lower()
            ).strip("-")
            normalized_allowed = {
                re.sub(r"[^a-z0-9]+", "-", str(value).strip().lower()).strip("-")
                for value in allowed
            }
            matches = normalized_reported in normalized_allowed
        if not matches:
            blockers.append(f"Device {label} is not admitted by the X4 Pro artifact")
    return blockers


def _firmware_maintenance_status(
    settings: BridgeConfig,
    now: datetime | None = None,
) -> dict[str, Any]:
    firmware = settings.firmware
    if not firmware.maintenance_window_enabled:
        return {
            "enabled": False,
            "open": True,
            "start": firmware.maintenance_start,
            "end": firmware.maintenance_end,
            "timezone": firmware.maintenance_timezone,
            "next_start": None,
            "usb_override": firmware.maintenance_usb_override,
        }
    try:
        zone = ZoneInfo(firmware.maintenance_timezone)
    except ZoneInfoNotFoundError:
        zone = ZoneInfo("UTC")
    current = (now or datetime.now(UTC)).astimezone(zone)
    start = _clock_minutes(firmware.maintenance_start, 60)
    end = _clock_minutes(firmware.maintenance_end, 5 * 60)
    minute = current.hour * 60 + current.minute
    open_now = (
        True
        if start == end
        else (start <= minute < end if start < end else minute >= start or minute < end)
    )
    next_start = datetime.combine(
        current.date(),
        time(start // 60, start % 60),
        tzinfo=zone,
    )
    if next_start <= current:
        next_start += timedelta(days=1)
    return {
        "enabled": True,
        "open": open_now,
        "start": f"{start // 60:02d}:{start % 60:02d}",
        "end": f"{end // 60:02d}:{end % 60:02d}",
        "timezone": str(zone),
        "next_start": (
            None
            if open_now
            else next_start.astimezone(UTC).isoformat(timespec="seconds")
        ),
        "usb_override": firmware.maintenance_usb_override,
    }


def _firmware_install_blockers(
    record: dict[str, Any],
    settings: BridgeConfig,
    store: DeviceStore,
) -> list[str]:
    firmware = _device_firmware(settings, record)
    compatibility_blockers = _firmware_artifact_blockers(record, firmware)
    if compatibility_blockers:
        return compatibility_blockers
    error = _firmware_metadata_error(settings, firmware)
    blockers = [error] if error else []
    if error:
        return blockers
    if _firmware_version(firmware.version) <= _firmware_version(
        str(record.get("firmware") or "")
    ):
        blockers.append("Device already runs this release or a newer release")
    note4 = _is_note4(record)
    if not note4 and record.get("sd_ready") is not True:
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
    if (
        record.get("dispatched_commands")
        and "install" not in record["dispatched_commands"]
    ):
        blockers.append("Waiting for the previous command acknowledgement")
    maintenance = _firmware_maintenance_status(settings)
    if not maintenance["open"] and not (maintenance["usb_override"] and usb_connected):
        blockers.append(
            "Outside firmware maintenance window "
            f"{maintenance['start']}-{maintenance['end']} "
            f"{maintenance['timezone']}"
        )

    if note4:
        already_active = "install" in (
            record.get("pending_commands") or []
        ) or "install" in (record.get("dispatched_commands") or [])
        if not already_active and store.active_firmware_installs() >= firmware.max_parallel:
            blockers.append(
                f"Maximum of {firmware.max_parallel} concurrent firmware install(s) reached"
            )
        return blockers

    rollout = store.firmware_rollout()
    if rollout.get("target_version") == firmware.version:
        status = str(rollout.get("status") or "")
        canary_id = str(rollout.get("canary_device_id") or "")
        if status == "failed":
            failed_id = str(rollout.get("last_failed_device_id") or "a fleet device")
            blockers.append(
                f"Rollout paused after failure on {failed_id}; retry or reset the rollout"
            )
        elif (
            firmware.canary_required
            and status in {"awaiting_canary", "canary_active"}
            and canary_id
            and canary_id != record.get("device_id")
        ):
            blockers.append(f"Waiting for canary {canary_id} to boot and acknowledge")
        elif (
            firmware.canary_required
            and firmware.require_usb_for_canary
            and status == "awaiting_canary"
            and not canary_id
            and not usb_connected
        ):
            blockers.append("The first canary installation requires USB power")
    elif (
        firmware.canary_required
        and firmware.require_usb_for_canary
        and not usb_connected
    ):
        blockers.append("The first canary installation requires USB power")

    already_active = "install" in (
        record.get("pending_commands") or []
    ) or "install" in (record.get("dispatched_commands") or [])
    if not already_active and store.active_firmware_installs() >= firmware.max_parallel:
        blockers.append(
            f"Maximum of {firmware.max_parallel} concurrent firmware install(s) reached"
        )
    return blockers


def _firmware_retry_blockers(
    record: dict[str, Any],
    settings: BridgeConfig,
    store: DeviceStore,
) -> list[str]:
    """Explain why a failed device cannot be retried yet."""
    if not _device_capabilities(record).supports_xteink_ota:
        return ["Firmware retry is not available for this device family"]
    blockers: list[str] = []
    error = _firmware_metadata_error(settings)
    if error:
        return [error]
    if record.get("firmware_update_status") not in {"failed", "cancelled"}:
        blockers.append(
            "The device has no failed or cancelled firmware update to retry"
        )
    if _firmware_version(settings.firmware.version) <= _firmware_version(
        str(record.get("firmware") or "")
    ):
        blockers.append("Device already runs this release or a newer release")
    if record.get("sd_ready") is not True:
        blockers.append("Device SD card is not ready")
    battery = record.get("battery_percent")
    if record.get("usb_connected") is not True and (
        battery is None or float(battery) < settings.firmware.minimum_battery_percent
    ):
        blockers.append(
            f"Connect USB or charge the device to {settings.firmware.minimum_battery_percent}%"
        )
    if record.get("pending_commands") or record.get("dispatched_commands"):
        blockers.append("Cancel the existing command before retrying")
    retries = int(record.get("firmware_retry_count") or 0)
    if retries >= settings.firmware.retry_limit:
        blockers.append(
            f"Firmware retry limit of {settings.firmware.retry_limit} has been reached"
        )
    last_attempt = record.get("firmware_last_retry_at") or record.get(
        "firmware_update_error_at"
    )
    if settings.firmware.retry_backoff_seconds > 0 and last_attempt:
        try:
            elapsed = (
                datetime.now(UTC) - datetime.fromisoformat(str(last_attempt))
            ).total_seconds()
            if elapsed < settings.firmware.retry_backoff_seconds:
                blockers.append(
                    "Retry backoff is active for "
                    f"{max(1, int(settings.firmware.retry_backoff_seconds - elapsed))} seconds"
                )
        except ValueError:
            pass
    if store.active_firmware_installs() >= settings.firmware.max_parallel:
        blockers.append(
            f"Maximum of {settings.firmware.max_parallel} concurrent firmware install(s) reached"
        )
    return blockers


def _usb_recovery_blockers(
    record: dict[str, Any],
    settings: BridgeConfig,
    store: DeviceStore,
    external_usb_evidence: dict[str, Any] | None = None,
) -> list[str]:
    """Explain why an operator cannot reconcile a USB-recovered device."""
    if not _device_capabilities(record).supports_xteink_ota:
        return ["USB recovery verification is not available for this device family"]
    target = settings.firmware.version
    blockers: list[str] = []
    if not target:
        blockers.append("No target firmware release is configured")
    rollout = store.firmware_rollout()
    rollout_still_blocked = rollout.get("target_version") == target and rollout.get(
        "status"
    ) in {"awaiting_canary", "canary_active", "failed"}
    if record.get("firmware_update_status") == "verified" and not rollout_still_blocked:
        blockers.append("This firmware installation is already verified")
    tracked = bool(
        record.get("firmware_update_target") == target
        or "install" in (record.get("pending_commands") or [])
        or "install" in (record.get("dispatched_commands") or [])
    )
    if not tracked:
        blockers.append("The device has no tracked firmware installation to recover")
    if record.get("firmware") != target:
        blockers.append("The device is not reporting the exact target firmware")
    if record.get("usb_connected") is not True and not _valid_external_usb_evidence(
        str(record.get("device_id") or ""),
        external_usb_evidence,
    ):
        blockers.append("The device is not reporting USB power")
    if record.get("sd_ready") is not True:
        blockers.append("The device SD card is not ready")
    if any(
        command != "install" for command in (record.get("pending_commands") or [])
    ) or any(
        command != "install" for command in (record.get("dispatched_commands") or [])
    ):
        blockers.append("The device has an unrelated command in progress")
    try:
        seen = datetime.fromisoformat(str(record.get("last_seen")))
        age = (datetime.now(UTC) - seen).total_seconds()
        if age < 0 or age > USB_RECOVERY_MAX_CHECKIN_AGE_SECONDS:
            blockers.append("The device check-in is not recent enough")
    except (TypeError, ValueError):
        blockers.append("The device has no valid recent check-in")
    return blockers


def _advanced_health_metrics(
    record: dict[str, Any],
    profile: DeviceConfig,
    now: datetime | None = None,
) -> dict[str, Any]:
    current = now or datetime.now(UTC)
    history = [
        point
        for point in (record.get("checkin_history") or [])
        if isinstance(point, dict)
    ]

    wifi_points: list[tuple[datetime, float]] = []
    battery_points: list[tuple[datetime, float]] = []
    for point in history:
        try:
            observed = datetime.fromisoformat(str(point.get("at") or ""))
        except ValueError:
            continue
        rssi = point.get("rssi")
        if isinstance(rssi, (int, float)):
            wifi_points.append((observed, float(rssi)))
        battery = point.get("battery_percent")
        if isinstance(battery, (int, float)) and point.get("usb_connected") is not True:
            battery_points.append((observed, float(battery)))

    wifi_recent = wifi_points[-8:]
    wifi_average = (
        round(sum(value for _, value in wifi_recent) / len(wifi_recent), 1)
        if wifi_recent
        else None
    )
    wifi_delta = (
        round(wifi_recent[-1][1] - wifi_recent[0][1], 1)
        if len(wifi_recent) >= 2
        else None
    )
    wifi_trend = "unknown"
    if wifi_delta is not None:
        wifi_trend = (
            "improving"
            if wifi_delta >= 3
            else "declining"
            if wifi_delta <= -3
            else "stable"
        )

    battery_drain_per_day: float | None = None
    battery_runtime_hours: float | None = None
    if len(battery_points) >= 2:
        latest_at, latest_percent = battery_points[-1]
        baseline: tuple[datetime, float] | None = None
        for candidate in battery_points:
            elapsed = (latest_at - candidate[0]).total_seconds()
            if elapsed >= 1800 and candidate[1] > latest_percent:
                baseline = candidate
                break
        if baseline:
            elapsed_days = (latest_at - baseline[0]).total_seconds() / 86400
            dropped = baseline[1] - latest_percent
            if elapsed_days > 0 and dropped >= 0.5:
                battery_drain_per_day = round(dropped / elapsed_days, 2)
                if battery_drain_per_day > 0:
                    battery_runtime_hours = round(
                        min(8760, latest_percent / battery_drain_per_day * 24),
                        1,
                    )

    checkin_age_seconds: int | None = None
    missed_checkins = 0
    expected_interval = max(
        60,
        int(record.get("sleep_seconds") or profile.refresh_interval_seconds),
    )
    try:
        seen = datetime.fromisoformat(str(record.get("last_seen") or ""))
        checkin_age_seconds = max(0, int((current - seen).total_seconds()))
        if checkin_age_seconds > expected_interval * 1.5 + 60:
            missed_checkins = max(1, checkin_age_seconds // expected_interval - 1)
    except ValueError:
        pass

    return {
        "checkin_history_count": len(history),
        "checkin_age_seconds": checkin_age_seconds,
        "expected_checkin_interval_seconds": expected_interval,
        "missed_checkins": int(missed_checkins),
        "checkin_health": (
            "overdue"
            if missed_checkins >= 2
            else "late"
            if missed_checkins == 1
            else "on_time"
        ),
        "wifi_average_rssi": wifi_average,
        "wifi_trend_delta_db": wifi_delta,
        "wifi_trend": wifi_trend,
        "battery_drain_percent_per_day": battery_drain_per_day,
        "estimated_battery_runtime_hours": battery_runtime_hours,
        "sd_failure_events": int(record.get("sd_failure_events") or 0),
        "consecutive_sd_failures": int(record.get("consecutive_sd_failures") or 0),
        "reset_count": int(record.get("reset_count") or 0),
        "watchdog_reset_count": int(record.get("watchdog_reset_count") or 0),
        "panic_reset_count": int(record.get("panic_reset_count") or 0),
        "brownout_reset_count": int(record.get("brownout_reset_count") or 0),
    }


def _decorate_device(
    record: dict[str, Any],
    settings: BridgeConfig,
    store: DeviceStore | None = None,
    available_profiles: list[str] | None = None,
    available_policy_profiles: list[str] | None = None,
) -> dict[str, Any]:
    result = dict(record)
    descriptor = _device_capabilities(result)
    result["device_capabilities"] = descriptor.to_dict()
    result["device_family"] = descriptor.family
    result["firmware_provider"] = descriptor.firmware.provider
    result["supported_actions"] = list(descriptor.management.actions)
    result["identity"] = _device_identity(result)
    result.update(_display_runtime(result))
    last_seen = result.get("last_seen")
    online = False
    power_state = "offline"
    if isinstance(last_seen, str):
        try:
            seen = datetime.fromisoformat(last_seen)
            age = (datetime.now(UTC) - seen).total_seconds()
            profile = _effective_device(
                _capability_normalized_config(
                    settings.device(
                        str(result.get("device_id") or ""),
                        int(result.get("width") or 480),
                        int(result.get("height") or 800),
                        str(result.get("model") or ""),
                    ),
                    descriptor,
                ),
                result,
            )
            planned_sleep = _integer(
                str(result.get("sleep_seconds"))
                if result.get("sleep_seconds") is not None
                else None,
                0,
                0,
                86400,
            )
            online_window = max(
                180,
                int(profile.refresh_interval_seconds * 1.5) + 60,
                planned_sleep + 300,
            )
            online = age <= online_window
            sleep_action = str(result.get("sleep_action") or "")
            if sleep_action == "power_off":
                power_state = "powered_off"
            elif sleep_action == "scheduled":
                grace = (
                    0
                    if result.get("wake_reason") == "scheduled_timer"
                    else profile.manual_wake_grace_seconds
                )
                power_state = (
                    "awake" if age <= grace else ("sleeping" if online else "offline")
                )
            elif sleep_action == "awake":
                # Live/USB-kiosk devices intentionally report only on their
                # refresh interval. A quiet period longer than 90 seconds does
                # not mean they slept when their last explicit plan was to
                # remain awake.
                power_state = "awake" if online else "offline"
            else:
                power_state = (
                    "awake" if age <= 90 else ("sleeping" if online else "offline")
                )
        except ValueError:
            pass
    result["online"] = online
    result["power_state"] = power_state
    device_firmware = _device_firmware(settings, result)
    artifact_blockers = _firmware_artifact_blockers(result, device_firmware)
    result["firmware_artifact_family"] = descriptor.firmware.artifact_family
    result["firmware_artifact_compatible"] = bool(
        descriptor.firmware.manageable
        and not artifact_blockers
        and not _firmware_metadata_error(settings, device_firmware)
    )
    result["firmware_compatibility_blockers"] = artifact_blockers
    result["latest_firmware"] = device_firmware.version or result.get("firmware", "")
    profile = _effective_device(
        _capability_normalized_config(
            settings.device(
                str(result.get("device_id") or ""),
                int(result.get("width") or 480),
                int(result.get("height") or 800),
                str(result.get("model") or ""),
            ),
            descriptor,
        ),
        result,
    )
    result["name"] = profile.name
    result["area"] = profile.area
    management = descriptor.management
    if management.supports_dashboard_profiles:
        result["assigned_profile"] = profile.profile
    else:
        result.pop("assigned_profile", None)
    if management.modes:
        result["assigned_mode"] = profile.mode
    else:
        result.pop("assigned_mode", None)
    if management.supports_provisioning:
        result["assigned_auto_start"] = profile.auto_start
        result["assigned_refresh_interval_seconds"] = profile.refresh_interval_seconds
    else:
        result.pop("assigned_auto_start", None)
        result.pop("assigned_refresh_interval_seconds", None)
    if management.supports_sleep_policy:
        result.update(
            {
                "assigned_live_mode": profile.live_mode,
                "assigned_manual_sleep_seconds": profile.manual_sleep_seconds,
                "assigned_intelligent_sleep": profile.intelligent_sleep,
                "assigned_active_start": profile.active_start,
                "assigned_active_end": profile.active_end,
                "assigned_timezone": profile.timezone,
                "assigned_stay_awake_on_usb": profile.stay_awake_on_usb,
                "assigned_manual_wake_grace_seconds": (
                    profile.manual_wake_grace_seconds
                ),
            }
        )
    else:
        for field in (
            "assigned_live_mode",
            "assigned_manual_sleep_seconds",
            "assigned_intelligent_sleep",
            "assigned_active_start",
            "assigned_active_end",
            "assigned_timezone",
            "assigned_stay_awake_on_usb",
            "assigned_manual_wake_grace_seconds",
        ):
            result.pop(field, None)
        if management.supports_provisioning:
            # Known always-on receivers retain their effective overlay for
            # diagnostics, while the capability contract keeps these values
            # out of editable sleep-policy surfaces.
            result["assigned_live_mode"] = profile.live_mode
            result["assigned_intelligent_sleep"] = profile.intelligent_sleep
    if management.supports_battery_policy:
        result.update(
            {
                "assigned_critical_battery_percent": profile.critical_battery_percent,
                "assigned_low_battery_percent": profile.low_battery_percent,
                "assigned_low_battery_multiplier": profile.low_battery_multiplier,
            }
        )
    else:
        for field in (
            "assigned_critical_battery_percent",
            "assigned_low_battery_percent",
            "assigned_low_battery_multiplier",
        ):
            result.pop(field, None)
    if management.supports_rendering_profile:
        result["assigned_rendering_profile"] = profile.rendering_profile
        result["assigned_unchanged_image_multiplier"] = (
            profile.unchanged_image_multiplier
        )
    else:
        result.pop("assigned_rendering_profile", None)
        result.pop("assigned_unchanged_image_multiplier", None)
    if management.supports_opendisplay_policy:
        result["assigned_open_display_transport_policy"] = (
            profile.open_display_transport_policy
        )
    else:
        result.pop("assigned_open_display_transport_policy", None)
    desired_revision = int(result.get("assigned_policy_revision") or 0)
    reported_revision = int(result.get("reported_policy_revision") or 0)
    result["assigned_policy_name"] = str(result.get("assigned_policy_name") or "custom")
    result["policy_revision"] = desired_revision
    result["reported_policy_revision"] = reported_revision
    result["policy_sync_state"] = (
        "synced"
        if desired_revision > 0 and desired_revision == reported_revision
        else "pending"
        if desired_revision > reported_revision
        else "not_managed"
        if desired_revision == 0
        else "mismatch"
    )
    result["available_policy_profiles"] = available_policy_profiles or list(
        FLEET_POLICY_PRESETS
    )
    result.update(_advanced_health_metrics(result, profile))
    maintenance = _firmware_maintenance_status(settings)
    result["firmware_maintenance_enabled"] = maintenance["enabled"]
    result["firmware_maintenance_window_open"] = maintenance["open"]
    result["firmware_maintenance_start"] = maintenance["start"]
    result["firmware_maintenance_end"] = maintenance["end"]
    result["firmware_maintenance_timezone"] = maintenance["timezone"]
    result["firmware_maintenance_next_start"] = maintenance["next_start"]
    result["firmware_maintenance_usb_override"] = maintenance["usb_override"]
    result["available_profiles"] = available_profiles or list(settings.profiles)
    result["available_modes"] = list(descriptor.management.modes)
    result["update_available"] = bool(
        device_firmware.version
        and device_firmware.url
        and _firmware_version(device_firmware.version)
        > _firmware_version(str(result.get("firmware") or ""))
    )
    if store:
        blockers = _firmware_install_blockers(result, settings, store)
        rollout = store.firmware_rollout()
        result["firmware_install_blockers"] = blockers
        result["firmware_install_ready"] = result["update_available"] and not blockers
        result["firmware_rollout_status"] = rollout.get("status") or "not_started"
        result["firmware_canary_device_id"] = rollout.get("canary_device_id")
        result["firmware_canary_verified"] = (
            rollout.get("target_version") == device_firmware.version
            and rollout.get("status") == "canary_verified"
        )
        retry_blockers = _firmware_retry_blockers(result, settings, store)
        result["firmware_retry_blockers"] = retry_blockers
        result["firmware_retry_ready"] = (
            result["update_available"]
            and result.get("firmware_update_status") in {"failed", "cancelled"}
            and not retry_blockers
        )
        result["firmware_retry_limit"] = device_firmware.retry_limit
        result["firmware_retry_backoff_seconds"] = (
            device_firmware.retry_backoff_seconds
        )
        result["firmware_rollout_reset_ready"] = (
            descriptor.supports_xteink_ota
            and rollout.get("target_version") == settings.firmware.version
            and rollout.get("status") in {"failed", "canary_active"}
        )
        recovery_blockers = _usb_recovery_blockers(result, settings, store)
        result["usb_recovery_verification_blockers"] = recovery_blockers
        result["usb_recovery_verification_ready"] = not recovery_blockers
    battery = _number(
        str(result.get("battery_percent"))
        if result.get("battery_percent") is not None
        else None
    )
    result["low_battery"] = bool(
        battery is not None and battery <= profile.low_battery_percent
    )
    health_issues: list[str] = []
    if not online and power_state != "powered_off":
        health_issues.append("offline")
    if result.get("sd_ready") is False and not _is_android_display(result):
        health_issues.append("sd_card")
    if (
        result.get("sd_writable") is False
        and result.get("sd_ready") is True
        and not _is_android_display(result)
    ):
        health_issues.append("sd_write")
    if result.get("ha_error"):
        health_issues.append("home_assistant")
    if result.get("image_conversion_error"):
        health_issues.append("image_conversion")
    if result.get("dashboard_fetch_error"):
        health_issues.append("dashboard_fetch")
    if result.get("firmware_update_status") in {"failed", "cancelled"}:
        health_issues.append("firmware_update")
    if result["low_battery"]:
        health_issues.append("low_battery")
    if result.get("missed_checkins", 0) >= 2:
        health_issues.append("checkin_overdue")
    if (
        result.get("wifi_average_rssi") is not None
        and result["wifi_average_rssi"] <= -80
    ):
        health_issues.append("weak_wifi")
    if (
        result.get("consecutive_sd_failures", 0) >= 2
        and not _is_android_display(result)
    ):
        health_issues.append("repeated_sd_failure")
    if str(result.get("reset_reason") or "") in {
        "panic",
        "interrupt_watchdog",
        "task_watchdog",
        "watchdog",
        "brownout",
    }:
        health_issues.append("problem_reset")
    if health_issues:
        result["health_state"] = (
            "offline" if health_issues == ["offline"] else "needs_attention"
        )
    elif power_state in {"sleeping", "powered_off"}:
        result["health_state"] = power_state
    else:
        result["health_state"] = "healthy"
    result["health_issues"] = health_issues
    result["health_detail"] = ", ".join(health_issues) if health_issues else "No issues"
    result.pop("receiver_token_sha256", None)
    return result


def _effective_device(base: DeviceConfig, record: dict[str, Any]) -> DeviceConfig:
    selected = replace(
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
        intelligent_sleep=bool(
            record.get("assigned_intelligent_sleep", base.intelligent_sleep)
        ),
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
        stay_awake_on_usb=bool(
            record.get("assigned_stay_awake_on_usb", base.stay_awake_on_usb)
        ),
        manual_wake_grace_seconds=_integer(
            str(record.get("assigned_manual_wake_grace_seconds"))
            if record.get("assigned_manual_wake_grace_seconds") is not None
            else None,
            base.manual_wake_grace_seconds,
            0,
            600,
        ),
        rendering_profile=(
            str(record.get("assigned_rendering_profile") or base.rendering_profile)
            if str(record.get("assigned_rendering_profile") or base.rendering_profile)
            in {"standard", "photo"}
            else "standard"
        ),
        open_display_transport_policy=(
            str(
                record.get("assigned_open_display_transport_policy")
                or base.open_display_transport_policy
            )
            if str(
                record.get("assigned_open_display_transport_policy")
                or base.open_display_transport_policy
            )
            in {"auto", "lan_preferred", "ble_only"}
            else "auto"
        ),
    )
    if _is_always_on_color_display(record):
        # Always-powered colour panels use push invalidation when available and
        # a one-minute safety poll. Battery schedules and unchanged-image
        # multipliers must not make an interactive wall dashboard appear stale.
        return replace(
            selected,
            refresh_interval_seconds=min(selected.refresh_interval_seconds, 60),
            live_mode=True,
            intelligent_sleep=False,
            stay_awake_on_usb=True,
            low_battery_multiplier=1,
            unchanged_image_multiplier=1,
            open_display_transport_policy="lan_preferred",
        )
    return selected


def _header_value(value: Any) -> str:
    selected = str(value or "").replace("\r", " ").replace("\n", " ")[:160]
    return selected.encode("latin-1", errors="replace").decode("latin-1")


def _is_x3_model(model: str) -> bool:
    normalized = re.sub(r"[^A-Z0-9]", "", str(model or "").upper())
    return (
        normalized == "X3"
        or normalized.endswith("XTEINKX3")
        or normalized in {"N4", "NOTE4", "ZECTRIXNOTE4"}
    )


def _device_screen_payload(
    image: bytes,
    media_type: str,
    model: str,
) -> tuple[bytes, str]:
    """Return a payload the target can decode without runtime conversion.

    The X3's smaller fragmented heap cannot reliably allocate the PNG inflate
    window after the reader and networking stacks have started.  Deliver a
    native 1-bit BMP to X3 devices while keeping the compressed PNG path for
    X4 devices.
    """
    normalized_media_type = str(media_type or "").split(";", 1)[0].strip().lower()
    if normalized_media_type != "image/png" or not _is_x3_model(model):
        return image, media_type

    output = io.BytesIO()
    with Image.open(io.BytesIO(image)) as source:
        source.convert("1").save(output, format="BMP")
    return output.getvalue(), "image/bmp"


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


async def _bounded_request_body(
    request: Request, maximum: int, too_large_detail: str
) -> bytes:
    """Read an HTTP body without allowing chunked requests to bypass the cap."""
    supplied_length = request.headers.get("content-length")
    if supplied_length not in (None, ""):
        try:
            content_length = int(supplied_length)
        except ValueError as err:
            raise HTTPException(status_code=400, detail="Invalid Content-Length") from err
        if content_length < 0:
            raise HTTPException(status_code=400, detail="Invalid Content-Length")
        if content_length > maximum:
            raise HTTPException(status_code=413, detail=too_large_detail)
    content = bytearray()
    async for chunk in request.stream():
        if len(content) + len(chunk) > maximum:
            raise HTTPException(status_code=413, detail=too_large_detail)
        content.extend(chunk)
    return bytes(content)


def _boolean(value: str | None) -> bool | None:
    if value is None:
        return None
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _capabilities(value: str | None) -> set[str]:
    return {
        selected.strip().lower()
        for selected in str(value or "").split(",")
        if selected.strip()
    }


def _clock_minutes(value: str, fallback: int) -> int:
    try:
        hour, minute = (int(part) for part in value.split(":", 1))
    except (TypeError, ValueError):
        return fallback
    return hour * 60 + minute if 0 <= hour <= 23 and 0 <= minute <= 59 else fallback


def _provisioning_assignment(
    payload: dict[str, Any],
    selected: str,
    available_profiles: list[str],
) -> dict[str, Any]:
    assignment: dict[str, Any] = {}
    if "name" in payload:
        assignment["assigned_name"] = _header_value(payload["name"]) or selected
    if "area" in payload:
        assignment["assigned_area"] = _header_value(payload["area"])
    if "profile" in payload:
        profile = str(payload["profile"])
        if profile not in available_profiles:
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
            str(payload["refresh_interval_seconds"]), 900, 60, 86400
        )
    if "manual_sleep_seconds" in payload:
        assignment["assigned_manual_sleep_seconds"] = _integer(
            str(payload["manual_sleep_seconds"]), 900, 60, 86400
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
    if "rendering_profile" in payload:
        rendering_profile = str(payload["rendering_profile"]).strip().lower()
        if rendering_profile not in {"standard", "photo"}:
            raise HTTPException(status_code=400, detail="Unsupported rendering profile")
        assignment["assigned_rendering_profile"] = rendering_profile
    if "open_display_transport_policy" in payload:
        transport_policy = str(payload["open_display_transport_policy"]).strip().lower()
        if transport_policy not in {"auto", "lan_preferred", "ble_only"}:
            raise HTTPException(
                status_code=400, detail="Unsupported OpenDisplay transport policy"
            )
        assignment["assigned_open_display_transport_policy"] = transport_policy
    return assignment


def _filter_provisioning_assignment(
    assignment: dict[str, Any],
    descriptor: DeviceCapabilityDescriptor,
) -> tuple[dict[str, Any], list[str]]:
    """Remove fields a device family cannot apply and report what was skipped."""
    selected = dict(assignment)
    skipped: list[str] = []

    def remove(fields: set[str]) -> None:
        for field in fields:
            if field in selected:
                selected.pop(field, None)
                skipped.append(field.removeprefix("assigned_"))

    if not descriptor.management.supports_provisioning:
        remove(set(selected))
        return selected, skipped
    mode = str(selected.get("assigned_mode") or "")
    if mode and mode not in descriptor.management.modes:
        remove({"assigned_mode"})
    if not descriptor.management.supports_dashboard_profiles:
        remove({"assigned_profile"})
    if not descriptor.management.supports_battery_policy:
        remove(
            {
                "assigned_critical_battery_percent",
                "assigned_low_battery_percent",
                "assigned_low_battery_multiplier",
            }
        )
    if not descriptor.management.supports_sleep_policy:
        remove(
            {
                "assigned_manual_sleep_seconds",
                "assigned_intelligent_sleep",
                "assigned_active_start",
                "assigned_active_end",
                "assigned_timezone",
                "assigned_stay_awake_on_usb",
                "assigned_manual_wake_grace_seconds",
            }
        )
    if not descriptor.management.supports_rendering_profile:
        remove(
            {
                "assigned_rendering_profile",
                "assigned_unchanged_image_multiplier",
            }
        )
    if not descriptor.management.supports_opendisplay_policy:
        remove({"assigned_open_display_transport_policy"})
    return selected, skipped


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
    if (
        battery_percent is not None
        and battery_percent <= profile.critical_battery_percent
    ):
        return plan("power_off", 0, "critical_battery")

    start = _clock_minutes(profile.active_start, 6 * 60)
    end = _clock_minutes(profile.active_end, 22 * 60)
    minute = local.hour * 60 + local.minute
    always_active = start == end
    active = always_active or (
        start <= minute < end if start < end else minute >= start or minute < end
    )

    if not active:
        next_start = datetime.combine(
            local.date(), time(start // 60, start % 60), tzinfo=zone
        )
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
        reason = (
            "unchanged_image" if reason == "refresh_interval" else f"{reason}_unchanged"
        )
    return plan("scheduled", max(60, seconds), reason)


def _button_events(
    value: str | None, default_mode: str = BUTTON_ACTION_MODE
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for encoded in (value or "").split(";"):
        parts = encoded.split(",")
        if (
            len(parts) not in {4, 5, 6}
            or parts[1] not in SUPPORTED_BUTTONS
            or parts[2] != "pressed"
        ):
            continue
        gesture = parts[4] if len(parts) >= 5 else "short"
        mode = parts[5] if len(parts) >= 6 else default_mode
        if gesture not in GESTURES or mode not in SUPPORTED_MODES:
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
                "gesture": gesture,
                "mode": mode,
            }
        )
    return result[:16]


def _new_button_events(
    record: dict[str, Any], events: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    known = {
        (
            int(event.get("sequence") or 0),
            str(event.get("button") or ""),
            int(event.get("uptime_ms") or 0),
        )
        for event in record.get("recent_button_events") or []
    }
    return [
        event
        for event in events
        if (
            int(event.get("sequence") or 0),
            str(event.get("button") or ""),
            int(event.get("uptime_ms") or 0),
        )
        not in known
    ]


def _dispatch_button_actions(
    device_id: str,
    record: dict[str, Any],
    events: list[dict[str, Any]],
    store: DeviceStore,
    ha: HomeAssistantClient,
) -> tuple[list[str], dict[str, Any]]:
    """Resolve new gesture events, execute HA calls, and return page commands."""
    navigation: list[str] = []
    mappings = record.get("button_action_mappings")
    if not isinstance(mappings, dict):
        mappings = {}
    latest = record
    for event in events:
        action = resolve_action(
            mappings,
            str(event.get("button") or ""),
            str(event.get("gesture") or "short"),
            str(event.get("mode") or BUTTON_ACTION_MODE),
        )
        action_type = str(action.get("type") or "none")
        if action_type == "navigation":
            command = str(action.get("command") or "")
            navigation.append(command)
            success, detail = True, f"navigation:{command}"
        elif action_type == "home_assistant":
            success, detail = ha.call_service(
                str(action.get("service") or ""),
                str(action.get("entity_id") or ""),
                action.get("data") if isinstance(action.get("data"), dict) else None,
            )
        else:
            success, detail = True, "no action"
        latest = (
            store.record_button_action_result(device_id, event, action, success, detail)
            or latest
        )
    return navigation, latest


def _button_action_activation(record: dict[str, Any]) -> dict[str, Any]:
    current_mode = str(record.get("mode") or "unknown")
    active_mode = current_mode == BUTTON_ACTION_MODE
    return {
        "saved_on_bridge": True,
        "active_now": active_mode,
        "status": "ready" if active_mode else "waiting_for_home_assistant_mode",
        "current_mode": current_mode,
        "applies_in_mode": BUTTON_ACTION_MODE,
        "requires_device_sync": False,
        "updated_at": record.get("button_actions_updated_at"),
        "last_seen": record.get("last_seen"),
        "last_executed_at": record.get("last_button_action_at"),
        "last_result": record.get("last_button_action_result"),
    }


def _number(value: str | None) -> float | None:
    try:
        return float(value) if value not in (None, "") else None
    except ValueError:
        return None


def _device_id(value: str | None) -> str:
    selected = str(value or "").strip()
    if not selected:
        raise HTTPException(status_code=400, detail="X-FlexDisplay-ID is required")
    if not DEVICE_ID_PATTERN.fullmatch(selected):
        raise HTTPException(status_code=400, detail="Invalid X-FlexDisplay-ID")
    return selected


def _valid_command(command: str) -> bool:
    return command in SUPPORTED_COMMANDS or bool(
        re.fullmatch(r"page-[1-9][0-9]?", command)
    )


def _auto_rotate_due(record: dict[str, Any], seconds: int) -> bool:
    if seconds <= 0:
        return False
    changed_at = record.get("dashboard_page_changed_at")
    if not isinstance(changed_at, str):
        return False
    try:
        return (
            datetime.now(UTC) - datetime.fromisoformat(changed_at)
        ).total_seconds() >= seconds
    except ValueError:
        return False


def create_app(config: BridgeConfig | None = None) -> FastAPI:
    settings = config or load_config()
    store = DeviceStore(settings.state_path)
    dashboards = DashboardProfileStore(
        settings.state_path.with_name("flexdisplay-dashboards.json"),
        settings.profiles,
        settings.default_profile,
    )
    dashboard_assets = DashboardAssetStore(
        settings.state_path.with_name("dashboard-assets")
    )
    photo_frames = PhotoFrameMediaStore(
        settings.state_path.with_name("flexdisplay-photo-frame.json")
    )
    loading_screens = LoadingScreenStore(
        settings.state_path.with_name("flexdisplay-loading-screens.json")
    )
    content_packs = ContentPackStore(
        settings.state_path.with_name("flexdisplay-content-packs.json"),
        settings.state_path.with_name("content-packs"),
    )
    content_channels = ContentChannelStore(
        settings.state_path.with_name("flexdisplay-content-channels.json")
    )
    packaged_firmware = os.getenv("FLEXDISPLAY_PACKAGED_FIRMWARE", "").strip()
    firmware_mirror = FirmwareMirror(
        settings.state_path.with_name("firmware-cache"),
        Path(packaged_firmware) if packaged_firmware else None,
    )
    flexhub = FlexHubClient(
        settings.state_path.with_name("flexdisplay-flexhub.json"),
        default_url=settings.flexhub.url,
        default_access_pin=settings.flexhub.access_pin,
        timeout_seconds=settings.flexhub.timeout_seconds,
    )
    meshtastic_console = MeshtasticConsoleStore(
        settings.state_path.with_name("flexdisplay-meshtastic-console.json")
    )
    screen_history = ScreenHistoryStore(
        settings.state_path.with_name("screen-history"),
        settings.screen_history.limit,
    )
    ha = HomeAssistantClient(settings.home_assistant)
    voice_assistant = HomeAssistantVoiceClient(settings.home_assistant)
    renderer = DashboardRenderer()
    rook = RookBroker()

    def fleet_policy_profiles() -> dict[str, dict[str, Any]]:
        profiles = {
            profile_id: {**preset, "id": profile_id, "built_in": True}
            for profile_id, preset in FLEET_POLICY_PRESETS.items()
        }
        for profile_id, profile in store.custom_policy_profiles().items():
            profiles[profile_id] = {**profile, "id": profile_id, "built_in": False}
        return profiles

    def fleet_scope_records(
        scope: str,
        requested_ids: set[str],
        group_id: str = "",
    ) -> list[dict[str, Any]]:
        if scope not in {"all", "x3", "x4", "devices", "group"}:
            raise HTTPException(status_code=400, detail="Unsupported fleet scope")
        if scope == "devices" and not requested_ids:
            raise HTTPException(status_code=400, detail="Select at least one device")
        selected_group: dict[str, Any] = {}
        group_ids: set[str] = set()
        group_filters: dict[str, Any] = {}
        if scope == "group":
            selected_group = store.fleet_groups().get(group_id) or {}
            if not selected_group:
                raise HTTPException(status_code=404, detail="Fleet group not found")
            group_ids = {
                str(device_id) for device_id in selected_group.get("device_ids") or []
            }
            group_filters = (
                selected_group.get("filters")
                if isinstance(selected_group.get("filters"), dict)
                else {}
            )

        def group_matches(record: dict[str, Any]) -> bool:
            device_id = str(record.get("device_id") or "")
            if device_id in group_ids:
                return True
            if not group_filters:
                return False
            descriptor = _device_capabilities(record)
            values: dict[str, Any] = {
                "family": descriptor.family,
                "firmware_provider": descriptor.firmware.provider,
                "model_key": descriptor.model_key,
                "area": str(record.get("assigned_area") or record.get("area") or ""),
                "power_class": descriptor.power.power_class,
            }
            if "online" in group_filters:
                values["online"] = bool(
                    _decorate_device(record, settings, store).get("online")
                )
            return all(values.get(key) == value for key, value in group_filters.items())

        records: list[dict[str, Any]] = []
        for record in store.all():
            device_id = str(record.get("device_id") or "")
            descriptor = _device_capabilities(record)
            if (
                scope == "all"
                or (scope == "x3" and descriptor.model_key == "x3")
                or (scope == "x4" and descriptor.model_key == "x4")
                or (scope == "devices" and device_id in requested_ids)
                or (scope == "group" and group_matches(record))
            ):
                records.append(record)
        return records

    def group_payload(group: dict[str, Any]) -> dict[str, Any]:
        resolved = fleet_scope_records("group", set(), str(group.get("id") or ""))
        return {
            **group,
            "resolved_device_ids": [str(item.get("device_id") or "") for item in resolved],
            "resolved_count": len(resolved),
        }

    def advance_firmware_rollout() -> dict[str, Any]:
        rollout = store.firmware_rollout()
        target = settings.firmware.version
        planned = [str(item) for item in (rollout.get("planned_devices") or [])]
        result: dict[str, Any] = {
            "queued": [],
            "blocked": {},
            "complete": [],
            "rollout": rollout,
        }
        if (
            not planned
            or rollout.get("target_version") != target
            or not rollout.get("auto_continue")
        ):
            return result
        if rollout.get("status") == "failed":
            result["blocked"]["rollout"] = "Rollout is paused after a failure"
            return result

        for device_id in planned:
            record = store.get(device_id)
            if not record:
                result["blocked"][device_id] = "Device has not checked in"
                continue
            if not _device_capabilities(record).supports_xteink_ota:
                result["blocked"][device_id] = (
                    "Device is not eligible for the X3/X4 firmware channel"
                )
                continue
            if _firmware_version(
                str(record.get("firmware") or "")
            ) >= _firmware_version(target):
                result["complete"].append(device_id)
                continue
            if "install" in (record.get("pending_commands") or []) or "install" in (
                record.get("dispatched_commands") or []
            ):
                result["queued"].append(device_id)
                continue
            blockers = _firmware_install_blockers(record, settings, store)
            if blockers:
                result["blocked"][device_id] = "; ".join(blockers)
                continue
            try:
                store.queue_firmware_install(
                    device_id,
                    target,
                    canary_required=settings.firmware.canary_required,
                    max_parallel=settings.firmware.max_parallel,
                )
                result["queued"].append(device_id)
            except ValueError as err:
                result["blocked"][device_id] = str(err)
        result["rollout"] = store.firmware_rollout()
        return result

    def deliver_screen(
        device_id: str,
        image: bytes,
        media_type: str,
        response: Response,
        *,
        image_unchanged: bool,
        image_cached: bool,
        capabilities: set[str],
        uncompressed_bytes: int,
    ) -> Response:
        empty_unchanged = (
            image_unchanged and image_cached and "empty-unchanged" in capabilities
        )
        content = b"" if empty_unchanged else image
        transfer_format = (
            "empty-unchanged"
            if empty_unchanged
            else media_type.rsplit("/", 1)[-1].lower()
        )
        baseline = max(len(image), int(uncompressed_bytes))
        saved = max(0, baseline - len(content))
        savings_percent = round(saved * 100 / baseline) if baseline else 0
        response.headers["X-FlexDisplay-Transfer-Encoding"] = transfer_format
        response.headers["X-FlexDisplay-Transfer-Bytes"] = str(len(content))
        response.headers["X-FlexDisplay-Uncompressed-Bytes"] = str(baseline)
        response.headers["X-FlexDisplay-Transfer-Saved-Bytes"] = str(saved)
        response.headers["X-FlexDisplay-Transfer-Savings"] = str(savings_percent)
        response.headers["X-FlexDisplay-Transfer-Optimized"] = (
            "true" if empty_unchanged or media_type == "image/png" else "false"
        )
        store.touch(
            device_id,
            {
                "last_transfer_encoding": transfer_format,
                "last_transfer_bytes": len(content),
                "last_transfer_uncompressed_bytes": baseline,
                "last_transfer_saved_bytes": saved,
                "last_transfer_savings_percent": savings_percent,
                "last_transfer_optimized": bool(
                    empty_unchanged or media_type == "image/png"
                ),
                "last_transfer_at": datetime.now(UTC).isoformat(timespec="seconds"),
            },
        )
        publish_current(device_id)
        return Response(
            content=content,
            media_type=media_type,
            headers=dict(response.headers),
        )

    def fetch_dashboard_entities(
        configured_entities: tuple[EntityConfig, ...],
    ) -> tuple[list[Any], str]:
        states, error = ha.fetch(configured_entities)
        by_id = {entity.entity_id: entity for entity in configured_entities}
        hydrated = []
        for state in states:
            configured = by_id.get(state.entity_id)
            if configured and configured.style == "name_card":
                hydrated.append(
                    replace(
                        state,
                        image_bytes=dashboard_assets.profile_photo(
                            configured.badge_photo_id
                        ),
                        badge_theme=configured.badge_theme,
                    )
                )
            else:
                hydrated.append(state)
        return hydrated, error

    def publish_current(device_id: str) -> None:
        current = store.get(device_id)
        if not current:
            return
        configured = settings.device(
            device_id,
            int(current.get("width") or 480),
            int(current.get("height") or 800),
            str(current.get("model") or ""),
        )
        profile = _effective_device(configured, current)
        decorated = _decorate_device(
            current,
            settings,
            store,
            dashboards.names(),
            list(fleet_policy_profiles()),
        )
        decorated["screen_history_count"] = len(screen_history.list(device_id))
        mqtt.publish_device(device_id, profile, decorated)

    def publish_screen_preview(
        device_id: str,
        content: bytes,
        media_type: str,
    ) -> None:
        if not mqtt.discovery_enabled:
            return
        preview = content
        if media_type != "image/png":
            output = io.BytesIO()
            with Image.open(io.BytesIO(content)) as source:
                source.convert("1").save(output, format="PNG", optimize=True)
            preview = output.getvalue()
        mqtt.publish_screen(device_id, preview)

    def queue_firmware_for_device(
        device_id: str, current: dict[str, Any]
    ) -> dict[str, Any]:
        """Queue the release owned by the device's trusted firmware provider."""
        descriptor = _device_capabilities(current)
        if not descriptor.firmware.manageable:
            raise ValueError(
                "Firmware installation is not managed by the Bridge for this device family"
            )
        blockers = _firmware_install_blockers(current, settings, store)
        if blockers:
            raise ValueError("; ".join(blockers))
        firmware = _device_firmware(settings, current)
        if descriptor.firmware.provider == "note4":
            return store.queue_device_firmware_install(
                device_id,
                firmware.version,
                firmware_provider=descriptor.firmware.provider,
                artifact_family=descriptor.firmware.artifact_family,
            )
        if descriptor.firmware.artifact_family == "x4pro_s3":
            return store.queue_device_firmware_install(
                device_id,
                firmware.version,
                firmware_provider=descriptor.firmware.provider,
                artifact_family=descriptor.firmware.artifact_family,
            )
        if descriptor.supports_xteink_ota:
            return store.queue_firmware_install(
                device_id,
                firmware.version,
                canary_required=firmware.canary_required,
                max_parallel=firmware.max_parallel,
            )
        raise ValueError("No trusted firmware provider is configured for this device")

    def queue_from_mqtt(device_id: str, command: str, payload: str) -> None:
        if device_id == "flexhub":
            try:
                if command == "send-meshtastic":
                    selected = payload.strip()
                    if selected.startswith("{"):
                        parsed = json.loads(selected)
                        if not isinstance(parsed, dict):
                            raise ValueError("Meshtastic command must be a JSON object")
                    else:
                        parsed = {
                            "text": selected,
                            "destination": "broadcast",
                            "channel": 0,
                            "request_ack": False,
                        }
                    normalized = FlexHubClient.normalize_meshtastic_message(parsed)
                    flexhub.send_meshtastic_message(normalized)
                elif command == "clear-meshtastic-unread":
                    flexhub.mark_meshtastic_read()
                elif command in {"scan", "deliver", "retry", "cancel"}:
                    flexhub.action(command)
                else:
                    raise ValueError("Unsupported FlexHub MQTT command")
            except (json.JSONDecodeError, FlexHubClientError, ValueError) as exc:
                LOGGER.warning("FlexHub MQTT %s failed: %s", command, str(exc)[:180])
                publisher = getattr(mqtt, "publish_flexhub_message", None)
                if callable(publisher) and command == "send-meshtastic":
                    publisher(
                        {
                            "text": payload[:220],
                            "direction": "outbound",
                            "status": "failed",
                            "error": str(exc)[:180],
                        }
                    )
            return
        if not DEVICE_ID_PATTERN.fullmatch(device_id):
            return
        current = store.get(device_id)
        if not current:
            return
        descriptor = _device_capabilities(current)
        try:
            if command in SUPPORTED_COMMANDS:
                if command not in descriptor.management.actions:
                    raise ValueError(
                        "Command is not supported by this device family"
                    )
                if command == "install":
                    queue_firmware_for_device(device_id, current)
                else:
                    store.queue_command(device_id, command)
            elif command == "cancel":
                store.clear_commands(device_id, include_dispatched=True)
            elif command == "set-frontlight-on":
                if not descriptor.frontlight.supports_on:
                    raise ValueError(
                        "Frontlight power is not supported by this hardware revision"
                    )
                store.touch(
                    device_id,
                    {
                        "desired_frontlight_on": payload.strip().lower()
                        in {"1", "true", "yes", "on"}
                    },
                )
                store.queue_command(device_id, "refresh")
            elif command in {
                "set-frontlight-brightness",
                "set-frontlight-warmth",
            }:
                supported = (
                    descriptor.frontlight.supports_brightness
                    if command == "set-frontlight-brightness"
                    else descriptor.frontlight.supports_warmth
                )
                if not supported:
                    raise ValueError(
                        "Frontlight control is not supported by this hardware revision"
                    )
                try:
                    value = int(float(payload))
                except ValueError as err:
                    raise ValueError("A numeric value is required") from err
                if not descriptor.frontlight.minimum <= value <= descriptor.frontlight.maximum:
                    raise ValueError(
                        "Value must be between "
                        f"{descriptor.frontlight.minimum} and "
                        f"{descriptor.frontlight.maximum}"
                    )
                field = (
                    "desired_frontlight_brightness"
                    if command == "set-frontlight-brightness"
                    else "desired_frontlight_warmth"
                )
                store.touch(device_id, {field: value})
                store.queue_command(device_id, "refresh")
            elif command == "firmware-retry":
                if not descriptor.supports_xteink_ota:
                    raise ValueError(
                        "Firmware retry is not supported by this device family"
                    )
                blockers = _firmware_retry_blockers(current, settings, store)
                if blockers:
                    raise ValueError("; ".join(blockers))
                store.retry_firmware_install(
                    device_id,
                    settings.firmware.version,
                    canary_required=settings.firmware.canary_required,
                    max_parallel=settings.firmware.max_parallel,
                    retry_limit=settings.firmware.retry_limit,
                    retry_backoff_seconds=settings.firmware.retry_backoff_seconds,
                )
            elif command == "rollout-reset":
                if not descriptor.supports_xteink_ota:
                    raise ValueError(
                        "Firmware rollout reset is not supported by this device family"
                    )
                store.reset_firmware_rollout(
                    settings.firmware.version,
                    canary_required=settings.firmware.canary_required,
                )
            elif command == "resend-screen":
                if (
                    "refresh" not in descriptor.management.actions
                    or not descriptor.management.supports_screen_history
                ):
                    raise ValueError(
                        "Screen history is not supported by this device family"
                    )
                _, item = screen_history.latest(device_id)
                store.set_screen_override(device_id, str(item["id"]))
                store.queue_command(device_id, "refresh")
            elif command in {
                "set-auto-start",
                "set-live-mode",
                "set-intelligent-sleep",
                "set-stay-awake-on-usb",
            }:
                if (
                    command in {"set-auto-start", "set-live-mode"}
                    and not descriptor.management.supports_fleet_policy
                ):
                    raise ValueError(
                        "Fleet settings are not supported by this device family"
                    )
                if (
                    command in {"set-intelligent-sleep", "set-stay-awake-on-usb"}
                    and not descriptor.management.supports_sleep_policy
                ):
                    raise ValueError(
                        "Sleep settings are not supported by this device family"
                    )
                field = {
                    "set-auto-start": "assigned_auto_start",
                    "set-live-mode": "assigned_live_mode",
                    "set-intelligent-sleep": "assigned_intelligent_sleep",
                    "set-stay-awake-on-usb": "assigned_stay_awake_on_usb",
                }[command]
                store.provision(
                    device_id,
                    {field: payload.strip().lower() in {"1", "true", "yes", "on"}},
                )
                store.queue_command(device_id, "refresh")
            elif command in {
                "set-refresh-interval",
                "set-manual-sleep",
                "set-critical-battery",
                "set-low-battery",
                "set-low-battery-multiplier",
                "set-unchanged-multiplier",
                "set-manual-wake-grace",
            }:
                if (
                    command == "set-refresh-interval"
                    and not descriptor.management.supports_fleet_policy
                ):
                    raise ValueError(
                        "Fleet settings are not supported by this device family"
                    )
                if command in {
                    "set-critical-battery",
                    "set-low-battery",
                    "set-low-battery-multiplier",
                } and not descriptor.management.supports_battery_policy:
                    raise ValueError(
                        "Battery settings are not supported by this device family"
                    )
                if command in {
                    "set-manual-sleep",
                    "set-manual-wake-grace",
                } and not descriptor.management.supports_sleep_policy:
                    raise ValueError(
                        "Sleep settings are not supported by this device family"
                    )
                if (
                    command == "set-unchanged-multiplier"
                    and not descriptor.management.supports_rendering_profile
                ):
                    raise ValueError(
                        "Rendering settings are not supported by this device family"
                    )
                field, minimum, maximum = {
                    "set-refresh-interval": (
                        "assigned_refresh_interval_seconds",
                        60,
                        86400,
                    ),
                    "set-manual-sleep": (
                        "assigned_manual_sleep_seconds",
                        60,
                        86400,
                    ),
                    "set-critical-battery": (
                        "assigned_critical_battery_percent",
                        5,
                        50,
                    ),
                    "set-low-battery": (
                        "assigned_low_battery_percent",
                        10,
                        80,
                    ),
                    "set-low-battery-multiplier": (
                        "assigned_low_battery_multiplier",
                        1,
                        12,
                    ),
                    "set-unchanged-multiplier": (
                        "assigned_unchanged_image_multiplier",
                        1,
                        12,
                    ),
                    "set-manual-wake-grace": (
                        "assigned_manual_wake_grace_seconds",
                        0,
                        600,
                    ),
                }[command]
                try:
                    value = int(float(payload))
                except ValueError as err:
                    raise ValueError("A numeric value is required") from err
                if not minimum <= value <= maximum:
                    raise ValueError(f"Value must be between {minimum} and {maximum}")
                store.provision(device_id, {field: value})
                store.queue_command(device_id, "refresh")
            elif command == "set-mode":
                if payload not in descriptor.management.modes:
                    raise ValueError("Unsupported device mode")
                store.provision(device_id, {"assigned_mode": payload})
                store.queue_command(device_id, "refresh")
            elif command == "set-profile":
                if not descriptor.management.supports_dashboard_profiles:
                    raise ValueError(
                        "Dashboard profiles are not supported by this device family"
                    )
                if payload not in dashboards.names():
                    raise ValueError("Unknown dashboard profile")
                store.provision(device_id, {"assigned_profile": payload})
                store.queue_command(device_id, "refresh")
            elif command == "set-policy":
                if not descriptor.management.supports_fleet_policy:
                    raise ValueError(
                        "Fleet policy is not supported by this device family"
                    )
                preset = fleet_policy_profiles().get(payload)
                if not preset:
                    raise ValueError("Unknown fleet policy profile")
                assignment = _provisioning_assignment(
                    preset["settings"], device_id, dashboards.names()
                )
                assignment, _ = _filter_provisioning_assignment(
                    assignment, descriptor
                )
                assignment["assigned_policy_name"] = payload
                assignment["assigned_policy_revision"] = store.next_policy_revision()
                store.provision(device_id, assignment)
                store.queue_command(device_id, "refresh")
            elif command == "set-opendisplay-transport":
                if not descriptor.management.supports_opendisplay_policy:
                    raise ValueError(
                        "OpenDisplay transport is not supported by this device family"
                    )
                selected_transport = payload.strip().lower()
                if selected_transport not in {"auto", "lan_preferred", "ble_only"}:
                    raise ValueError("Unsupported OpenDisplay transport policy")
                store.provision(
                    device_id,
                    {
                        "assigned_open_display_transport_policy": selected_transport
                    },
                )
                store.queue_command(device_id, "refresh")
            elif command in {"set-name", "set-area"}:
                if not descriptor.management.supports_provisioning:
                    raise ValueError(
                        "Provisioning is not supported by this device family"
                    )
                field = "assigned_name" if command == "set-name" else "assigned_area"
                value = _header_value(payload)
                if command == "set-name" and not value:
                    value = device_id
                store.provision(device_id, {field: value})
                store.queue_command(device_id, "refresh")
            elif command == "set-timezone":
                if not descriptor.management.supports_sleep_policy:
                    raise ValueError(
                        "Sleep scheduling is not supported by this device family"
                    )
                try:
                    ZoneInfo(payload)
                except ZoneInfoNotFoundError as err:
                    raise ValueError("Unknown timezone") from err
                store.provision(
                    device_id,
                    {"assigned_timezone": payload[:64]},
                )
                store.queue_command(device_id, "refresh")
            elif command in {"set-active-start", "set-active-end"}:
                if not descriptor.management.supports_sleep_policy:
                    raise ValueError(
                        "Sleep scheduling is not supported by this device family"
                    )
                minutes = _clock_minutes(payload, -1)
                if minutes < 0:
                    raise ValueError("Active time must use HH:MM")
                field = (
                    "assigned_active_start"
                    if command == "set-active-start"
                    else "assigned_active_end"
                )
                store.provision(
                    device_id,
                    {field: f"{minutes // 60:02d}:{minutes % 60:02d}"},
                )
                store.queue_command(device_id, "refresh")
            else:
                raise ValueError("Unsupported MQTT command")
            store.record_management_result(
                device_id, command, True, f"{command}:accepted"
            )
        except (ScreenHistoryError, ValueError) as err:
            store.record_management_result(device_id, command, False, str(err))
        publish_current(device_id)

    mqtt = MqttService(settings.mqtt, queue_from_mqtt)

    def dispatch_queued_command(
        device_id: str, command: str, record: dict[str, Any]
    ) -> None:
        """Wake push-capable displays after the command is safely persisted."""
        try:
            reason = f"command:{command}"
            if _is_android_display(record):
                rook.publish_refresh(device_id, reason)
            if _supports_mqtt_screen_refresh(record):
                mqtt.publish_screen_refresh(
                    device_id,
                    reason=reason,
                    command_id=str(record.get("pending_command_id") or ""),
                    queued_at=str(record.get("command_queued_at") or ""),
                )
        except Exception:
            # The durable command remains queued and the device's safety poll
            # will still deliver it if a best-effort wake transport is down.
            LOGGER.exception("Could not publish refresh wake event for %s", device_id)

    store.add_command_listener(dispatch_queued_command)

    def process_meshtastic_messages(
        messages: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        processed: list[dict[str, Any]] = []
        publisher = getattr(mqtt, "publish_flexhub_message", None)
        for message in meshtastic_console.claim_messages(messages):
            if callable(publisher):
                publisher(message)
            rule_results: list[dict[str, Any]] = []
            for rule in meshtastic_console.matching_rules(message):
                body = str(message.get("text") or "")
                prefix = str(rule.get("match_prefix") or "")
                if rule.get("strip_prefix") and body.casefold().startswith(
                    prefix.casefold()
                ):
                    body = body[len(prefix) :].lstrip(" :-")
                sender = str(
                    message.get("sender")
                    or message.get("sender_name")
                    or message.get("from")
                    or message.get("from_id")
                    or "Meshtastic"
                )[:80]
                channel = message.get("channel", 0)
                if rule.get("notify"):
                    notify_success, notify_detail = ha.call_service(
                        str(rule.get("notify_service") or "notify.notify"),
                        data={
                            "title": str(rule.get("title") or "Meshtastic alert"),
                            "message": body,
                        },
                    )
                    rule_results.append(
                        {
                            "rule_id": rule["id"],
                            "device_id": "home_assistant_notification",
                            "success": notify_success,
                            "detail": notify_detail,
                        }
                    )
                for device_id in rule.get("device_ids") or []:
                    record = store.get(str(device_id))
                    if not record:
                        rule_results.append(
                            {
                                "rule_id": rule["id"],
                                "device_id": device_id,
                                "success": False,
                                "detail": "Display has not checked in",
                            }
                        )
                        continue
                    try:
                        width = int(record.get("width") or 480)
                        height = int(record.get("height") or 800)
                        configured = settings.device(
                            str(device_id),
                            width,
                            height,
                            str(record.get("model") or ""),
                        )
                        profile = _effective_device(configured, record)
                        image = render_content_page(
                            ContentPage(
                                kind="message",
                                title=str(rule.get("title") or "MESHTASTIC ALERT"),
                                body=body,
                                footer=f"{sender} · channel {channel}",
                                source="Meshtastic",
                                priority=str(rule.get("priority") or "important"),
                            ),
                            device_name=profile.name,
                            width=width,
                            height=height,
                            page_index=0,
                            page_count=1,
                        )
                        captured = screen_history.record(
                            str(device_id),
                            image,
                            media_type="image/png",
                            metadata={
                                "source": "meshtastic_rule",
                                "rule_id": rule["id"],
                                "message_sequence": message.get("sequence"),
                                "title": rule.get("title"),
                            },
                        )
                        store.set_screen_override(str(device_id), str(captured["id"]))
                        store.queue_command(str(device_id), "refresh")
                        store.record_management_result(
                            str(device_id),
                            "meshtastic-rule",
                            True,
                            f"Rule {rule['id']} queued message {message.get('sequence', '')}",
                        )
                        publish_current(str(device_id))
                        rule_results.append(
                            {
                                "rule_id": rule["id"],
                                "device_id": device_id,
                                "success": True,
                                "screen_id": captured["id"],
                            }
                        )
                    except (OSError, ScreenHistoryError, ValueError) as exc:
                        store.record_management_result(
                            str(device_id), "meshtastic-rule", False, str(exc)
                        )
                        rule_results.append(
                            {
                                "rule_id": rule["id"],
                                "device_id": device_id,
                                "success": False,
                                "detail": str(exc)[:160],
                            }
                        )
            meshtastic_console.record_evaluation(message, rule_results)
            processed.append({"message": message, "rule_results": rule_results})
        return processed

    async def warm_firmware_mirror() -> None:
        try:
            await asyncio.to_thread(firmware_mirror.prepare, settings.firmware)
        except FirmwareMirrorError:
            # Health/status endpoints expose the bounded failure and next retry.
            pass

    async def monitor_fleet_health() -> None:
        while True:
            await asyncio.sleep(15)
            store.expire_stale_firmware_installs(
                settings.firmware.stale_install_seconds
            )
            advance_firmware_rollout()
            for current in store.all():
                publish_current(str(current.get("device_id") or ""))

    async def monitor_flexhub() -> None:
        while True:
            try:
                if flexhub.configured:
                    summary = await asyncio.to_thread(flexhub.poll)
                    mqtt.publish_flexhub(summary)
                    if summary.get("connected"):
                        try:
                            _, observed = await asyncio.to_thread(
                                flexhub.fetch_messages,
                                after=0,
                                limit=FlexHubClient.MESHTASTIC_MESSAGE_CAPACITY,
                            )
                        except FlexHubClientError:
                            observed = []
                        if observed:
                            await asyncio.to_thread(
                                process_meshtastic_messages, observed
                            )
            except asyncio.CancelledError:
                raise
            except Exception:
                LOGGER.exception("FlexHub monitor iteration failed")
            await asyncio.sleep(settings.flexhub.poll_seconds)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        del app
        store.expire_stale_firmware_installs(settings.firmware.stale_install_seconds)
        for purged_device_id in store.purged_device_ids:
            mqtt.remove_device(purged_device_id)
        mqtt.start()
        health_task = asyncio.create_task(monitor_fleet_health())
        flexhub_task = asyncio.create_task(monitor_flexhub())
        mirror_task = None
        if settings.firmware.mirror_enabled:
            mirror_task = asyncio.create_task(warm_firmware_mirror())
        yield
        if mirror_task and not mirror_task.done():
            mirror_task.cancel()
        health_task.cancel()
        flexhub_task.cancel()
        with suppress(asyncio.CancelledError):
            await health_task
        with suppress(asyncio.CancelledError):
            await flexhub_task
        mqtt.stop()

    app = FastAPI(
        title="FlexDisplay Home Assistant Bridge",
        version=__version__,
        lifespan=lifespan,
    )
    app.state.config = settings
    app.state.store = store
    app.state.dashboards = dashboards
    app.state.photo_frames = photo_frames
    app.state.loading_screens = loading_screens
    app.state.content_packs = content_packs
    app.state.content_channels = content_channels
    app.state.firmware_mirror = firmware_mirror
    app.state.flexhub = flexhub
    app.state.meshtastic_console = meshtastic_console
    app.state.screen_history = screen_history
    app.state.mqtt = mqtt
    app.state.rook = rook
    app.state.voice_assistant = voice_assistant

    def firmware_delivery_url(
        request: Request, selected: dict[str, Any] | str = ""
    ) -> str:
        firmware = _device_firmware(settings, selected)
        descriptor = _device_capabilities(selected)
        if descriptor.model_key == "x4_pro":
            if firmware.url == "packaged":
                return str(request.url_for("x4_pro_firmware_binary"))
            return firmware.url
        if descriptor.firmware.provider == "note4":
            if firmware.url == "packaged":
                return str(request.url_for("note4_firmware_binary"))
            return firmware.url
        if firmware.mirror_enabled:
            return str(request.url_for("firmware_binary"))
        return firmware.url

    def apply_frontlight_headers(
        response: Response,
        record: dict[str, Any],
    ) -> None:
        descriptor = _device_capabilities(record)
        frontlight = descriptor.frontlight
        if frontlight.available is not True:
            return
        if frontlight.supports_on:
            desired_on = record.get(
                "desired_frontlight_on", record.get("frontlight_on") is True
            )
            response.headers["X-FlexDisplay-Desired-Frontlight-On"] = (
                "true" if desired_on is True else "false"
            )
        if frontlight.supports_brightness:
            brightness = record.get(
                "desired_frontlight_brightness",
                record.get("frontlight_brightness"),
            )
            if brightness is not None:
                response.headers["X-FlexDisplay-Desired-Frontlight-Brightness"] = str(
                    max(frontlight.minimum, min(frontlight.maximum, int(brightness)))
                )
        if frontlight.supports_warmth:
            warmth = record.get(
                "desired_frontlight_warmth",
                record.get("frontlight_warmth"),
            )
            if warmth is not None:
                response.headers["X-FlexDisplay-Desired-Frontlight-Warmth"] = str(
                    max(frontlight.minimum, min(frontlight.maximum, int(warmth)))
                )
        reported = set(descriptor.reported_capabilities)
        if "frontlight-home-hold" in reported:
            enabled = record.get(
                "desired_frontlight_home_hold",
                record.get("frontlight_home_hold") is not False,
            )
            response.headers["X-FlexDisplay-Desired-Frontlight-Home-Hold"] = (
                "true" if enabled else "false"
            )
        if "frontlight-timeout" in reported:
            timeout = record.get(
                "desired_frontlight_timeout_seconds",
                record.get("frontlight_timeout_seconds", 300),
            )
            response.headers["X-FlexDisplay-Desired-Frontlight-Timeout"] = str(
                max(15, min(3600, int(timeout)))
            )

    def apply_loading_screen_headers(
        response: Response,
        request: Request,
        device_id: str,
        profile: DeviceConfig,
        width: int,
        height: int,
    ) -> None:
        config = loading_screens.effective(device_id)
        _, digest = loading_screens.render(
            device_id,
            {
                "name": profile.name,
                "area": profile.area,
                "profile": profile.profile,
            },
            width,
            height,
        )
        response.headers["X-FlexDisplay-Loading-Enabled"] = (
            "true" if config["enabled"] else "false"
        )
        response.headers["X-FlexDisplay-Loading-Policy"] = str(config["policy"])
        response.headers["X-FlexDisplay-Loading-SHA256"] = digest
        response.headers["X-FlexDisplay-Loading-URL"] = str(
            request.url_for("device_loading_screen", device_id=device_id)
        )

    def apply_content_pack_headers(
        response: Response,
        request: Request,
        device_id: str,
    ) -> None:
        assignment = content_packs.desired(device_id)
        if not assignment:
            return
        version = str(assignment["desired_version"])
        base_url = str(request.base_url).rstrip("/")
        access_token = content_packs.download_token(version)
        _, digest = content_packs.manifest(version, base_url, access_token)
        response.headers["X-FlexDisplay-Content-Version"] = version
        response.headers["X-FlexDisplay-Content-Manifest-URL"] = (
            f"{base_url}/api/v1/content-packs/{version}/manifest.json"
            f"?access_token={access_token}"
        )
        response.headers["X-FlexDisplay-Content-Manifest-SHA256"] = digest

    def queue_loading_screen_refresh(target: str) -> list[str]:
        refreshed: list[str] = []
        for current in store.all():
            device_id = str(current.get("device_id") or "")
            if target != "default" and device_id != target:
                continue
            store.queue_command(device_id, "refresh")
            refreshed.append(device_id)
        return refreshed

    @app.middleware("http")
    async def normalize_ingress_path(request: Request, call_next):
        """Tolerate malformed ingress paths while Home Assistant refreshes app metadata."""
        path = str(request.scope.get("path") or "")
        if path.startswith("//"):
            request.scope["path"] = f"/{path.lstrip('/')}"
            raw_path = request.scope.get("raw_path")
            if isinstance(raw_path, bytes) and raw_path.startswith(b"//"):
                request.scope["raw_path"] = b"/" + raw_path.lstrip(b"/")
        return await call_next(request)

    @app.get("/healthz")
    def health() -> dict[str, Any]:
        flexhub_health = flexhub.summary()
        mirror_health = firmware_mirror.status(settings.firmware)
        return {
            "status": "ok",
            "version": __version__,
            "home_assistant_configured": ha.configured,
            "mqtt_enabled": settings.mqtt.enabled,
            "mqtt_connected": mqtt.connected,
            "home_assistant_entity_source": settings.mqtt.entity_source,
            "mqtt_discovery_enabled": mqtt.discovery_enabled,
            "screen_history_enabled": settings.screen_history.enabled,
            "screen_history_limit": settings.screen_history.limit,
            "firmware_mirror": _public_mirror_status(mirror_health),
            "firmware_maintenance": _firmware_maintenance_status(settings),
            "flexhub": {
                "configured": bool(flexhub_health.get("configured")),
                "connected": bool(flexhub_health.get("connected")),
                "last_seen": flexhub_health.get("last_seen") or "",
                "error": _public_flexhub_error(flexhub_health.get("error")),
            },
        }

    @app.get("/api/v1/system")
    def system_status(request: Request) -> dict[str, Any]:
        """Return the effective, redacted operational configuration for Studio."""
        authorize(request)
        flexhub_health = flexhub.summary()
        mirror = firmware_mirror.status(settings.firmware)
        maintenance = _firmware_maintenance_status(settings)
        firmware_source = os.getenv(
            "FLEXDISPLAY_FIRMWARE_CONFIG_SOURCE",
            "packaged_release" if settings.firmware.url == "packaged" else "bridge_configuration",
        )
        note4_source = os.getenv(
            "FLEXDISPLAY_NOTE4_FIRMWARE_CONFIG_SOURCE",
            "packaged_release"
            if settings.note4_firmware.url == "packaged"
            else "bridge_configuration",
        )
        if firmware_source not in {
            "packaged_release",
            "home_assistant_app",
            "bridge_configuration",
        }:
            firmware_source = "bridge_configuration"
        if note4_source not in {
            "packaged_release",
            "home_assistant_app",
            "bridge_configuration",
        }:
            note4_source = "bridge_configuration"
        configured_firmware_version = os.getenv(
            "FLEXDISPLAY_FIRMWARE_CONFIGURED_VERSION", ""
        )
        configured_note4_version = os.getenv(
            "FLEXDISPLAY_NOTE4_FIRMWARE_CONFIGURED_VERSION", ""
        )
        configured_flexhub_url = _safe_display_url(settings.flexhub.url)
        effective_flexhub_url = _safe_display_url(
            str(flexhub_health.get("url") or "")
        )
        flexhub_saved = bool(flexhub_health.get("saved_configuration"))
        flexhub_url_override = bool(flexhub_health.get("saved_url_override"))
        flexhub_pin_saved = bool(flexhub_health.get("saved_pin_authoritative"))
        flexhub_apply_state = (
            "saved_disconnect"
            if flexhub_url_override and not effective_flexhub_url
            else "saved_override"
            if flexhub_url_override
            else "effective"
        )
        firmware_owner = (
            "Release package"
            if firmware_source == "packaged_release"
            else "Home Assistant App"
            if firmware_source == "home_assistant_app"
            else "Bridge configuration"
        )
        note4_owner = (
            "Release package"
            if note4_source == "packaged_release"
            else "Home Assistant App"
            if note4_source == "home_assistant_app"
            else "Bridge configuration"
        )
        x_metadata_error = _firmware_metadata_error(settings, settings.firmware)
        x4_pro_metadata_error = _firmware_metadata_error(
            settings, settings.x4_pro_firmware
        )
        note4_metadata_error = _firmware_metadata_error(
            settings, settings.note4_firmware
        )
        x_channel_ready = not x_metadata_error and (
            not settings.firmware.mirror_enabled or bool(mirror.get("ready"))
        )

        effective_settings = {
            "dashboard_title": {
                "label": "Dashboard title",
                "value": settings.title,
                "source": "bridge_configuration",
                "apply_state": "effective",
                "owner": "Home Assistant App",
            },
            "home_assistant_entity_source": {
                "label": "Home Assistant entity source",
                "value": settings.mqtt.entity_source,
                "source": "bridge_configuration",
                "apply_state": "effective",
                "owner": "Home Assistant App",
            },
            "mqtt_enabled": {
                "label": "MQTT enabled",
                "value": settings.mqtt.enabled,
                "source": "bridge_configuration",
                "apply_state": "effective",
                "owner": "Home Assistant App",
            },
            "mqtt_endpoint": {
                "label": "MQTT endpoint",
                "value": _safe_host_port(settings.mqtt.host, settings.mqtt.port),
                "source": "bridge_configuration",
                "apply_state": "effective",
                "owner": "Home Assistant App",
            },
            "mqtt_credentials": {
                "label": "MQTT credentials",
                "value": "configured" if settings.mqtt.username or settings.mqtt.password else "not configured",
                "source": "bridge_configuration",
                "apply_state": "effective",
                "owner": "Home Assistant App",
                "sensitive": True,
            },
            "bridge_api_key": {
                "label": "Bridge API key",
                "value": "configured" if settings.api_key else "not configured",
                "source": "bridge_configuration",
                "apply_state": "effective",
                "owner": "Home Assistant App",
                "sensitive": True,
            },
            "screen_history_enabled": {
                "label": "Screen history",
                "value": settings.screen_history.enabled,
                "source": "bridge_configuration",
                "apply_state": "effective",
                "owner": "Home Assistant App",
            },
            "screen_history_limit": {
                "label": "Screen history limit",
                "value": settings.screen_history.limit,
                "source": "bridge_configuration",
                "apply_state": "effective",
                "owner": "Home Assistant App",
            },
            "flexhub_endpoint": {
                "label": "FlexHub endpoint",
                "value": effective_flexhub_url,
                "configured_value": (
                    configured_flexhub_url if flexhub_url_override else ""
                ),
                "configured_label": "Home Assistant App option",
                "source": flexhub_health.get("url_configuration_source")
                or "not_configured",
                "apply_state": flexhub_apply_state,
                "owner": (
                    "Bridge saved state"
                    if flexhub_health.get("saved_url_authoritative")
                    else "Home Assistant App"
                ),
                "detail": (
                    "A saved Bridge disconnect overrides the Home Assistant App option."
                    if flexhub_apply_state == "saved_disconnect"
                    else (
                        "Saved Bridge connection settings override the Home Assistant App option."
                    )
                    if flexhub_apply_state == "saved_override"
                    else ""
                ),
            },
            "flexhub_access_pin": {
                "label": "FlexHub access PIN",
                "value": (
                    "configured"
                    if flexhub_health.get("access_pin_configured")
                    else "not configured"
                ),
                "source": flexhub_health.get("pin_configuration_source")
                or "not_configured",
                "apply_state": (
                    "saved_override" if flexhub_pin_saved else "effective"
                ),
                "owner": (
                    "Bridge saved state"
                    if flexhub_health.get("saved_pin_authoritative")
                    else "Home Assistant App"
                ),
                "sensitive": True,
                "detail": (
                    "A saved Bridge PIN overrides the Home Assistant App option."
                    if flexhub_pin_saved
                    else ""
                ),
            },
            "x_series_firmware": {
                "label": "X3/X4 firmware",
                "value": settings.firmware.version,
                "configured_value": configured_firmware_version,
                "source": firmware_source,
                "apply_state": (
                    "resolved_override"
                    if configured_firmware_version
                    and configured_firmware_version != settings.firmware.version
                    else "effective"
                ),
                "owner": firmware_owner,
                "detail": (
                    "A known legacy Home Assistant option was resolved to the packaged release."
                    if configured_firmware_version
                    and configured_firmware_version != settings.firmware.version
                    else ""
                ),
            },
            "firmware_maintenance_window": {
                "label": "Firmware maintenance window",
                "value": (
                    f"{maintenance['start']}-{maintenance['end']} {maintenance['timezone']}"
                    if maintenance["enabled"]
                    else "Disabled"
                ),
                "source": "bridge_configuration",
                "apply_state": "effective",
                "owner": "Home Assistant App",
            },
        }

        system_payload = {
            "bridge": {
                "status": "running",
                "version": __version__,
                "detail": "Serving Studio and device check-ins.",
                "owner": "Home Assistant App",
            },
            "connections": {
                "home_assistant": {
                    "configured": ha.configured,
                    "status": "configured" if ha.configured else "not_configured",
                    "detail": (
                        "API credentials are configured; connectivity is verified when entities are requested."
                        if ha.configured
                        else "Home Assistant API credentials are not configured."
                    ),
                    "owner": "Home Assistant App",
                },
                "mqtt": {
                    "enabled": settings.mqtt.enabled,
                    "connected": mqtt.connected,
                    "status": (
                        "connected"
                        if mqtt.connected
                        else "disconnected"
                        if settings.mqtt.enabled
                        else "disabled"
                    ),
                    "endpoint": _safe_host_port(
                        settings.mqtt.host, settings.mqtt.port
                    ),
                    "discovery_enabled": mqtt.discovery_enabled,
                    "detail": (
                        "Discovery and device state publishing are available."
                        if mqtt.connected and mqtt.discovery_enabled
                        else "MQTT is connected; discovery is disabled while HACS owns entities."
                        if mqtt.connected
                        else "MQTT is enabled but the Bridge is not connected."
                        if settings.mqtt.enabled
                        else "MQTT is disabled."
                    ),
                    "owner": "Home Assistant App",
                },
                "flexhub": {
                    "configured": bool(flexhub_health.get("configured")),
                    "connected": bool(flexhub_health.get("connected")),
                    "status": (
                        "connected"
                        if flexhub_health.get("connected")
                        else "disconnected"
                        if flexhub_health.get("configured")
                        else "not_configured"
                    ),
                    "display_url": effective_flexhub_url,
                    "last_seen": flexhub_health.get("last_seen") or "",
                    "detail": _public_flexhub_error(flexhub_health.get("error"))
                    or (
                        "FlexHub is operational."
                        if flexhub_health.get("connected")
                        else "FlexHub is configured but is not currently connected."
                        if flexhub_health.get("configured")
                        else "No FlexHub endpoint is configured."
                    ),
                    "owner": (
                        "Bridge saved state"
                        if flexhub_saved
                        else "Home Assistant App"
                    ),
                },
            },
            "effective_settings": effective_settings,
            "firmware_channels": {
                "x_series": {
                    "label": "XTEINK X3/X4",
                    "status": (
                        "ready"
                        if x_channel_ready
                        else "invalid"
                        if x_metadata_error
                        else mirror.get("state") or "not_ready"
                    ),
                    "version": settings.firmware.version,
                    "family": "X3 and X4 only",
                    "track": "xteink",
                    "owner": firmware_owner,
                    "source": firmware_source,
                    "detail": (
                        "Verified local mirror is ready for capability-gated rollout."
                        if settings.firmware.mirror_enabled and mirror.get("ready")
                        else "Direct firmware delivery is configured; local mirroring is disabled."
                        if x_channel_ready
                        else x_metadata_error
                        or _public_firmware_error(mirror.get("last_error"))
                        or "The local firmware mirror is not ready."
                    ),
                },
                "x4_pro": {
                    "label": "XTEINK X4 Pro",
                    "status": (
                        "configured"
                        if not x4_pro_metadata_error
                        else "blocked"
                    ),
                    "version": settings.x4_pro_firmware.version,
                    "family": "X4 Pro exact hardware variants only",
                    "track": "x4pro_s3",
                    "owner": "External X4 Pro firmware repository",
                    "source": "bridge_configuration",
                    "detail": (
                        "Exact model, board, hardware revision, MCU family, and flash size are required."
                        if not x4_pro_metadata_error
                        else (
                            "No compatible X4 Pro manifest/artifact is configured; "
                            "X4 Pro devices remain read-only and cannot inherit X3/X4 OTA."
                        )
                    ),
                },
                "note4": {
                    "label": "Zectrix Note4",
                    "status": "configured" if not note4_metadata_error else "not_configured",
                    "version": settings.note4_firmware.version,
                    "configured_version": configured_note4_version,
                    "family": "Note4 only",
                    "track": "note4",
                    "owner": note4_owner,
                    "source": note4_source,
                    "detail": (
                        "Delivered through the dedicated Note4 firmware channel."
                        if not note4_metadata_error
                        else note4_metadata_error
                    ),
                },
                "android_receiver": {
                    "label": "Amazon Android receivers",
                    "status": "external",
                    "version": "Managed on device",
                    "family": "Echo Spot and Echo Show 5",
                    "track": "android_app",
                    "owner": "Android receiver app",
                    "detail": "Android application updates never use ESP firmware rollout.",
                },
                "generic_embedded": {
                    "label": "Generic ESP displays",
                    "status": "unmanaged",
                    "version": "No trusted release",
                    "family": "Unclassified ESP and LCD devices",
                    "track": "none",
                    "owner": "Device-specific integration",
                    "detail": "Unknown devices fail closed until a trusted firmware provider is added.",
                },
            },
            "links": {
                "home_assistant_app_settings": {
                    "label": "Home Assistant Apps",
                    "url": _home_assistant_apps_url(request),
                    "owner": "Home Assistant",
                    "description": (
                        "Open Apps, select FlexDisplay Bridge, then manage lifecycle "
                        "settings, credentials, network ports, and restart-required options."
                    ),
                },
                "bridge_health": {
                    "label": "Bridge health JSON",
                    "url": "../healthz",
                    "owner": "FlexDisplay Bridge",
                    "description": "Open the small redacted runtime health response.",
                    "external": False,
                },
            },
        }

        alerts: list[dict[str, Any]] = []
        for setting_id, entry in effective_settings.items():
            apply_state = str(entry.get("apply_state") or "effective")
            if apply_state == "effective":
                continue
            alerts.append(
                {
                    "id": f"setting-{setting_id}",
                    "severity": "warning",
                    "category": "configuration_drift",
                    "title": f"{entry.get('label') or setting_id} is overridden",
                    "detail": str(
                        entry.get("detail")
                        or "The effective value differs from the saved configuration."
                    ),
                    "owner": str(entry.get("owner") or "FlexDisplay Bridge"),
                }
            )
        if settings.mqtt.enabled and not mqtt.connected:
            alerts.append(
                {
                    "id": "mqtt-disconnected",
                    "severity": "warning",
                    "category": "connection",
                    "title": "MQTT is disconnected",
                    "detail": "The Bridge is configured for MQTT but is not connected to the broker.",
                    "owner": "Home Assistant App",
                }
            )
        if flexhub_health.get("configured") and not flexhub_health.get("connected"):
            alerts.append(
                {
                    "id": "flexhub-disconnected",
                    "severity": "warning",
                    "category": "connection",
                    "title": "FlexHub is disconnected",
                    "detail": "The configured FlexHub is not currently reachable.",
                    "owner": "FlexDisplay Bridge",
                }
            )
        if not ha.configured:
            alerts.append(
                {
                    "id": "home-assistant-not-configured",
                    "severity": "info",
                    "category": "connection",
                    "title": "Home Assistant API is not configured",
                    "detail": "Entity-backed dashboard content is unavailable until Home Assistant credentials are configured.",
                    "owner": "Home Assistant App",
                }
            )
        x_channel = system_payload["firmware_channels"]["x_series"]
        if x_channel["status"] not in {"ready", "configured"}:
            alerts.append(
                {
                    "id": "x-series-firmware-not-ready",
                    "severity": "warning",
                    "category": "firmware",
                    "title": "X3/X4 firmware channel is not ready",
                    "detail": str(
                        x_channel.get("detail")
                        or "Review the release channel configuration."
                    ),
                    "owner": str(x_channel.get("owner") or "FlexDisplay Bridge"),
                }
            )
        system_payload["alerts"] = alerts
        return system_payload

    @app.post("/api/v1/devices/{device_id}/assist")
    def run_device_assist(
        device_id: str,
        request: Request,
        audio: bytes = Body(..., media_type="application/octet-stream"),
    ) -> Response:
        authorize(request)
        selected = _device_id(device_id)
        if request.headers.get("X-FlexDisplay-New-Conversation", "").lower() in {
            "1",
            "true",
            "yes",
        }:
            voice_assistant.reset_conversation(selected)
        try:
            result = voice_assistant.run(audio, selected)
        except VoiceAssistantError as exc:
            message = str(exc)
            status = 400 if message.startswith(("Hold", "Voice command", "PCM")) else 502
            raise HTTPException(status_code=status, detail=message) from exc
        store.touch(
            selected,
            {
                "last_voice_transcript": result.transcript[:512],
                "last_voice_response": result.response_text[:512],
                "last_voice_at": datetime.now(UTC).isoformat(timespec="seconds"),
            },
        )
        return Response(
            encode_voice_response(result),
            media_type="application/octet-stream",
            headers={
                "X-FlexDisplay-Assist-Transcript": display_text(result.transcript),
                "X-FlexDisplay-Assist-Response": display_text(result.response_text),
                "X-FlexDisplay-Audio-Format": "pcm-s16le-16000-mono",
                "X-FlexDisplay-Conversation": (
                    "continue" if result.continue_conversation else "active"
                ),
            },
        )

    @app.put("/api/v1/devices/{device_id}/voice")
    def configure_device_voice(
        device_id: str,
        payload: dict[str, Any],
        request: Request,
    ) -> dict[str, Any]:
        authorize(request)
        selected = _device_id(device_id)
        current = store.get(selected)
        if not current:
            raise HTTPException(status_code=404, detail="Device has not checked in")
        if not (_is_note4(current) or _is_android_display(current)):
            raise HTTPException(
                status_code=409,
                detail="Voice controls require a Note4 or Android receiver",
            )
        changes: dict[str, Any] = {}
        if "volume" in payload:
            changes["desired_voice_volume"] = max(0, min(100, int(payload["volume"])))
        if "muted" in payload:
            changes["desired_voice_muted"] = bool(payload["muted"])
        if not changes:
            raise HTTPException(status_code=400, detail="Volume or mute state is required")
        record = store.touch(selected, changes)
        return {"updated": True, "device": record}

    @app.put("/api/v1/devices/{device_id}/display")
    def configure_device_display(
        device_id: str,
        payload: dict[str, Any],
        request: Request,
    ) -> dict[str, Any]:
        authorize(request)
        selected = _device_id(device_id)
        current = store.get(selected)
        if not current:
            raise HTTPException(status_code=404, detail="Device has not checked in")
        descriptor = _device_capabilities(current)
        changes: dict[str, Any] = {}
        if _is_android_display(current):
            if "brightness" in payload:
                changes["desired_screen_brightness"] = max(
                    5, min(100, int(payload["brightness"]))
                )
        elif descriptor.frontlight.available is True:
            reported = set(descriptor.reported_capabilities)
            if "frontlight_on" in payload and descriptor.frontlight.supports_on:
                changes["desired_frontlight_on"] = bool(payload["frontlight_on"])
            if (
                "frontlight_brightness" in payload
                and descriptor.frontlight.supports_brightness
            ):
                changes["desired_frontlight_brightness"] = max(
                    descriptor.frontlight.minimum,
                    min(
                        descriptor.frontlight.maximum,
                        int(payload["frontlight_brightness"]),
                    ),
                )
            if (
                "frontlight_warmth" in payload
                and descriptor.frontlight.supports_warmth
            ):
                changes["desired_frontlight_warmth"] = max(
                    descriptor.frontlight.minimum,
                    min(
                        descriptor.frontlight.maximum,
                        int(payload["frontlight_warmth"]),
                    ),
                )
            if (
                "frontlight_home_hold" in payload
                and "frontlight-home-hold" in reported
            ):
                changes["desired_frontlight_home_hold"] = bool(
                    payload["frontlight_home_hold"]
                )
            if (
                "frontlight_timeout_seconds" in payload
                and "frontlight-timeout" in reported
            ):
                changes["desired_frontlight_timeout_seconds"] = max(
                    15, min(3600, int(payload["frontlight_timeout_seconds"]))
                )
        else:
            raise HTTPException(
                status_code=409,
                detail="Display controls are not admitted for this hardware revision",
            )
        if not changes:
            raise HTTPException(
                status_code=400,
                detail="A supported display control is required",
            )
        record = store.touch(selected, changes)
        return {"updated": True, "device": record}

    @app.get("/api/v1/flexhub")
    def flexhub_status(request: Request, refresh: bool = False) -> dict[str, Any]:
        authorize(request)
        summary = (
            flexhub.poll() if refresh and flexhub.configured else flexhub.summary()
        )
        mqtt.publish_flexhub(summary)
        return summary

    @app.put("/api/v1/flexhub/settings")
    def configure_flexhub(payload: dict[str, Any], request: Request) -> dict[str, Any]:
        authorize(request)
        try:
            flexhub.configure(
                str(payload.get("url") or ""),
                str(payload.get("access_pin") or ""),
            )
        except FlexHubClientError as err:
            raise HTTPException(status_code=400, detail=str(err)) from err
        summary = flexhub.poll() if flexhub.configured else flexhub.summary()
        mqtt.publish_flexhub(summary)
        return summary

    @app.post("/api/v1/flexhub/refresh")
    def refresh_flexhub(request: Request) -> dict[str, Any]:
        authorize(request)
        summary = flexhub.poll()
        mqtt.publish_flexhub(summary)
        return summary

    @app.get("/api/v1/flexhub/meshtastic/messages")
    def flexhub_meshtastic_messages(
        request: Request,
        after: int = 0,
        limit: int = 30,
        session_id: int | None = None,
        query: str = "",
        direction: str = "",
        channel: int | None = None,
        node: str = "",
    ) -> dict[str, Any]:
        authorize(request)
        try:
            result, observed = flexhub.fetch_messages(
                after=after,
                limit=limit,
                session_id=session_id,
                query=query,
                direction=direction,
                channel=channel,
                node=node,
            )
        except FlexHubClientError as err:
            status = (
                400 if str(err).startswith("Meshtastic") else _flexhub_proxy_status(err)
            )
            raise HTTPException(status_code=status, detail=str(err)) from err
        processed = process_meshtastic_messages(observed)
        return {
            **result,
            "bridge": {
                "new_messages": len(processed),
                "console": flexhub.summary()["meshtastic_console"],
            },
        }

    @app.get("/api/v1/flexhub/meshtastic/nodes")
    def flexhub_meshtastic_nodes(request: Request) -> dict[str, Any]:
        authorize(request)
        try:
            return flexhub.meshtastic_nodes()
        except FlexHubClientError as err:
            raise HTTPException(
                status_code=_flexhub_proxy_status(err), detail=str(err)
            ) from err

    @app.post("/api/v1/flexhub/meshtastic/messages")
    def send_flexhub_meshtastic_message(
        payload: dict[str, Any], request: Request
    ) -> dict[str, Any]:
        authorize(request)
        try:
            normalized = FlexHubClient.normalize_meshtastic_message(payload)
        except FlexHubClientError as err:
            raise HTTPException(status_code=400, detail=str(err)) from err
        try:
            result = flexhub.send_meshtastic_message(normalized)
        except FlexHubClientError as err:
            raise HTTPException(
                status_code=_flexhub_proxy_status(err), detail=str(err)
            ) from err
        return result

    @app.post("/api/v1/flexhub/actions/{action}")
    def run_flexhub_action(action: str, request: Request) -> dict[str, Any]:
        authorize(request)
        try:
            return flexhub.action(action)
        except FlexHubClientError as err:
            status = (
                400
                if str(err) == "Unsupported FlexHub action"
                else _flexhub_proxy_status(err)
            )
            raise HTTPException(status_code=status, detail=str(err)) from err

    @app.get("/api/v1/flexhub/meshtastic/settings")
    def flexhub_meshtastic_settings(request: Request) -> dict[str, Any]:
        authorize(request)
        return meshtastic_console.payload()

    @app.put("/api/v1/flexhub/meshtastic/settings")
    def save_flexhub_meshtastic_settings(
        payload: dict[str, Any], request: Request
    ) -> dict[str, Any]:
        authorize(request)
        try:
            return meshtastic_console.replace(payload)
        except MeshtasticConsoleValidationError as err:
            raise HTTPException(status_code=400, detail=str(err)) from err

    @app.post("/api/v1/flexhub/meshtastic/read")
    def mark_flexhub_meshtastic_read(request: Request) -> dict[str, Any]:
        authorize(request)
        return {"meshtastic_console": flexhub.mark_meshtastic_read()}

    @app.get("/api/v1/firmware/current.bin", name="firmware_binary")
    def firmware_binary() -> FileResponse:
        try:
            path = firmware_mirror.prepare(settings.firmware)
        except FirmwareMirrorError as err:
            raise HTTPException(
                status_code=503,
                detail=_public_firmware_error(str(err)),
            ) from err
        return FileResponse(
            path,
            media_type="application/octet-stream",
            filename=f"flexdisplay-{settings.firmware.version}.bin",
            headers={
                "X-FlexDisplay-Firmware-Version": settings.firmware.version,
                "X-FlexDisplay-Firmware-SHA256": settings.firmware.sha256,
                "Cache-Control": "private, max-age=300",
            },
        )

    @app.get("/api/v1/firmware/note4/current.bin", name="note4_firmware_binary")
    def note4_firmware_binary() -> FileResponse:
        firmware = settings.note4_firmware
        path = Path(
            os.getenv(
                "FLEXDISPLAY_PACKAGED_NOTE4_FIRMWARE",
                "/app/firmware/note4.bin",
            )
        )
        if not path.is_file():
            raise HTTPException(status_code=503, detail="Note4 firmware is not packaged")
        if path.stat().st_size != firmware.size:
            raise HTTPException(status_code=503, detail="Note4 firmware size mismatch")
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest != firmware.sha256:
            raise HTTPException(status_code=503, detail="Note4 firmware checksum mismatch")
        return FileResponse(
            path,
            media_type="application/octet-stream",
            filename=f"note4-{firmware.version}.bin",
            headers={
                "X-FlexDisplay-Firmware-Version": firmware.version,
                "X-FlexDisplay-Firmware-SHA256": firmware.sha256,
                "Cache-Control": "private, max-age=300",
            },
        )

    @app.get(
        "/api/v1/firmware/x4-pro/current.bin",
        name="x4_pro_firmware_binary",
    )
    def x4_pro_firmware_binary() -> FileResponse:
        firmware = settings.x4_pro_firmware
        path = Path(
            os.getenv(
                "FLEXDISPLAY_PACKAGED_X4_PRO_FIRMWARE",
                "/app/firmware/x4-pro.bin",
            )
        )
        error = _firmware_metadata_error(settings, firmware)
        if error:
            raise HTTPException(status_code=503, detail=error)
        if not path.is_file():
            raise HTTPException(
                status_code=503,
                detail="X4 Pro firmware is not packaged",
            )
        if path.stat().st_size != firmware.size:
            raise HTTPException(status_code=503, detail="X4 Pro firmware size mismatch")
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest != firmware.sha256:
            raise HTTPException(
                status_code=503,
                detail="X4 Pro firmware checksum mismatch",
            )
        return FileResponse(
            path,
            media_type="application/octet-stream",
            filename=f"x4-pro-{firmware.version}.bin",
            headers={
                "X-FlexDisplay-Firmware-Version": firmware.version,
                "X-FlexDisplay-Firmware-SHA256": firmware.sha256,
                "X-FlexDisplay-Artifact-Family": firmware.artifact_family,
                "Cache-Control": "private, max-age=300",
            },
        )

    @app.post("/api/v1/firmware/mirror/refresh")
    def refresh_firmware_mirror(request: Request) -> dict[str, Any]:
        authorize(request)
        try:
            path = firmware_mirror.prepare(settings.firmware, force=True)
        except FirmwareMirrorError as err:
            raise HTTPException(
                status_code=503,
                detail=_public_firmware_error(str(err)),
            ) from err
        return {
            "refreshed": True,
            "mirror": _public_mirror_status(
                firmware_mirror.status(settings.firmware)
            ),
        }

    @app.get("/api/v1/devices")
    def devices(compact: bool = False) -> dict[str, Any]:
        store.expire_stale_firmware_installs(settings.firmware.stale_install_seconds)
        payload: list[dict[str, Any]] = []
        for record in store.all():
            decorated = {
                **_decorate_device(record, settings, store, dashboards.names()),
                "screen_history_count": len(
                    screen_history.list(str(record.get("device_id") or ""))
                ),
            }
            if compact:
                # Studio needs a short check-in series for its sparklines, but
                # not the large diagnostic/event histories returned by the
                # per-device detail endpoint. Keeping those out of the fleet
                # poll removes most of a multi-device response and JSON parse.
                decorated["checkin_history"] = list(
                    decorated.get("checkin_history") or []
                )[-24:]
                for key in (
                    "recent_button_events",
                    "reset_history",
                    "command_history",
                    "firmware_progress_history",
                    "management_history",
                ):
                    decorated.pop(key, None)
            payload.append(decorated)
        return {"devices": payload}

    @app.get("/api/v1/devices/{device_id}")
    def device(device_id: str) -> dict[str, Any]:
        selected = _device_id(device_id)
        record = store.get(selected)
        if not record:
            raise HTTPException(status_code=404, detail="Device not found")
        return {
            **_decorate_device(record, settings, store, dashboards.names()),
            "screen_history_count": len(screen_history.list(selected)),
        }

    @app.get("/api/v1/devices/{device_id}/timeline")
    def device_timeline(
        device_id: str,
        request: Request,
        limit: int = 50,
        include_checkins: bool = False,
    ) -> dict[str, Any]:
        authorize(request)
        selected = _device_id(device_id)
        record = store.get(selected)
        if not record:
            raise HTTPException(status_code=404, detail="Device not found")
        return {
            "device_id": selected,
            "identity": _device_identity(record),
            "events": _device_timeline(
                record,
                include_checkins=include_checkins,
                limit=limit,
            ),
        }

    @app.get("/api/v1/system/support-bundle")
    def system_support_bundle(request: Request) -> Response:
        """Download a compact, allowlisted diagnostic snapshot for support."""
        authorize(request)
        system = system_status(request)
        devices: list[dict[str, Any]] = []
        for record in store.all():
            decorated = _decorate_device(record, settings, store, dashboards.names())
            devices.append(
                {
                    "device_id": decorated.get("device_id"),
                    "name": decorated.get("name"),
                    "area": decorated.get("area"),
                    "model": decorated.get("model"),
                    "identity": decorated.get("identity"),
                    "family": decorated.get("device_family"),
                    "firmware_provider": decorated.get("firmware_provider"),
                    "firmware": decorated.get("firmware"),
                    "latest_firmware": decorated.get("latest_firmware"),
                    "online": bool(decorated.get("online")),
                    "health_state": decorated.get("health_state"),
                    "last_seen": decorated.get("last_seen"),
                    "policy_name": decorated.get("policy_name"),
                    "policy_sync_status": decorated.get("policy_sync_status"),
                    "timeline": [
                        {
                            "type": event.get("type"),
                            "at": event.get("at"),
                            "title": event.get("title"),
                            "status": event.get("status"),
                        }
                        for event in _device_timeline(record, limit=20)
                    ],
                }
            )
        bundle = {
            "schema_version": 1,
            "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "system": system,
            "fleet": {
                "device_count": len(devices),
                "groups": [
                    group_payload(group)
                    for group in store.fleet_groups().values()
                ],
                "devices": devices,
            },
        }
        return Response(
            json.dumps(bundle, indent=2, sort_keys=True),
            media_type="application/json",
            headers={
                "Content-Disposition": (
                    "attachment; filename=flexdisplay-support-bundle.json"
                )
            },
        )

    @app.delete("/api/v1/devices/{device_id}")
    def delete_device(device_id: str, request: Request) -> dict[str, Any]:
        authorize(request)
        selected = _device_id(device_id)
        current = store.get(selected)
        if not current:
            raise HTTPException(status_code=404, detail="Device not found")
        configured = settings.device(
            selected,
            int(current.get("width") or 480),
            int(current.get("height") or 800),
            str(current.get("model") or ""),
        )
        profile = _effective_device(configured, current)
        removed = store.remove_device(selected)
        mqtt.remove_device(selected, profile, current)
        return {"deleted": selected, "device": removed}

    @app.get("/api/v1/devices/{device_id}/events")
    def device_events(device_id: str) -> dict[str, Any]:
        selected = _device_id(device_id)
        record = store.get(selected)
        if not record:
            raise HTTPException(status_code=404, detail="Device not found")
        return {"events": record.get("recent_button_events", [])}

    @app.get("/api/v1/devices/{device_id}/screens")
    def device_screens(device_id: str) -> dict[str, Any]:
        selected = _device_id(device_id)
        if not store.get(selected):
            raise HTTPException(status_code=404, detail="Device not found")
        items = screen_history.list(selected)
        return {
            "device_id": selected,
            "limit": settings.screen_history.limit,
            "screens": [
                {
                    **item,
                    "preview_url": f"/api/v1/devices/{selected}/screens/{item['id']}",
                    "current": index == 0,
                }
                for index, item in enumerate(items)
            ],
        }

    @app.get("/api/v1/devices/{device_id}/screens/current")
    def current_device_screen(device_id: str) -> FileResponse:
        selected = _device_id(device_id)
        current = store.get(selected)
        if not current:
            raise HTTPException(status_code=404, detail="Device not found")
        try:
            path, item = screen_history.latest(selected)
        except ScreenHistoryError as err:
            raise HTTPException(status_code=404, detail=str(err)) from err
        return FileResponse(
            path,
            media_type=str(item.get("media_type") or "image/png"),
            headers={
                "X-FlexDisplay-Screen-ID": str(item["id"]),
                "X-FlexDisplay-Screen-SHA256": str(item["sha256"]),
                "Cache-Control": "no-cache",
            },
        )

    @app.get("/api/v1/devices/{device_id}/screens/current.png")
    def current_device_screen_png(device_id: str) -> Response:
        selected = _device_id(device_id)
        try:
            path, item = screen_history.latest(selected)
            content = path.read_bytes()
        except (OSError, ScreenHistoryError) as err:
            raise HTTPException(status_code=404, detail=str(err)) from err
        if item.get("media_type") != "image/png":
            output = io.BytesIO()
            with Image.open(io.BytesIO(content)) as source:
                source.convert("1").save(output, format="PNG", optimize=True)
            content = output.getvalue()
        return Response(
            content=content,
            media_type="image/png",
            headers={
                "X-FlexDisplay-Screen-ID": str(item["id"]),
                "X-FlexDisplay-Screen-SHA256": str(item["sha256"]),
                "Cache-Control": "no-cache",
            },
        )

    @app.get("/api/v1/devices/{device_id}/screens/{history_id}")
    def historical_device_screen(device_id: str, history_id: str) -> FileResponse:
        selected = _device_id(device_id)
        try:
            path, item = screen_history.get(selected, history_id)
        except ScreenHistoryError as err:
            raise HTTPException(status_code=404, detail=str(err)) from err
        return FileResponse(
            path,
            media_type=str(item.get("media_type") or "image/png"),
            headers={
                "X-FlexDisplay-Screen-ID": str(item["id"]),
                "X-FlexDisplay-Screen-SHA256": str(item["sha256"]),
                "Cache-Control": "private, max-age=300",
            },
        )

    @app.post("/api/v1/devices/{device_id}/screens/{history_id}/resend")
    def resend_historical_screen(
        device_id: str,
        history_id: str,
        request: Request,
    ) -> dict[str, Any]:
        authorize(request)
        selected = _device_id(device_id)
        try:
            _, item = screen_history.get(selected, history_id)
        except ScreenHistoryError as err:
            raise HTTPException(status_code=404, detail=str(err)) from err
        record = store.set_screen_override(selected, str(item["id"]))
        if not record:
            raise HTTPException(status_code=404, detail="Device not found")
        record = store.queue_command(selected, "refresh")
        store.record_management_result(
            selected,
            "resend-screen",
            True,
            f"Queued saved screen {item['id']}",
        )
        publish_current(selected)
        return {
            "queued": True,
            "screen": item,
            "device": _decorate_device(
                record,
                settings,
                store,
                dashboards.names(),
            ),
        }

    @app.get("/api/v1/devices/{device_id}/button-actions")
    def device_button_actions(device_id: str, request: Request) -> dict[str, Any]:
        authorize(request)
        selected = _device_id(device_id)
        record = store.get(selected)
        if not record:
            raise HTTPException(status_code=404, detail="Device not found")
        if not _device_capabilities(record).management.supports_button_actions:
            raise HTTPException(
                status_code=409,
                detail="Configurable physical-button actions are not supported by this device",
            )
        return {
            "device_id": selected,
            "mode": BUTTON_ACTION_MODE,
            "mappings": mappings_payload(record.get("button_action_mappings")),
            "show_indicators": bool(record.get("button_action_indicators")),
            "activation": _button_action_activation(record),
        }

    @app.put("/api/v1/devices/{device_id}/button-actions")
    def save_device_button_actions(
        device_id: str,
        payload: dict[str, Any],
        request: Request,
    ) -> dict[str, Any]:
        authorize(request)
        selected = _device_id(device_id)
        current = store.get(selected)
        if not current:
            raise HTTPException(status_code=404, detail="Device not found")
        if not _device_capabilities(current).management.supports_button_actions:
            raise HTTPException(
                status_code=409,
                detail="Configurable physical-button actions are not supported by this device",
            )
        try:
            mappings = normalize_mappings(payload)
        except ButtonActionValidationError as err:
            raise HTTPException(status_code=400, detail=str(err)) from err
        show_indicators = payload.get("show_indicators") is True
        record = store.set_button_actions(
            selected,
            mappings,
            show_indicators,
        )
        if not record:
            raise HTTPException(status_code=404, detail="Device not found")
        return {
            "device_id": selected,
            "mode": BUTTON_ACTION_MODE,
            "mappings": mappings_payload(record.get("button_action_mappings")),
            "show_indicators": bool(record.get("button_action_indicators")),
            "activation": _button_action_activation(record),
        }

    def authorize(request: Request) -> None:
        if (
            settings.api_key
            and request.headers.get("X-FlexDisplay-Bridge-Key") != settings.api_key
        ):
            raise HTTPException(status_code=401, detail="Bridge API key required")

    def authorize_receiver(request: Request, device_id: str) -> dict[str, Any]:
        selected = _device_id(device_id)
        record = store.get(selected)
        if not record or not _is_android_display(record):
            raise HTTPException(status_code=404, detail="Android receiver not found")
        supplied = request.headers.get("X-FlexDisplay-Receiver-Token", "")
        expected = str(record.get("receiver_token_sha256") or "")
        observed = hashlib.sha256(supplied.encode("utf-8")).hexdigest() if supplied else ""
        if not expected or not hmac.compare_digest(expected, observed):
            raise HTTPException(status_code=401, detail="Receiver token required")
        return record

    def execute_rook_action(
        device_id: str,
        action: dict[str, Any],
        confirmed: bool,
        source: str,
    ) -> dict[str, Any]:
        if action.get("confirmation") and not confirmed:
            raise HTTPException(
                status_code=409,
                detail={
                    "confirmation_required": True,
                    "message": action.get("confirmation_text") or "Confirm this action",
                },
            )
        success, detail = ha.call_service(
            str(action.get("service") or ""),
            str(action.get("entity_id") or ""),
            action.get("data") if isinstance(action.get("data"), dict) else None,
        )
        store.touch(
            device_id,
            {
                "last_touch_action_at": datetime.now(UTC).isoformat(timespec="seconds"),
                "last_touch_action_source": source,
                "last_touch_action_label": action.get("label"),
                "last_touch_action_result": detail,
            },
        )
        publish_current(device_id)
        if not success:
            raise HTTPException(status_code=502, detail=detail)
        return {"success": True, "detail": detail, "refresh": True}

    @app.get("/api/v1/devices/{device_id}/interactions")
    def receiver_interactions(device_id: str, request: Request) -> dict[str, Any]:
        authorize_receiver(request, device_id)
        return rook.interactions(_device_id(device_id))

    @app.post("/api/v1/devices/{device_id}/interactions/{action_id}")
    def receiver_interaction_action(
        device_id: str,
        action_id: str,
        request: Request,
        payload: dict[str, Any] = Body(default={}),
    ) -> dict[str, Any]:
        authorize_receiver(request, device_id)
        selected = _device_id(device_id)
        action = rook.interaction_action(selected, action_id)
        if not action:
            raise HTTPException(status_code=404, detail="Interaction is no longer active")
        return execute_rook_action(
            selected,
            action,
            bool(payload.get("confirmed")),
            "dashboard",
        )

    @app.post("/api/v1/devices/{device_id}/notifications")
    def create_receiver_notification(
        device_id: str,
        request: Request,
        payload: dict[str, Any] = Body(...),
    ) -> dict[str, Any]:
        authorize(request)
        selected = _device_id(device_id)
        record = store.get(selected)
        if not record or not _is_android_display(record):
            raise HTTPException(status_code=404, detail="Android receiver not found")
        title = str(payload.get("title") or "Notification").replace("\n", " ").strip()[:80]
        message = str(payload.get("message") or "").replace("\r", " ").strip()[:320]
        chime = str(payload.get("chime") or "default").strip().lower()
        if chime not in {"none", "default", "doorbell", "alert"}:
            raise HTTPException(status_code=400, detail="Unsupported notification chime")
        duration = _integer(str(payload.get("duration") or 20), 20, 5, 300)
        try:
            public_actions, private_actions = normalize_notification_actions(
                payload.get("actions")
            )
        except RookInteractionError as err:
            raise HTTPException(status_code=400, detail=str(err)) from err
        image = b""
        image_media_type = "image/jpeg"
        camera_entity = str(payload.get("camera_entity") or "").strip().lower()
        if camera_entity:
            try:
                image, image_media_type = ha.camera_image(camera_entity)
            except ValueError as err:
                raise HTTPException(status_code=422, detail=str(err)) from err
        result = rook.publish_notification(
            selected,
            title=title,
            message=message,
            chime=chime,
            duration=duration,
            image=image,
            image_media_type=image_media_type,
            public_actions=public_actions,
            private_actions=private_actions,
        )
        store.touch(
            selected,
            {
                "last_notification_at": datetime.now(UTC).isoformat(timespec="seconds"),
                "last_notification_title": title,
                "last_notification_camera": camera_entity,
            },
        )
        publish_current(selected)
        return {"queued": True, **result}

    @app.get("/api/v1/devices/{device_id}/notifications/next")
    def next_receiver_notification(
        device_id: str,
        request: Request,
        after: int = 0,
        timeout: float = 25.0,
    ) -> dict[str, Any]:
        authorize_receiver(request, device_id)
        return rook.wait(_device_id(device_id), max(0, after), timeout)

    @app.get("/api/v1/devices/{device_id}/notifications/{notification_id}/image")
    def receiver_notification_image(
        device_id: str,
        notification_id: str,
        request: Request,
    ) -> Response:
        authorize_receiver(request, device_id)
        selected = rook.notification_image(_device_id(device_id), notification_id)
        if not selected:
            raise HTTPException(status_code=404, detail="Notification image not found")
        image, media_type = selected
        return Response(content=image, media_type=media_type)

    @app.post("/api/v1/devices/{device_id}/notifications/{notification_id}/dismiss")
    def dismiss_receiver_notification(
        device_id: str,
        notification_id: str,
        request: Request,
    ) -> dict[str, Any]:
        authorize_receiver(request, device_id)
        dismissed = rook.dismiss(_device_id(device_id), notification_id)
        return {"dismissed": dismissed}

    @app.post(
        "/api/v1/devices/{device_id}/notifications/{notification_id}/actions/{action_id}"
    )
    def receiver_notification_action(
        device_id: str,
        notification_id: str,
        action_id: str,
        request: Request,
        payload: dict[str, Any] = Body(default={}),
    ) -> dict[str, Any]:
        authorize_receiver(request, device_id)
        selected = _device_id(device_id)
        action = rook.notification_action(selected, notification_id, action_id)
        if not action:
            raise HTTPException(status_code=404, detail="Notification action not found")
        return execute_rook_action(
            selected,
            action,
            bool(payload.get("confirmed")),
            "notification",
        )

    @app.get("/studio", include_in_schema=False)
    def studio_redirect() -> RedirectResponse:
        return RedirectResponse("./studio/")

    @app.get("/", include_in_schema=False)
    def root_redirect() -> RedirectResponse:
        return RedirectResponse("./studio/")

    @app.get("/studio/", include_in_schema=False)
    def studio_page() -> FileResponse:
        path = Path(__file__).with_name("static") / "dashboard-studio.html"
        return FileResponse(path, media_type="text/html")

    @app.get("/api/v1/studio")
    def studio(request: Request) -> dict[str, Any]:
        authorize(request)
        return {
            "version": __version__,
            "profiles": dashboards.all(),
            "devices": [
                {
                    **_decorate_device(
                        record,
                        settings,
                        store,
                        dashboards.names(),
                    ),
                    "screen_history_count": len(
                        screen_history.list(str(record.get("device_id") or ""))
                    ),
                }
                for record in store.all()
            ],
            "models": {
                "X3": {"width": 528, "height": 792},
                "X4": {"width": 480, "height": 800},
                "X4_PRO": {"width": 480, "height": 800},
                "N4": {"width": 400, "height": 300},
                "ROOK": {"width": 480, "height": 480},
                "CHECKERS": {"width": 960, "height": 480},
            },
            "capabilities": {
                "layouts": [
                    "auto",
                    "single",
                    "rows",
                    "columns",
                    "grid",
                    "house_pulse",
                ],
                "styles": [
                    "value",
                    "gauge",
                    "progress",
                    "history",
                    "qr",
                    "image",
                    "name_card",
                ],
                "image_fits": ["cover", "contain"],
                "badge_themes": sorted(BADGE_THEMES),
                "text_scale": {"minimum": 60, "maximum": 180, "default": 100},
                "qr_scale": {"minimum": 50, "maximum": 150, "default": 100},
                "activation_types": ["always", "schedule", "condition"],
                "condition_operators": [
                    "equals",
                    "not_equals",
                    "above",
                    "below",
                    "contains",
                    "on",
                    "off",
                    "unavailable",
                ],
                "templates": [
                    "doorbell",
                    "alarm",
                    "energy",
                    "appliance",
                    "weather_alert",
                    "name_card",
                    "qr_code",
                ],
                "button_action": {
                    "mode": BUTTON_ACTION_MODE,
                    "buttons": list(CONFIGURABLE_BUTTONS),
                    "gestures": list(GESTURES),
                },
                "icons": [
                    "auto",
                    "home",
                    "temperature",
                    "humidity",
                    "battery",
                    "power",
                    "solar",
                    "wifi",
                    "storage",
                    "clock",
                    "weather",
                    "rain",
                    "light",
                    "lock",
                    "alert",
                ],
            },
        }

    @app.get("/api/v1/studio/entities")
    def studio_entities(request: Request) -> dict[str, Any]:
        authorize(request)
        entities, error = ha.catalog()
        synthetic = [
            {
                "entity_id": entity_id,
                "label": label,
                "state": state,
                "unit": unit,
                "domain": "device",
                "icon": "",
                "device_class": "",
            }
            for entity_id, label, state, unit in (
                ("device.battery", "Device Battery", "76", "%"),
                ("device.uptime", "Uptime", "2.4 h", ""),
                ("device.storage", "SD Card", "Ready", ""),
                ("device.memory", "Free Memory", "112 KB", ""),
                ("device.wifi", "Wi-Fi Signal", "-54", "dBm"),
                ("device.mode", "Display Mode", "Home Assistant", ""),
                ("device.wake", "Wake Reason", "Power Button", ""),
                ("device.usb", "USB Power", "Connected", ""),
            )
        ]
        return {"entities": synthetic + entities, "error": error}

    @app.post("/api/v1/studio/assets/profile-photo")
    async def upload_studio_profile_photo(
        request: Request,
        x_flexdisplay_filename: str | None = Header(default=None),
    ) -> dict[str, Any]:
        authorize(request)
        content_length = _integer(
            request.headers.get("Content-Length"),
            0,
            0,
            MAX_BADGE_PHOTO_BYTES + 1,
        )
        if content_length > MAX_BADGE_PHOTO_BYTES:
            raise HTTPException(
                status_code=413,
                detail="Profile photos may not exceed 5 MB",
            )
        content = await request.body()
        try:
            asset = dashboard_assets.put_profile_photo(
                content,
                x_flexdisplay_filename or "profile-photo",
            )
        except DashboardAssetValidationError as err:
            status = 413 if len(content) > MAX_BADGE_PHOTO_BYTES else 400
            raise HTTPException(status_code=status, detail=str(err)) from err
        return {"asset": asset}

    @app.get("/api/v1/studio/services")
    def studio_services(request: Request) -> dict[str, Any]:
        authorize(request)
        services, error = ha.service_catalog()
        return {"services": services, "error": error}

    @app.get("/api/v1/loading-screens")
    def loading_screen_settings(request: Request) -> dict[str, Any]:
        authorize(request)
        return loading_screens.payload()

    @app.get("/api/v1/loading-screens/{target}")
    def loading_screen_setting(target: str, request: Request) -> dict[str, Any]:
        authorize(request)
        if target != "default":
            _device_id(target)
        return {"target": target, "config": loading_screens.effective(target)}

    @app.put("/api/v1/loading-screens/{target}")
    def save_loading_screen_setting(
        target: str,
        payload: dict[str, Any],
        request: Request,
    ) -> dict[str, Any]:
        authorize(request)
        if target != "default":
            _device_id(target)
        try:
            config = loading_screens.put(target, payload)
        except LoadingScreenValidationError as err:
            raise HTTPException(status_code=400, detail=str(err)) from err
        refreshed = queue_loading_screen_refresh(target)
        return {"target": target, "config": config, "refresh_queued": refreshed}

    @app.delete("/api/v1/loading-screens/{device_id}")
    def reset_loading_screen_setting(
        device_id: str,
        request: Request,
    ) -> dict[str, Any]:
        authorize(request)
        selected = _device_id(device_id)
        config = loading_screens.reset(selected)
        store.queue_command(selected, "refresh")
        return {"target": selected, "config": config, "refresh_queued": [selected]}

    @app.post("/api/v1/loading-screens/{target}/logo")
    async def upload_loading_screen_logo(
        target: str,
        request: Request,
        x_flexdisplay_filename: str | None = Header(default=None),
    ) -> dict[str, Any]:
        authorize(request)
        if target != "default":
            _device_id(target)
        content_length = _integer(
            request.headers.get("Content-Length"),
            0,
            0,
            MAX_LOGO_BYTES + 1,
        )
        if content_length > MAX_LOGO_BYTES:
            raise HTTPException(status_code=413, detail="Logo may not exceed 2 MB")
        content = await request.body()
        try:
            config = loading_screens.put_logo(
                target,
                content,
                x_flexdisplay_filename or "logo",
            )
        except LoadingScreenValidationError as err:
            status = 413 if len(content) > MAX_LOGO_BYTES else 400
            raise HTTPException(status_code=status, detail=str(err)) from err
        return {
            "target": target,
            "config": config,
            "refresh_queued": queue_loading_screen_refresh(target),
        }

    @app.delete("/api/v1/loading-screens/{target}/logo")
    def delete_loading_screen_logo(
        target: str,
        request: Request,
    ) -> dict[str, Any]:
        authorize(request)
        if target != "default":
            _device_id(target)
        return {
            "target": target,
            "config": loading_screens.clear_logo(target),
            "refresh_queued": queue_loading_screen_refresh(target),
        }

    @app.post("/api/v1/loading-screens/{target}/preview")
    def preview_loading_screen(
        target: str,
        payload: dict[str, Any],
        request: Request,
    ) -> Response:
        authorize(request)
        if target != "default":
            _device_id(target)
        model = str(payload.get("model") or "X4").upper()
        default_width, default_height = (528, 792) if model == "X3" else (480, 800)
        width = _integer(
            str(payload.get("width") or default_width),
            default_width,
            240,
            1200,
        )
        height = _integer(
            str(payload.get("height") or default_height),
            default_height,
            240,
            1600,
        )
        selected_device_id = str(payload.get("device_id") or "") or (
            target if target != "default" else f"{model}-PREVIEW"
        )
        current = store.get(selected_device_id) or {
            "device_id": selected_device_id,
            "name": f"{model} Preview",
            "area": "Showroom",
            "model": model,
            "width": width,
            "height": height,
        }
        configured = settings.device(selected_device_id, width, height, model)
        profile = _effective_device(configured, current)
        raw_config = payload.get("config")
        if raw_config is not None and not isinstance(raw_config, dict):
            raise HTTPException(
                status_code=400,
                detail="Loading-screen configuration must be an object",
            )
        try:
            content, digest = loading_screens.render(
                selected_device_id,
                {
                    "name": profile.name,
                    "area": profile.area,
                    "profile": profile.profile,
                },
                width,
                height,
                config_override=raw_config,
                target_override=target,
            )
        except LoadingScreenValidationError as err:
            raise HTTPException(status_code=400, detail=str(err)) from err
        return Response(
            content=content,
            media_type="image/bmp",
            headers={"ETag": f'"{digest}"'},
        )

    @app.get(
        "/api/v1/devices/{device_id}/loading-screen.bmp",
        name="device_loading_screen",
    )
    def device_loading_screen(
        device_id: str,
        width: int | None = None,
        height: int | None = None,
    ) -> Response:
        selected = _device_id(device_id)
        current = store.get(selected) or {}
        model = str(
            current.get("model") or ("X4" if selected.startswith("X4-") else "X3")
        )
        default_width, default_height = (
            (480, 800) if "X4" in model.upper() else (528, 792)
        )
        selected_width = _integer(
            str(width or current.get("width") or default_width),
            default_width,
            240,
            1200,
        )
        selected_height = _integer(
            str(height or current.get("height") or default_height),
            default_height,
            240,
            1600,
        )
        configured = settings.device(
            selected,
            selected_width,
            selected_height,
            model,
        )
        profile = _effective_device(configured, current)
        content, digest = loading_screens.render(
            selected,
            {
                "name": profile.name,
                "area": profile.area,
                "profile": profile.profile,
            },
            selected_width,
            selected_height,
        )
        return Response(
            content=content,
            media_type="image/bmp",
            headers={
                "ETag": f'"{digest}"',
                "X-FlexDisplay-Loading-SHA256": digest,
            },
        )

    @app.get("/api/v1/photo-frame")
    def photo_frame_library(request: Request) -> dict[str, Any]:
        authorize(request)
        payload = photo_frames.payload()
        payload["capabilities"] = {
            "formats": ["JPEG", "PNG", "WebP", "BMP"],
            "fits": ["cover", "contain"],
            "rotations": [0, 90, 180, 270],
            "models": {
                "X3": {"width": 528, "height": 792},
                "X4": {"width": 480, "height": 800},
                "X4_PRO": {"width": 480, "height": 800},
                "ROOK": {"width": 480, "height": 480},
                "CHECKERS": {"width": 960, "height": 480},
            },
            "maximum_image_bytes": MAX_IMAGE_BYTES,
        }
        return payload

    @app.put("/api/v1/photo-frame/albums/{album_id}")
    def save_photo_frame_album(
        album_id: str,
        payload: dict[str, Any],
        request: Request,
    ) -> dict[str, Any]:
        authorize(request)
        try:
            album = photo_frames.put_album(album_id, payload)
        except PhotoFrameValidationError as err:
            raise HTTPException(status_code=400, detail=str(err)) from err
        return {"album_id": album_id, "album": album}

    @app.delete("/api/v1/photo-frame/albums/{album_id}")
    def delete_photo_frame_album(album_id: str, request: Request) -> dict[str, str]:
        authorize(request)
        try:
            photo_frames.delete_album(album_id)
        except KeyError as err:
            raise HTTPException(status_code=404, detail="Album not found") from err
        except PhotoFrameValidationError as err:
            raise HTTPException(status_code=409, detail=str(err)) from err
        return {"deleted": album_id}

    @app.post("/api/v1/photo-frame/albums/{album_id}/images")
    async def upload_photo_frame_image(
        album_id: str,
        request: Request,
        x_flexdisplay_filename: str | None = Header(default=None),
        x_flexdisplay_caption: str | None = Header(default=None),
        x_flexdisplay_image_fit: str | None = Header(default="cover"),
        x_flexdisplay_rotation: int | None = Header(default=0),
    ) -> dict[str, Any]:
        authorize(request)
        content_length = _integer(
            request.headers.get("Content-Length"), 0, 0, MAX_IMAGE_BYTES + 1
        )
        if content_length > MAX_IMAGE_BYTES:
            raise HTTPException(status_code=413, detail="Images may not exceed 8 MB")
        content = await request.body()
        try:
            item = photo_frames.add_image(
                album_id,
                content,
                filename=x_flexdisplay_filename or "photo",
                caption=x_flexdisplay_caption or "",
                fit=x_flexdisplay_image_fit or "cover",
                rotation=x_flexdisplay_rotation or 0,
            )
        except KeyError as err:
            raise HTTPException(status_code=404, detail="Album not found") from err
        except PhotoFrameValidationError as err:
            status = 413 if len(content) > MAX_IMAGE_BYTES else 400
            raise HTTPException(status_code=status, detail=str(err)) from err
        return {"album_id": album_id, "image": item}

    @app.post("/api/v1/photo-frame/albums/{album_id}/home-assistant")
    def import_photo_frame_home_assistant_image(
        album_id: str,
        payload: dict[str, Any],
        request: Request,
    ) -> dict[str, Any]:
        authorize(request)
        entity_id = str(payload.get("entity_id") or "")
        if not entity_id.startswith(("camera.", "image.")):
            raise HTTPException(
                status_code=400,
                detail="Choose a Home Assistant camera.* or image.* entity",
            )
        states, error = ha.fetch(
            (
                EntityConfig(
                    entity_id,
                    str(payload.get("caption") or entity_id),
                    style="image",
                    image_fit=str(payload.get("fit") or "cover"),
                ),
            )
        )
        if error or not states or not states[0].available or not states[0].image_bytes:
            raise HTTPException(
                status_code=502,
                detail=error or "Home Assistant image source is unavailable",
            )
        try:
            item = photo_frames.add_image(
                album_id,
                states[0].image_bytes,
                filename=f"{entity_id}.image",
                caption=str(payload.get("caption") or states[0].label),
                fit=str(payload.get("fit") or "cover"),
                rotation=_integer(str(payload.get("rotation") or 0), 0, 0, 270),
                source=entity_id,
            )
        except KeyError as err:
            raise HTTPException(status_code=404, detail="Album not found") from err
        except PhotoFrameValidationError as err:
            raise HTTPException(status_code=400, detail=str(err)) from err
        return {"album_id": album_id, "image": item}

    @app.put("/api/v1/photo-frame/albums/{album_id}/images/{item_id}")
    def update_photo_frame_image(
        album_id: str,
        item_id: str,
        payload: dict[str, Any],
        request: Request,
    ) -> dict[str, Any]:
        authorize(request)
        try:
            item = photo_frames.update_image(album_id, item_id, payload)
        except KeyError as err:
            raise HTTPException(status_code=404, detail="Photo not found") from err
        except PhotoFrameValidationError as err:
            raise HTTPException(status_code=400, detail=str(err)) from err
        return {"album_id": album_id, "image": item}

    @app.delete("/api/v1/photo-frame/albums/{album_id}/images/{item_id}")
    def delete_photo_frame_image(
        album_id: str,
        item_id: str,
        request: Request,
    ) -> dict[str, str]:
        authorize(request)
        try:
            photo_frames.delete_image(album_id, item_id)
        except KeyError as err:
            raise HTTPException(status_code=404, detail="Photo not found") from err
        return {"deleted": item_id}

    @app.get("/api/v1/photo-frame/albums/{album_id}/images/{item_id}/preview")
    def preview_photo_frame_image(
        album_id: str,
        item_id: str,
        request: Request,
        model: str = "X4",
        width: int | None = None,
        height: int | None = None,
    ) -> Response:
        authorize(request)
        selected_model = model.upper()
        default_width, default_height = (
            (528, 792) if selected_model == "X3" else (480, 800)
        )
        try:
            content = photo_frames.render(
                album_id,
                item_id,
                _integer(str(width or default_width), default_width, 240, 1200),
                _integer(str(height or default_height), default_height, 240, 1600),
            )
        except (KeyError, OSError) as err:
            raise HTTPException(status_code=404, detail="Photo not found") from err
        except PhotoFrameValidationError as err:
            raise HTTPException(status_code=400, detail=str(err)) from err
        return Response(content=content, media_type="image/png")

    @app.put("/api/v1/photo-frame/devices/{device_id}")
    def assign_photo_frame_album(
        device_id: str,
        payload: dict[str, Any],
        request: Request,
    ) -> dict[str, str]:
        authorize(request)
        selected = _device_id(device_id)
        try:
            assignment = photo_frames.assign(
                selected, str(payload.get("album_id") or "default")
            )
        except KeyError as err:
            raise HTTPException(status_code=404, detail="Album not found") from err
        existing = store.get(selected)
        inferred_model = (
            "XTEINK_X4"
            if not existing and selected.upper().startswith("X4-")
            else "XTEINK_X3"
            if not existing and selected.upper().startswith("X3-")
            else ""
        )
        store.provision(
            selected,
            {
                "assigned_mode": "photo_frame",
                **(
                    {"model": inferred_model, "model_reported": None}
                    if inferred_model
                    else {}
                ),
            },
        )
        store.queue_command(selected, "refresh")
        return assignment

    @app.get("/api/v1/photo-frame/devices/{device_id}/image")
    def photo_frame_device_image(
        device_id: str,
        request: Request,
        direction: str = "auto",
        x_flexdisplay_width: str | None = Header(default=None),
        x_flexdisplay_height: str | None = Header(default=None),
    ) -> Response:
        selected = _device_id(device_id)
        width = _integer(x_flexdisplay_width, 480, 240, 1200)
        height = _integer(x_flexdisplay_height, 800, 240, 1600)
        try:
            content, headers = photo_frames.next_for_device(
                selected,
                width=width,
                height=height,
                direction=direction,
            )
        except PhotoFrameValidationError as err:
            raise HTTPException(status_code=409, detail=str(err)) from err
        return Response(content=content, media_type="image/bmp", headers=headers)

    @app.put("/api/v1/studio/profiles/{profile_name}")
    def save_studio_profile(
        profile_name: str,
        payload: dict[str, Any],
        request: Request,
    ) -> dict[str, Any]:
        authorize(request)
        try:
            profile = dashboards.put(profile_name, payload)
        except DashboardValidationError as err:
            raise HTTPException(status_code=400, detail=str(err)) from err
        refreshed: list[str] = []
        for record in store.all():
            if (
                str(record.get("assigned_profile") or settings.default_profile)
                == profile_name
            ):
                store.queue_command(str(record["device_id"]), "refresh")
                refreshed.append(str(record["device_id"]))
        return {"profile": profile_payload(profile), "refresh_queued": refreshed}

    @app.delete("/api/v1/studio/profiles/{profile_name}")
    def delete_studio_profile(profile_name: str, request: Request) -> dict[str, Any]:
        authorize(request)
        assigned = [
            str(record["device_id"])
            for record in store.all()
            if str(record.get("assigned_profile") or settings.default_profile)
            == profile_name
        ]
        if assigned:
            raise HTTPException(
                status_code=409,
                detail=f"Profile is assigned to: {', '.join(assigned)}",
            )
        try:
            dashboards.delete(profile_name)
        except KeyError as err:
            raise HTTPException(status_code=404, detail="Profile not found") from err
        except DashboardValidationError as err:
            raise HTTPException(status_code=409, detail=str(err)) from err
        return {"deleted": profile_name}

    @app.post("/api/v1/studio/preview")
    def studio_preview(payload: dict[str, Any], request: Request) -> Response:
        authorize(request)
        raw_profile = payload.get("profile")
        if not isinstance(raw_profile, dict):
            raise HTTPException(
                status_code=400, detail="A dashboard profile is required"
            )
        try:
            draft = parse_profile("preview", raw_profile)
        except DashboardValidationError as err:
            raise HTTPException(status_code=400, detail=str(err)) from err
        model = str(payload.get("model") or "X4").upper()
        default_width, default_height = (
            (528, 792)
            if model == "X3"
            else (400, 300)
            if model == "N4"
            else (480, 480)
            if model == "ROOK"
            else (960, 480)
            if model == "CHECKERS"
            else (480, 800)
        )
        width = _integer(
            str(payload.get("width") or default_width), default_width, 240, 1200
        )
        height = _integer(
            str(payload.get("height") or default_height), default_height, 240, 1600
        )
        device_id = str(payload.get("device_id") or f"{model}-PREVIEW")
        record = store.get(device_id) or {
            "device_id": device_id,
            "name": f"{model} Preview",
            "model": model,
            "battery_percent": 76,
            "rssi": -54,
            "sd_ready": True,
            "usb_connected": True,
            "uptime_seconds": 8640,
            "free_heap": 114688,
            "mode": "home_assistant",
            "wake_reason": "power_button",
        }
        states, ha_error = fetch_dashboard_entities(
            DashboardProfileStore.entity_configs(draft)
        )
        pages = build_dashboard_pages(states, record, draft.pages)
        page_index = _integer(
            str(payload.get("page_index") or 0),
            0,
            0,
            max(0, len(pages) - 1),
        )
        page = pages[page_index]
        image = renderer.render(
            title=page.title,
            device=record,
            width=width,
            height=height,
            entities=page.entities,
            page_index=page_index,
            page_count=len(pages),
            ha_error=ha_error,
            layout=page.layout,
            button_actions=mappings_payload(record.get("button_action_mappings")),
            show_button_indicators=bool(record.get("button_action_indicators")),
        )
        return Response(
            content=image,
            media_type="image/png",
            headers={"X-FlexDisplay-Preview-HA-Error": "true" if ha_error else "false"},
        )

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
            descriptor = _device_capabilities(current)
            if not descriptor.firmware.manageable:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "No firmware release is configured"
                        if descriptor.family == "android_receiver"
                        else "Firmware installation is not managed by the Bridge for "
                        "this device family"
                    ),
                )
            try:
                record = queue_firmware_for_device(selected, current)
            except ValueError as err:
                raise HTTPException(status_code=409, detail=str(err)) from err
        else:
            current = store.get(selected)
            if current:
                descriptor = _device_capabilities(current)
                page_command = bool(re.fullmatch(r"page-[1-9][0-9]?", command))
                if (
                    command not in descriptor.management.actions
                    and not (
                        page_command
                        and descriptor.management.supports_page_selection
                    )
                ):
                    raise HTTPException(
                        status_code=409,
                        detail="Command is not supported by this device family",
                    )
            record = store.queue_command(selected, command)
        return {"queued": command, "device": record}

    @app.delete("/api/v1/devices/{device_id}/commands")
    def cancel_commands(
        device_id: str,
        request: Request,
        include_dispatched: bool = True,
    ) -> dict[str, Any]:
        authorize(request)
        selected = _device_id(device_id)
        record = store.clear_commands(
            selected,
            include_dispatched=include_dispatched,
        )
        if not record:
            raise HTTPException(status_code=404, detail="Device not found")
        return {
            "cancelled": True,
            "include_dispatched": include_dispatched,
            "device": _decorate_device(record, settings, store, dashboards.names()),
        }

    @app.post("/api/v1/devices/{device_id}/firmware/retry")
    def retry_firmware(device_id: str, request: Request) -> dict[str, Any]:
        authorize(request)
        selected = _device_id(device_id)
        current = store.get(selected)
        if not current:
            raise HTTPException(status_code=404, detail="Device has not checked in")
        blockers = _firmware_retry_blockers(current, settings, store)
        if blockers:
            raise HTTPException(status_code=409, detail="; ".join(blockers))
        try:
            record = store.retry_firmware_install(
                selected,
                settings.firmware.version,
                canary_required=settings.firmware.canary_required,
                max_parallel=settings.firmware.max_parallel,
                retry_limit=settings.firmware.retry_limit,
                retry_backoff_seconds=settings.firmware.retry_backoff_seconds,
            )
        except ValueError as err:
            raise HTTPException(status_code=409, detail=str(err)) from err
        return {
            "retried": True,
            "device": _decorate_device(record, settings, store, dashboards.names()),
        }

    @app.post("/api/v1/firmware/rollout/reset")
    def reset_firmware_rollout(request: Request) -> dict[str, Any]:
        authorize(request)
        try:
            rollout = store.reset_firmware_rollout(
                settings.firmware.version,
                canary_required=settings.firmware.canary_required,
            )
        except ValueError as err:
            raise HTTPException(status_code=409, detail=str(err)) from err
        return {"reset": True, "rollout": rollout}

    @app.get("/api/v1/devices/{device_id}/firmware/progress")
    def firmware_progress(
        device_id: str,
        x_flexdisplay_id: str | None = Header(default=None),
        x_flexdisplay_command_id: str | None = Header(default=None),
        x_flexdisplay_firmware_stage: str | None = Header(default=None),
        x_flexdisplay_firmware_percent: str | None = Header(default=None),
        x_flexdisplay_firmware_detail: str | None = Header(default=None),
    ) -> dict[str, Any]:
        selected = _device_id(device_id)
        if _device_id(x_flexdisplay_id) != selected:
            raise HTTPException(status_code=409, detail="Device identity mismatch")
        stage = str(x_flexdisplay_firmware_stage or "").strip().lower()
        if stage not in FIRMWARE_PROGRESS_STAGES:
            raise HTTPException(
                status_code=400, detail="Unsupported firmware progress stage"
            )
        record, cancel_requested = store.record_firmware_progress(
            selected,
            str(x_flexdisplay_command_id or ""),
            stage,
            _integer(x_flexdisplay_firmware_percent, 0, 0, 100),
            str(x_flexdisplay_firmware_detail or ""),
        )
        if not record:
            raise HTTPException(status_code=404, detail="Device not found")
        return {
            "recorded": not cancel_requested,
            "cancel_requested": cancel_requested,
            "stage": stage,
        }

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
        external_usb_evidence = payload.get("external_usb_evidence")
        blockers = _usb_recovery_blockers(
            current,
            settings,
            store,
            external_usb_evidence if isinstance(external_usb_evidence, dict) else None,
        )
        if blockers:
            raise HTTPException(status_code=409, detail="; ".join(blockers))
        target = str(payload.get("expected_target_version") or "")
        command_id = str(payload.get("expected_command_id") or "")
        if target != settings.firmware.version:
            raise HTTPException(
                status_code=409, detail="Expected target does not match configuration"
            )
        try:
            record = store.verify_usb_recovery(
                selected,
                target,
                command_id,
                canary_required=settings.firmware.canary_required,
                max_checkin_age_seconds=USB_RECOVERY_MAX_CHECKIN_AGE_SECONDS,
                external_usb_evidence=(
                    external_usb_evidence
                    if isinstance(external_usb_evidence, dict)
                    else None
                ),
            )
        except ValueError as err:
            raise HTTPException(status_code=409, detail=str(err)) from err
        return {
            "verified": True,
            "verification_method": "usb_recovery",
            "device": _decorate_device(record, settings, store, dashboards.names()),
            "audit": record.get("last_usb_recovery_verification"),
        }

    @app.put("/api/v1/devices/{device_id}/provision")
    def provision_device(
        device_id: str, payload: dict[str, Any], request: Request
    ) -> dict[str, Any]:
        authorize(request)
        selected = _device_id(device_id)
        current = store.get(selected)
        if not current:
            raise HTTPException(status_code=404, detail="Device has not checked in")
        descriptor = _device_capabilities(current)
        if not descriptor.management.supports_provisioning:
            raise HTTPException(
                status_code=409,
                detail="Provisioning is not supported for this device family",
            )
        assignment = _provisioning_assignment(payload, selected, dashboards.names())
        if not assignment:
            raise HTTPException(
                status_code=400, detail="No provisioning fields supplied"
            )
        assignment, skipped = _filter_provisioning_assignment(
            assignment, descriptor
        )
        if skipped:
            raise HTTPException(
                status_code=409,
                detail=(
                    "These settings are not supported for this device family: "
                    + ", ".join(sorted(skipped))
                ),
            )
        assignment["assigned_policy_name"] = "custom"
        assignment["assigned_policy_revision"] = store.next_policy_revision()
        record = store.provision(selected, assignment)
        store.queue_command(selected, "refresh")
        return {
            "device": _decorate_device(record, settings, store, dashboards.names()),
        }

    @app.get("/api/v1/fleet/policies")
    def fleet_policies() -> dict[str, Any]:
        records = [
            _decorate_device(record, settings, store, dashboards.names())
            for record in store.all()
        ]
        photo_library = photo_frames.payload()
        policy_profiles = fleet_policy_profiles()
        rollout = store.firmware_rollout()
        maintenance = _firmware_maintenance_status(settings)
        return {
            "profiles": [
                {
                    "id": policy_id,
                    "label": preset["label"],
                    "description": preset["description"],
                    "settings": preset["settings"],
                    "built_in": bool(preset.get("built_in")),
                    "updated_at": preset.get("updated_at"),
                }
                for policy_id, preset in policy_profiles.items()
            ],
            "firmware": {
                "version": settings.firmware.version,
                "size": settings.firmware.size,
                "sha256": settings.firmware.sha256,
                "minimum_battery_percent": settings.firmware.minimum_battery_percent,
                "canary_required": settings.firmware.canary_required,
                "require_usb_for_canary": settings.firmware.require_usb_for_canary,
                "max_parallel": settings.firmware.max_parallel,
                "maintenance": maintenance,
                "mirror": _public_mirror_status(
                    firmware_mirror.status(settings.firmware)
                ),
                "rollout": rollout,
            },
            "dashboard_profiles": dashboards.names(),
            "photo_albums": [
                {
                    "id": album_id,
                    "name": str(album.get("name") or album_id),
                    "item_count": len(album.get("items") or []),
                }
                for album_id, album in photo_library.get("albums", {}).items()
            ],
            "groups": [
                group_payload(group)
                for group in store.fleet_groups().values()
            ],
            "summary": {
                "devices": len(records),
                "online": sum(bool(record.get("online")) for record in records),
                "healthy": sum(
                    record.get("health_state") == "healthy" for record in records
                ),
                "pending_policy": sum(
                    record.get("policy_sync_state") == "pending" for record in records
                ),
                "x3": sum(
                    "X3" in str(record.get("model") or "").upper() for record in records
                ),
                "x4": sum(
                    "X4" in str(record.get("model") or "").upper() for record in records
                ),
            },
            "devices": [
                {
                    "device_id": record.get("device_id"),
                    "name": record.get("name"),
                    "model": record.get("model"),
                    "device_family": record.get("device_family"),
                    "firmware_provider": record.get("firmware_provider"),
                    "identity": record.get("identity") or {},
                    "supported_actions": record.get("supported_actions") or [],
                    "device_capabilities": record.get("device_capabilities") or {},
                    "online": record.get("online"),
                    "power_state": record.get("power_state"),
                    "battery_percent": record.get("battery_percent"),
                    "firmware": record.get("firmware"),
                    "health_state": record.get("health_state"),
                    "policy_name": record.get("assigned_policy_name"),
                    "policy_revision": record.get("policy_revision"),
                    "reported_policy_revision": record.get("reported_policy_revision"),
                    "policy_sync_state": record.get("policy_sync_state"),
                    "rendering_profile": record.get("assigned_rendering_profile")
                    or "standard",
                    "open_display_transport_policy": record.get(
                        "assigned_open_display_transport_policy"
                    )
                    or "auto",
                    "open_display_last_transport": record.get(
                        "open_display_last_transport"
                    )
                    or "none",
                    "open_display_fallback": record.get("open_display_fallback") or "",
                    "open_display_min_free_heap": record.get(
                        "open_display_min_free_heap"
                    ),
                    "open_display_min_largest_block": record.get(
                        "open_display_min_largest_block"
                    ),
                    "open_display_lan_memory_blocked": record.get(
                        "open_display_lan_memory_blocked"
                    ),
                    "last_seen": record.get("last_seen"),
                    "provisioning_updated_at": record.get("provisioning_updated_at"),
                    "update_available": record.get("update_available"),
                    "firmware_install_ready": record.get("firmware_install_ready"),
                    "firmware_install_blockers": record.get("firmware_install_blockers")
                    or [],
                    "firmware_update_status": record.get("firmware_update_status")
                    or "idle",
                    "firmware_update_stage": record.get("firmware_update_stage")
                    or "idle",
                    "firmware_update_percent": record.get("firmware_update_percent")
                    or 0,
                    "firmware_update_error": record.get("firmware_update_error"),
                    "firmware_retry_ready": record.get("firmware_retry_ready"),
                    "firmware_retry_blockers": record.get("firmware_retry_blockers")
                    or [],
                    "usb_connected": record.get("usb_connected"),
                    "sd_ready": record.get("sd_ready"),
                }
                for record in records
            ],
        }

    @app.put("/api/v1/fleet/policies/{profile_id}")
    def save_fleet_policy_profile(
        profile_id: str,
        payload: dict[str, Any],
        request: Request,
    ) -> dict[str, Any]:
        authorize(request)
        selected = profile_id.strip().lower()
        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{1,31}", selected):
            raise HTTPException(status_code=400, detail="Invalid policy profile ID")
        if selected in FLEET_POLICY_PRESETS:
            raise HTTPException(
                status_code=409, detail="Built-in profiles cannot be replaced"
            )
        raw_settings = payload.get("settings")
        if not isinstance(raw_settings, dict):
            raise HTTPException(status_code=400, detail="Policy settings are required")
        allowed = {
            "live_mode",
            "intelligent_sleep",
            "stay_awake_on_usb",
            "refresh_interval_seconds",
            "manual_sleep_seconds",
            "manual_wake_grace_seconds",
            "critical_battery_percent",
            "low_battery_percent",
            "low_battery_multiplier",
            "unchanged_image_multiplier",
            "active_start",
            "active_end",
            "timezone",
            "rendering_profile",
            "open_display_transport_policy",
        }
        unknown = set(raw_settings) - allowed
        if unknown:
            raise HTTPException(
                status_code=400,
                detail=f"Unsupported policy setting: {min(unknown)}",
            )
        for field in ("live_mode", "intelligent_sleep", "stay_awake_on_usb"):
            if field in raw_settings and not isinstance(raw_settings[field], bool):
                raise HTTPException(
                    status_code=400, detail=f"{field} must be true or false"
                )
        assignment = _provisioning_assignment(
            raw_settings,
            "POLICY-VALIDATION",
            dashboards.names(),
        )
        normalised = {
            key.removeprefix("assigned_"): value for key, value in assignment.items()
        }
        label = (
            _header_value(payload.get("label"))[:48]
            or selected.replace("_", " ").title()
        )
        description = _header_value(payload.get("description"))[:160]
        profile = store.put_custom_policy_profile(
            selected,
            {"label": label, "description": description, "settings": normalised},
        )
        return {"saved": True, "profile": {**profile, "built_in": False}}

    @app.delete("/api/v1/fleet/policies/{profile_id}")
    def delete_fleet_policy_profile(
        profile_id: str, request: Request
    ) -> dict[str, Any]:
        authorize(request)
        selected = profile_id.strip().lower()
        if selected in FLEET_POLICY_PRESETS:
            raise HTTPException(
                status_code=409, detail="Built-in profiles cannot be deleted"
            )
        if not store.delete_custom_policy_profile(selected):
            raise HTTPException(status_code=404, detail="Policy profile not found")
        return {"deleted": selected}

    @app.get("/api/v1/fleet/groups")
    def list_fleet_groups(request: Request) -> dict[str, Any]:
        authorize(request)
        return {
            "groups": [
                group_payload(group)
                for group in store.fleet_groups().values()
            ]
        }

    @app.put("/api/v1/fleet/groups/{group_id}")
    def save_fleet_group(
        group_id: str,
        payload: dict[str, Any],
        request: Request,
    ) -> dict[str, Any]:
        authorize(request)
        selected = str(group_id or "").strip().lower()
        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{1,47}", selected):
            raise HTTPException(status_code=400, detail="Invalid fleet group ID")
        raw_ids = payload.get("device_ids") or []
        if not isinstance(raw_ids, list):
            raise HTTPException(status_code=400, detail="device_ids must be an array")
        device_ids = [_device_id(str(device_id)) for device_id in raw_ids]
        filters = payload.get("filters") or {}
        if not isinstance(filters, dict):
            raise HTTPException(status_code=400, detail="filters must be an object")
        allowed_filters = {
            "family",
            "firmware_provider",
            "model_key",
            "area",
            "power_class",
            "online",
        }
        unexpected = sorted(set(filters) - allowed_filters)
        if unexpected:
            raise HTTPException(
                status_code=400,
                detail=f"Unsupported group filters: {', '.join(unexpected)}",
            )
        if "online" in filters and not isinstance(filters["online"], bool):
            raise HTTPException(status_code=400, detail="online filter must be boolean")
        if not device_ids and not filters:
            raise HTTPException(
                status_code=400,
                detail="Select devices or configure at least one group filter",
            )
        group = store.put_fleet_group(
            selected,
            label=_header_value(payload.get("label"))[:64]
            or selected.replace("_", " ").title(),
            description=_header_value(payload.get("description"))[:180],
            device_ids=device_ids,
            filters={key: value for key, value in filters.items() if value != ""},
        )
        return {"saved": True, "group": group_payload(group)}

    @app.delete("/api/v1/fleet/groups/{group_id}")
    def delete_fleet_group(group_id: str, request: Request) -> dict[str, Any]:
        authorize(request)
        selected = str(group_id or "").strip().lower()
        if not store.delete_fleet_group(selected):
            raise HTTPException(status_code=404, detail="Fleet group not found")
        return {"deleted": selected}

    @app.post("/api/v1/fleet/policy/preview")
    def preview_fleet_policy(
        payload: dict[str, Any], request: Request
    ) -> dict[str, Any]:
        authorize(request)
        policy_id = str(payload.get("profile") or "")
        preset = fleet_policy_profiles().get(policy_id)
        if not preset:
            raise HTTPException(status_code=400, detail="Unknown fleet policy profile")
        scope = str(payload.get("scope") or "all").lower()
        group_id = str(payload.get("group_id") or "").strip().lower()
        requested_ids = {
            _device_id(str(device_id)) for device_id in payload.get("device_ids") or []
        }
        records = fleet_scope_records(scope, requested_ids, group_id)
        overrides = payload.get("overrides") or {}
        if not isinstance(overrides, dict):
            raise HTTPException(status_code=400, detail="Policy overrides must be an object")
        merged = {**preset["settings"], **overrides}
        selected_mode = str(payload.get("mode") or "").strip().lower()
        if selected_mode and selected_mode != "unchanged":
            if selected_mode not in SUPPORTED_MODES:
                raise HTTPException(status_code=400, detail="Unsupported default application")
            merged["mode"] = selected_mode
        dashboard_profile = str(payload.get("dashboard_profile") or "").strip()
        if dashboard_profile:
            if dashboard_profile not in dashboards.names():
                raise HTTPException(status_code=400, detail="Unknown dashboard profile")
            merged["profile"] = dashboard_profile
        photo_album = str(payload.get("photo_album") or "").strip()
        if photo_album:
            if photo_album not in photo_frames.payload().get("albums", {}):
                raise HTTPException(status_code=400, detail="Unknown photo-frame album")
            merged["mode"] = "photo_frame"
        targets: list[dict[str, Any]] = []
        excluded: dict[str, str] = {}
        filtered_fields: dict[str, list[str]] = {}
        offline: list[str] = []
        for record in records:
            device_id = str(record.get("device_id") or "")
            descriptor = _device_capabilities(record)
            if not descriptor.management.supports_fleet_policy:
                excluded[device_id] = "Fleet policy is not supported by this family"
                continue
            assignment = _provisioning_assignment(merged, device_id, dashboards.names())
            assignment, skipped = _filter_provisioning_assignment(assignment, descriptor)
            if "mode" in skipped:
                excluded[device_id] = "Selected mode is not supported by this family"
                continue
            if skipped:
                filtered_fields[device_id] = sorted(skipped)
            decorated = _decorate_device(record, settings, store)
            if not decorated.get("online"):
                offline.append(device_id)
            targets.append(
                {
                    "device_id": device_id,
                    "family": descriptor.family,
                    "identity_source": decorated["identity"]["source"],
                    "online": bool(decorated.get("online")),
                    "would_change": sorted(
                        key.removeprefix("assigned_")
                        for key, value in assignment.items()
                        if record.get(key) != value
                    ),
                }
            )
        return {
            "preview": True,
            "profile": policy_id,
            "scope": scope,
            "group_id": group_id,
            "targets": targets,
            "target_count": len(targets),
            "excluded": excluded,
            "filtered_fields": filtered_fields,
            "offline": offline,
        }

    @app.post("/api/v1/fleet/firmware/preview")
    def preview_fleet_firmware(
        payload: dict[str, Any], request: Request
    ) -> dict[str, Any]:
        authorize(request)
        scope = str(payload.get("scope") or "all").lower()
        group_id = str(payload.get("group_id") or "").strip().lower()
        requested_ids = {
            _device_id(str(device_id)) for device_id in payload.get("device_ids") or []
        }
        records = fleet_scope_records(scope, requested_ids, group_id)
        target = settings.firmware.version
        channel_error = _firmware_metadata_error(settings)
        eligible: list[str] = []
        ready: list[str] = []
        blocked: dict[str, list[str]] = {}
        excluded: dict[str, str] = {}
        offline: list[str] = []
        for record in records:
            device_id = str(record.get("device_id") or "")
            descriptor = _device_capabilities(record)
            if not descriptor.supports_xteink_ota:
                excluded[device_id] = "Not eligible for the X3/X4 firmware channel"
                continue
            if _firmware_version(target) <= _firmware_version(str(record.get("firmware") or "")):
                excluded[device_id] = "Already running this release or newer"
                continue
            eligible.append(device_id)
            blockers = [channel_error] if channel_error else _firmware_install_blockers(
                record, settings, store
            )
            if blockers:
                blocked[device_id] = blockers
            else:
                ready.append(device_id)
            if not _decorate_device(record, settings, store).get("online"):
                offline.append(device_id)
        return {
            "preview": True,
            "target_version": target,
            "scope": scope,
            "group_id": group_id,
            "channel_ready": not channel_error,
            "channel_error": channel_error,
            "eligible": eligible,
            "ready": ready,
            "blocked": blocked,
            "excluded": excluded,
            "offline": offline,
        }

    @app.put("/api/v1/fleet/policy")
    def apply_fleet_policy(payload: dict[str, Any], request: Request) -> dict[str, Any]:
        authorize(request)
        policy_id = str(payload.get("profile") or "")
        preset = fleet_policy_profiles().get(policy_id)
        if not preset:
            raise HTTPException(status_code=400, detail="Unknown fleet policy profile")

        scope = str(payload.get("scope") or "all").lower()
        group_id = str(payload.get("group_id") or "").strip().lower()
        requested_ids = {
            _device_id(str(device_id))
            for device_id in (payload.get("device_ids") or [])
        }
        scoped_records = fleet_scope_records(scope, requested_ids, group_id)
        excluded: dict[str, str] = {}

        overrides = payload.get("overrides") or {}
        if not isinstance(overrides, dict):
            raise HTTPException(
                status_code=400, detail="Policy overrides must be an object"
            )
        merged = {**preset["settings"], **overrides}
        selected_mode = str(payload.get("mode") or "").strip().lower()
        if selected_mode and selected_mode != "unchanged":
            if selected_mode not in SUPPORTED_MODES:
                raise HTTPException(
                    status_code=400, detail="Unsupported default application"
                )
            merged["mode"] = selected_mode
        dashboard_profile = str(payload.get("dashboard_profile") or "").strip()
        if dashboard_profile:
            if dashboard_profile not in dashboards.names():
                raise HTTPException(status_code=400, detail="Unknown dashboard profile")
            merged["profile"] = dashboard_profile
        photo_album = str(payload.get("photo_album") or "").strip()
        if photo_album:
            if photo_album not in photo_frames.payload().get("albums", {}):
                raise HTTPException(status_code=400, detail="Unknown photo-frame album")
            merged["mode"] = "photo_frame"
        revision = store.next_policy_revision()
        targets: list[str] = []
        filtered_fields: dict[str, list[str]] = {}
        for record in scoped_records:
            device_id = str(record.get("device_id") or "")
            descriptor = _device_capabilities(record)
            if not descriptor.management.supports_fleet_policy:
                excluded[device_id] = (
                    "Device does not advertise a compatible fleet policy contract"
                )
                continue
            assignment = _provisioning_assignment(merged, device_id, dashboards.names())
            assignment, skipped = _filter_provisioning_assignment(
                assignment, descriptor
            )
            if "mode" in skipped:
                excluded[device_id] = (
                    f"Mode {selected_mode or merged.get('mode')} is not supported "
                    "by this device family"
                )
                continue
            if skipped:
                filtered_fields[device_id] = sorted(skipped)
            assignment["assigned_policy_name"] = policy_id
            assignment["assigned_policy_revision"] = revision
            store.provision(device_id, assignment)
            if photo_album:
                photo_frames.assign(device_id, photo_album)
            if str(payload.get("delivery") or "when_awake") == "apply_now":
                store.queue_command(device_id, "refresh")
            targets.append(device_id)

        if not targets:
            raise HTTPException(
                status_code=404, detail="No devices matched the fleet scope"
            )
        return {
            "accepted": True,
            "profile": policy_id,
            "scope": scope,
            "group_id": group_id,
            "revision": revision,
            "delivery": str(payload.get("delivery") or "when_awake"),
            "mode": selected_mode or "unchanged",
            "dashboard_profile": dashboard_profile,
            "photo_album": photo_album,
            "targets": targets,
            "excluded": excluded,
            "filtered_fields": filtered_fields,
            "pending_acknowledgements": len(targets),
        }

    @app.post("/api/v1/fleet/firmware/install")
    def install_fleet_firmware(
        payload: dict[str, Any],
        request: Request,
    ) -> dict[str, Any]:
        authorize(request)
        target = settings.firmware.version
        if str(payload.get("confirm_version") or "") != target:
            raise HTTPException(
                status_code=400,
                detail="Confirm the exact configured firmware version before starting",
            )
        error = _firmware_metadata_error(settings)
        if error:
            raise HTTPException(status_code=409, detail=error)
        scope = str(payload.get("scope") or "all").lower()
        group_id = str(payload.get("group_id") or "").strip().lower()
        requested_ids = {
            _device_id(str(device_id))
            for device_id in (payload.get("device_ids") or [])
        }
        records = fleet_scope_records(scope, requested_ids, group_id)
        eligible_records: list[dict[str, Any]] = []
        excluded: dict[str, str] = {}
        for record in records:
            device_id = str(record.get("device_id") or "")
            if _device_capabilities(record).supports_xteink_ota:
                eligible_records.append(record)
            else:
                excluded[device_id] = (
                    "Device is not eligible for the X3/X4 firmware channel"
                )
        targets = [
            str(record.get("device_id") or "")
            for record in eligible_records
            if _firmware_version(target)
            > _firmware_version(str(record.get("firmware") or ""))
        ]
        if not targets:
            raise HTTPException(
                status_code=409,
                detail=(
                    "No selected device is eligible for this X3/X4 firmware release"
                    if records and not eligible_records
                    else "No selected device requires this firmware release"
                ),
            )
        try:
            store.plan_firmware_rollout(
                target,
                targets,
                scope=scope,
                canary_required=settings.firmware.canary_required,
            )
        except ValueError as err:
            raise HTTPException(status_code=409, detail=str(err)) from err
        advanced = advance_firmware_rollout()
        return {
            "accepted": True,
            "target_version": target,
            "scope": scope,
            "group_id": group_id,
            "targets": targets,
            "excluded": excluded,
            **advanced,
        }

    @app.get("/api/v1/content-channels")
    def list_content_channels(request: Request) -> dict[str, Any]:
        authorize(request)
        return content_channels.payload()

    @app.put("/api/v1/content-channels/{channel_id}")
    def save_content_channel(
        channel_id: str,
        payload: dict[str, Any],
        request: Request,
    ) -> dict[str, Any]:
        authorize(request)
        try:
            channel = content_channels.put(channel_id, payload)
        except ContentChannelValidationError as err:
            raise HTTPException(status_code=400, detail=str(err)) from err
        refreshed: list[str] = []
        for device_id, assigned in content_channels.payload()["assignments"].items():
            if assigned == channel_id and store.get(device_id):
                store.queue_command(device_id, "refresh")
                refreshed.append(device_id)
        return {"channel": channel, "refresh_queued": refreshed}

    @app.delete("/api/v1/content-channels/{channel_id}")
    def delete_content_channel(channel_id: str, request: Request) -> dict[str, Any]:
        authorize(request)
        try:
            content_channels.delete(channel_id)
        except KeyError as err:
            raise HTTPException(
                status_code=404, detail="Content channel not found"
            ) from err
        return {"deleted": channel_id}

    @app.put("/api/v1/content-channels/devices/{device_id}")
    def assign_content_channel(
        device_id: str,
        payload: dict[str, Any],
        request: Request,
    ) -> dict[str, Any]:
        authorize(request)
        selected = _device_id(device_id)
        if not store.get(selected):
            raise HTTPException(status_code=404, detail="Device has not checked in")
        channel_id = str(payload.get("channel_id") or "")
        try:
            content_channels.assign(selected, channel_id)
        except ContentChannelValidationError as err:
            raise HTTPException(status_code=400, detail=str(err)) from err
        store.set_dashboard_page(selected, 0, 1, "", [], "", "content_channel")
        store.queue_command(selected, "refresh")
        return {"device_id": selected, "channel_id": channel_id, "refresh_queued": True}

    @app.post("/api/v1/content-channels/preview")
    def preview_content_channel(
        payload: dict[str, Any],
        request: Request,
    ) -> Response:
        authorize(request)
        model = str(payload.get("model") or "X4").upper()
        width = _integer(payload.get("width"), 480 if "4" in model else 528, 240, 1200)
        height = _integer(
            payload.get("height"), 800 if "4" in model else 792, 240, 1600
        )
        try:
            draft = parse_channel("preview", payload.get("channel") or {})
            pages = content_channels.pages_for_channel(
                draft, "X4-PREVIEW", ["HOME ASSISTANT"]
            )
        except ContentChannelValidationError as err:
            raise HTTPException(status_code=400, detail=str(err)) from err
        selected = next((page for page in pages if page.kind != "dashboard"), None)
        if selected is None:
            raise HTTPException(
                status_code=400, detail="Add a Message, Quote, or News item to preview"
            )
        return Response(
            content=render_content_page(
                selected,
                device_name="Preview display",
                width=width,
                height=height,
                page_index=0,
                page_count=max(1, len(pages)),
            ),
            media_type="image/png",
        )

    @app.get("/api/v1/content-packs")
    def list_content_packs(request: Request) -> dict[str, Any]:
        authorize(request)
        return content_packs.payload()

    @app.post("/api/v1/content-packs")
    async def upload_content_pack(request: Request) -> dict[str, Any]:
        authorize(request)
        archive = await _bounded_request_body(
            request, MAX_PACK_BYTES, "Content pack is too large"
        )
        try:
            pack = content_packs.install(archive)
        except ContentPackConflictError as err:
            raise HTTPException(status_code=409, detail=str(err)) from err
        except ContentPackError as err:
            raise HTTPException(status_code=400, detail=str(err)) from err
        return {"pack": pack}

    @app.post("/api/v1/content-packs/quick-cards")
    async def build_quick_card_pack(request: Request) -> dict[str, Any]:
        """Create a device-ready Quick Cards pack directly from Studio."""
        authorize(request)
        body = await _bounded_request_body(
            request,
            MAX_QUICK_CARD_REQUEST_BYTES,
            "Quick Cards request is too large",
        )
        try:
            payload = json.loads(body)
        except (ValueError, UnicodeDecodeError, RecursionError) as err:
            raise HTTPException(status_code=400, detail="Quick Cards JSON is invalid") from err
        if not isinstance(payload, dict):
            raise HTTPException(status_code=400, detail="Quick Cards JSON must be an object")
        try:
            pack = content_packs.build_quick_cards(payload)
        except ContentPackConflictError as err:
            raise HTTPException(status_code=409, detail=str(err)) from err
        except ContentPackError as err:
            raise HTTPException(status_code=400, detail=str(err)) from err
        return {"pack": pack}

    @app.post("/api/v1/content-packs/{version}/rollout")
    def rollout_content_pack(
        version: str, payload: dict[str, Any], request: Request
    ) -> dict[str, Any]:
        authorize(request)
        scope = str(payload.get("scope") or "devices").lower()
        selected = payload.get("device_ids") or []
        if not isinstance(selected, list):
            raise HTTPException(status_code=400, detail="Device IDs must be a list")
        if scope not in {"all", "x3", "x4", "devices"}:
            raise HTTPException(status_code=400, detail="Unsupported content scope")
        if scope == "devices" and not selected:
            raise HTTPException(status_code=400, detail="Select at least one device")
        requested_ids = {_device_id(str(value)) for value in selected}
        scoped = fleet_scope_records(scope, requested_ids)
        known = {str(item.get("device_id") or "") for item in store.all()}
        if scope == "devices":
            missing = sorted(requested_ids - known)
            if missing:
                raise HTTPException(
                    status_code=404, detail=f"Unknown devices: {', '.join(missing)}"
                )
            unsupported = sorted(
                str(record.get("device_id") or "")
                for record in scoped
                if not _device_capabilities(record).supports_xteink_ota
            )
            if unsupported:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "Content packs require an X3/X4 content-capable device: "
                        f"{', '.join(unsupported)}"
                    ),
                )
        eligible = [
            record
            for record in scoped
            if _device_capabilities(record).supports_xteink_ota
        ]
        device_ids = [str(record.get("device_id") or "") for record in eligible]
        if not device_ids:
            raise HTTPException(
                status_code=404,
                detail="No X3/X4 content-capable devices matched the content scope",
            )
        scheduled_for = str(payload.get("scheduled_for") or "").strip()
        try:
            assignments = content_packs.assign(
                version,
                device_ids,
                scope=scope,
                scheduled_for=scheduled_for,
            )
        except ContentPackError as err:
            status_code = 404 if str(err) == "Unknown content pack" else 400
            raise HTTPException(status_code=status_code, detail=str(err)) from err
        if not scheduled_for:
            for device_id in device_ids:
                store.queue_command(device_id, "refresh")
        return {
            "version": version,
            "scope": scope,
            "device_ids": device_ids,
            "assignments": assignments,
        }

    @app.get(
        "/api/v1/content-packs/{version}/manifest.json",
        name="device_content_pack_manifest",
    )
    def content_pack_manifest(version: str, request: Request) -> Response:
        access_token = request.query_params.get("access_token", "")
        try:
            content, digest = content_packs.manifest(
                version, str(request.base_url).rstrip("/"), access_token
            )
        except (ContentPackAccessError, ContentPackError) as err:
            raise HTTPException(status_code=404, detail=str(err)) from err
        return Response(
            content=content,
            media_type="application/json",
            headers={"X-Content-SHA256": digest, "Cache-Control": "no-cache"},
        )

    @app.get("/api/v1/content-packs/{version}/files/{source:path}")
    def content_pack_file(version: str, source: str, request: Request) -> FileResponse:
        access_token = request.query_params.get("access_token", "")
        try:
            path = content_packs.file_path(version, source, access_token)
        except (ContentPackAccessError, ContentPackError) as err:
            raise HTTPException(status_code=404, detail=str(err)) from err
        return FileResponse(path)

    @app.get("/api/v1/screen")
    def screen(
        response: Response,
        request: Request,
        x_flexdisplay_id: str | None = Header(default=None),
        x_flexdisplay_width: str | None = Header(default=None),
        x_flexdisplay_height: str | None = Header(default=None),
        x_flexdisplay_model: str | None = Header(default=None),
        x_flexdisplay_firmware: str | None = Header(default=None),
        x_flexdisplay_firmware_artifact: str | None = Header(default=None),
        x_flexdisplay_board_id: str | None = Header(default=None),
        x_flexdisplay_hardware_revision: str | None = Header(default=None),
        x_flexdisplay_mcu_family: str | None = Header(default=None),
        x_flexdisplay_flash_size: str | None = Header(default=None),
        x_flexdisplay_psram_size: str | None = Header(default=None),
        x_flexdisplay_volume: str | None = Header(default=None),
        x_flexdisplay_muted: str | None = Header(default=None),
        x_flexdisplay_brightness: str | None = Header(default=None),
        x_flexdisplay_frontlight_on: str | None = Header(default=None),
        x_flexdisplay_frontlight_brightness: str | None = Header(default=None),
        x_flexdisplay_frontlight_warmth: str | None = Header(default=None),
        x_flexdisplay_frontlight_home_hold: str | None = Header(default=None),
        x_flexdisplay_frontlight_timeout: str | None = Header(default=None),
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
        x_flexdisplay_sd_writable: str | None = Header(default=None),
        x_flexdisplay_sd_diagnostic: str | None = Header(default=None),
        x_flexdisplay_wake_reason: str | None = Header(default=None),
        x_flexdisplay_reset_reason: str | None = Header(default=None),
        x_flexdisplay_boot_id: str | None = Header(default=None),
        x_flexdisplay_button_events: str | None = Header(default=None),
        x_flexdisplay_image_sha256: str | None = Header(default=None),
        x_flexdisplay_image_cached: str | None = Header(default=None),
        x_flexdisplay_last_image_error: str | None = Header(default=None),
        x_flexdisplay_last_fetch_error: str | None = Header(default=None),
        x_flexdisplay_capabilities: str | None = Header(default=None),
        x_flexdisplay_camera_available: str | None = Header(default=None),
        x_flexdisplay_microphone_available: str | None = Header(default=None),
        x_flexdisplay_audio_available: str | None = Header(default=None),
        x_flexdisplay_touch_available: str | None = Header(default=None),
        x_flexdisplay_always_on: str | None = Header(default=None),
        x_flexdisplay_device_class: str | None = Header(default=None),
        x_flexdisplay_content_version: str | None = Header(default=None),
        x_flexdisplay_content_status: str | None = Header(default=None),
        x_flexdisplay_content_error: str | None = Header(default=None),
        x_flexdisplay_policy_revision: str | None = Header(default=None),
        x_flexdisplay_receiver_token: str | None = Header(default=None),
        x_flexdisplay_quick_action: str | None = Header(default=None),
        x_flexdisplay_opendisplay_transport_policy: str | None = Header(default=None),
        x_flexdisplay_opendisplay_last_transport: str | None = Header(default=None),
        x_flexdisplay_opendisplay_fallback: str | None = Header(default=None),
        x_flexdisplay_opendisplay_min_free_heap: str | None = Header(default=None),
        x_flexdisplay_opendisplay_min_largest_block: str | None = Header(default=None),
        x_flexdisplay_opendisplay_lan_memory_blocked: str | None = Header(default=None),
    ):
        device_id = _device_id(x_flexdisplay_id)
        width = _integer(x_flexdisplay_width, 480, 240, 1200)
        height = _integer(x_flexdisplay_height, 800, 240, 1600)
        existing_record = store.get(device_id) or {}
        if x_flexdisplay_model:
            model = x_flexdisplay_model
        elif existing_record.get("model") and existing_record.get("model_reported") is True:
            # A transient omission must never replace an explicitly observed
            # family with a legacy device-ID inference.
            model = str(existing_record["model"])
        elif device_id.upper().startswith("X4-"):
            model = "X4"
        elif device_id.upper().startswith("X3-"):
            model = "X3"
        elif device_id.upper().startswith("N4-"):
            model = "N4"
        else:
            # Unknown identities may render through the shared protocol, but
            # must not inherit an X3 firmware provider from a historical UI
            # default. The device can advertise an explicit model next check-in.
            model = "UNKNOWN"
        # Preserve an explicitly reported family across transient model-header
        # omissions, but never preserve the evidence that admits X4 Pro
        # management. Every check-in must re-report that evidence in full.
        requires_fresh_x4_pro_evidence = (
            _device_capabilities({"model": model}).model_key == "x4_pro"
        )
        device_firmware = _device_firmware(settings, model)
        capabilities = (
            _capabilities(x_flexdisplay_capabilities)
            if x_flexdisplay_capabilities is not None
            else set()
            if requires_fresh_x4_pro_evidence
            else {
                str(value)
                for value in (existing_record.get("transfer_capabilities") or [])
            }
        )

        def reported_text(raw: str | None, key: str) -> str:
            return (
                _header_value(raw)
                if raw is not None
                else ""
                if requires_fresh_x4_pro_evidence
                else str(existing_record.get(key) or "")
            )

        def reported_size(raw: str | None, key: str, maximum: int) -> int | None:
            if raw is not None:
                return _optional_integer(raw, 0, maximum)
            if requires_fresh_x4_pro_evidence:
                return None
            value = existing_record.get(key)
            try:
                return int(value) if value is not None else None
            except (TypeError, ValueError):
                return None

        def capability_flag(raw: str | None, *names: str) -> bool:
            parsed = _boolean(raw)
            if parsed is not None:
                return parsed
            return any(name in capabilities for name in names)

        image_cached = bool(_boolean(x_flexdisplay_image_cached))
        last_image_error = (
            _header_value(x_flexdisplay_last_image_error).strip()
            if x_flexdisplay_last_image_error is not None
            else None
        )
        last_fetch_error = (
            _header_value(x_flexdisplay_last_fetch_error).strip()
            if x_flexdisplay_last_fetch_error is not None
            else None
        )
        one_bit_bytes = ((width + 7) // 8) * height
        model_source_reported = bool(x_flexdisplay_model)
        model_source_inferred = bool(
            not model_source_reported
            and existing_record.get("model")
            and model != "UNKNOWN"
            and existing_record.get("last_seen") is None
        )
        telemetry = {
            "model": model,
            "model_reported": (
                True
                if model_source_reported
                or (
                    not model_source_reported
                    and existing_record.get("model_reported") is True
                )
                else None
                if model_source_inferred
                else False
            ),
            "width": width,
            "height": height,
            "firmware": x_flexdisplay_firmware or "0.4.0",
            "reported_firmware_artifact": reported_text(
                x_flexdisplay_firmware_artifact,
                "reported_firmware_artifact",
            ),
            "board_id": reported_text(x_flexdisplay_board_id, "board_id"),
            "hardware_revision": reported_text(
                x_flexdisplay_hardware_revision, "hardware_revision"
            ),
            "mcu_family": reported_text(x_flexdisplay_mcu_family, "mcu_family"),
            "flash_size_bytes": reported_size(
                x_flexdisplay_flash_size,
                "flash_size_bytes",
                128 * 1024 * 1024,
            ),
            "psram_size_bytes": reported_size(
                x_flexdisplay_psram_size,
                "psram_size_bytes",
                64 * 1024 * 1024,
            ),
            "voice_volume": _optional_integer(x_flexdisplay_volume, 0, 100),
            "voice_muted": _boolean(x_flexdisplay_muted),
            "screen_brightness": _optional_integer(x_flexdisplay_brightness, 0, 100),
            "frontlight_on": _boolean(x_flexdisplay_frontlight_on),
            "frontlight_brightness": _optional_integer(
                x_flexdisplay_frontlight_brightness, 0, 100
            ),
            "frontlight_warmth": _optional_integer(
                x_flexdisplay_frontlight_warmth, 0, 100
            ),
            "frontlight_home_hold": _boolean(x_flexdisplay_frontlight_home_hold),
            "frontlight_timeout_seconds": _optional_integer(
                x_flexdisplay_frontlight_timeout, 15, 3600
            ),
            "battery_percent": _number(x_flexdisplay_battery_percent),
            "battery_voltage": _number(x_flexdisplay_battery_voltage),
            "rssi": _number(x_flexdisplay_rssi),
            "mode": x_flexdisplay_mode or "home_assistant",
            "usb_connected": _boolean(x_flexdisplay_usb_connected),
            "uptime_seconds": _optional_integer(
                x_flexdisplay_uptime_seconds, 0, 31_536_000
            ),
            "free_heap": _optional_integer(x_flexdisplay_free_heap, 0, 1_000_000),
            "min_free_heap": _optional_integer(
                x_flexdisplay_min_free_heap, 0, 1_000_000
            ),
            "sd_ready": _boolean(x_flexdisplay_sd_ready),
            "sd_writable": _boolean(x_flexdisplay_sd_writable),
            "sd_diagnostic": _header_value(x_flexdisplay_sd_diagnostic) or None,
            "wake_reason": x_flexdisplay_wake_reason or None,
            "reset_reason": x_flexdisplay_reset_reason or None,
            "boot_id": x_flexdisplay_boot_id or None,
            "transfer_capabilities": sorted(capabilities),
            "display_shape": "round" if "round-display" in capabilities else "rectangular",
            "touch_available": capability_flag(x_flexdisplay_touch_available, "touch"),
            "color_available": "color" in capabilities,
            "camera_available": capability_flag(
                x_flexdisplay_camera_available, "camera", "camera-snapshot"
            ),
            "microphone_available": capability_flag(
                x_flexdisplay_microphone_available, "microphone", "assist"
            ),
            "audio_available": capability_flag(x_flexdisplay_audio_available, "audio"),
            "always_on_available": capability_flag(
                x_flexdisplay_always_on, "always-on", "always-on-color"
            ),
            "device_class": _header_value(x_flexdisplay_device_class),
            "screen_resolution": f"{width}x{height}",
            "client_platform": "android" if "android" in capabilities else "embedded",
            "receiver_token_sha256": (
                hashlib.sha256(x_flexdisplay_receiver_token.encode("utf-8")).hexdigest()
                if x_flexdisplay_receiver_token
                else None
            ),
            "image_cached": image_cached,
            "reported_policy_revision": _optional_integer(
                x_flexdisplay_policy_revision, 0, 2_147_483_647
            ),
            "reported_open_display_transport_policy": (
                str(x_flexdisplay_opendisplay_transport_policy or "auto").lower()
            ),
            "open_display_last_transport": (
                str(x_flexdisplay_opendisplay_last_transport or "none").lower()
            ),
            "open_display_fallback": _header_value(
                x_flexdisplay_opendisplay_fallback
            ),
            "open_display_min_free_heap": _optional_integer(
                x_flexdisplay_opendisplay_min_free_heap, 0, 1_000_000
            ),
            "open_display_min_largest_block": _optional_integer(
                x_flexdisplay_opendisplay_min_largest_block, 0, 1_000_000
            ),
            "open_display_lan_memory_blocked": _boolean(
                x_flexdisplay_opendisplay_lan_memory_blocked
            ),
        }
        telemetry.update(_display_runtime(telemetry))
        if last_image_error is not None:
            telemetry["image_conversion_error"] = bool(last_image_error)
            if last_image_error:
                telemetry["last_image_error"] = last_image_error
                telemetry["last_image_error_at"] = datetime.now(UTC).isoformat(
                    timespec="seconds"
                )
        elif image_cached:
            # The diagnostic is sent once.  A later cached-image check-in
            # proves a subsequent delivery converted successfully, so clear
            # only the active fault while retaining its history and timestamp.
            telemetry["image_conversion_error"] = False
        if last_fetch_error is not None:
            telemetry["dashboard_fetch_error"] = bool(last_fetch_error)
            if last_fetch_error:
                telemetry["last_fetch_error"] = last_fetch_error
                telemetry["last_fetch_error_at"] = datetime.now(UTC).isoformat(
                    timespec="seconds"
                )
        elif image_cached:
            telemetry["dashboard_fetch_error"] = False
        record = store.touch(
            device_id,
            telemetry,
            clear_fields=(
                {
                    "board_id",
                    "hardware_revision",
                    "mcu_family",
                    "flash_size_bytes",
                    "psram_size_bytes",
                    "reported_firmware_artifact",
                    "transfer_capabilities",
                }
                if requires_fresh_x4_pro_evidence
                else None
            ),
        )
        content_assignment = content_packs.observe(
            device_id,
            x_flexdisplay_content_version or "",
            x_flexdisplay_content_status or "",
            x_flexdisplay_content_error or "",
        )
        if content_assignment:
            record = store.touch(
                device_id,
                {
                    "content_pack_desired": content_assignment.get("desired_version"),
                    "content_pack_version": content_assignment.get("installed_version"),
                    "content_pack_status": content_assignment.get("status"),
                    "content_pack_error": content_assignment.get("error"),
                },
            )
        descriptor = _device_capabilities(record)
        allowed_actions = set(descriptor.management.actions)
        stale_commands = sorted(
            {
                str(command)
                for command in (
                    list(record.get("pending_commands") or [])
                    + list(record.get("dispatched_commands") or [])
                )
                if (
                    str(command) != "install"
                    and str(command) not in allowed_actions
                    and not (
                        descriptor.management.supports_page_selection
                        and re.fullmatch(r"page-[1-9][0-9]*", str(command))
                    )
                )
            }
        )
        for stale_command in stale_commands:
            record = (
                store.remove_command(
                    device_id,
                    stale_command,
                    reason="capability-reconciled",
                )
                or record
            )
        configured = _capability_normalized_config(
            settings.device(device_id, width, height, model),
            descriptor,
        )
        default_assignment = {
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
            "assigned_rendering_profile": configured.rendering_profile,
            "assigned_open_display_transport_policy": (
                configured.open_display_transport_policy
            ),
        }
        filtered_assignment, _ = _filter_provisioning_assignment(
            default_assignment, descriptor
        )
        incompatible_fields = {
            key
            for key in default_assignment
            if key not in filtered_assignment and key in record
        }
        incompatible_mode = str(record.get("assigned_mode") or "")
        if (
            incompatible_mode
            and incompatible_mode not in descriptor.management.modes
        ):
            record = (
                store.remove_provisioning_fields(
                    device_id,
                    {"assigned_mode"},
                    reason="unsupported-mode",
                )
                or record
            )
        record = (
            store.remove_provisioning_fields(
                device_id,
                incompatible_fields,
            )
            or record
        )
        provisioning_enabled = bool(
            settings.provisioning.enabled
            and descriptor.management.supports_provisioning
        )
        if provisioning_enabled:
            record = store.ensure_provisioning(
                device_id,
                filtered_assignment,
            )
        descriptor = _device_capabilities(record)
        firmware_admitted = bool(
            descriptor.firmware.manageable
            and not _firmware_artifact_blockers(record, device_firmware)
        )
        install_active = bool(
            "install" in (record.get("pending_commands") or [])
            or "install" in (record.get("dispatched_commands") or [])
        )
        if install_active:
            expected_firmware = _device_firmware(settings, record)
            queued_provider = str(record.get("firmware_update_provider") or "")
            if not queued_provider:
                queued_role = str(record.get("firmware_update_role") or "")
                queued_provider = (
                    "note4"
                    if queued_role == "device"
                    else "xteink"
                    if queued_role in {"canary", "fleet"}
                    else ""
                )
            queued_target = str(record.get("firmware_update_target") or "")
            queued_artifact_family = str(
                record.get("firmware_update_artifact_family") or ""
            )
            if not queued_artifact_family:
                queued_artifact_family = (
                    "x_series"
                    if queued_provider == "xteink"
                    else queued_provider
                )
            install_compatible = bool(
                descriptor.firmware.manageable
                and queued_provider == descriptor.firmware.provider
                and queued_artifact_family == descriptor.firmware.artifact_family
                and queued_target
                and queued_target == expected_firmware.version
                and not _firmware_artifact_blockers(record, expected_firmware)
            )
        else:
            install_compatible = True
        if not install_compatible:
            # Cancel legacy or stale commands before they can cross firmware
            # providers after a corrected model check-in.
            record = (
                store.remove_command(
                    device_id,
                    "install",
                    reason="firmware-channel-mismatch",
                )
                or record
            )
        button_events = _button_events(
            x_flexdisplay_button_events,
            x_flexdisplay_mode or BUTTON_ACTION_MODE,
        )
        allowed_input_events = set(descriptor.inputs.event_types)
        button_events = [
            event for event in button_events if event.get("button") in allowed_input_events
        ]
        new_button_events = _new_button_events(record, button_events)
        record = store.record_button_events(device_id, button_events) or record
        physical_navigation, record = _dispatch_button_actions(
            device_id,
            record,
            (
                new_button_events
                if descriptor.management.supports_button_actions
                else []
            ),
            store,
            ha,
        )
        command_acknowledged = store.acknowledge(
            device_id,
            x_flexdisplay_command_result or "",
            x_flexdisplay_command_id or "",
        )
        record = (
            store.reconcile_running_firmware(
                device_id,
                device_firmware.version,
                canary_required=device_firmware.canary_required,
                require_usb_for_canary=device_firmware.require_usb_for_canary,
            )
            or record
        )
        if command_acknowledged or x_flexdisplay_command_result:
            advance_firmware_rollout()
        profile: DeviceConfig = _effective_device(configured, record)
        commands = store.consume_commands(device_id)
        if commands:
            record = store.get(device_id) or record
        elif not command_acknowledged:
            # Durable commands use at-least-once delivery. If the device resets,
            # loses the response, or fails before persisting a result, resend the
            # same dispatched command and command ID until it acknowledges.
            record = store.get(device_id) or record
            commands = list(record.get("dispatched_commands") or [])
        command_id = str(record.get("dispatched_command_id") or "") if commands else ""
        override_id = str(record.get("screen_override_id") or "")
        if override_id:
            try:
                override_path, override_item = screen_history.get(
                    device_id, override_id
                )
                image = override_path.read_bytes()
            except (OSError, ScreenHistoryError) as err:
                store.clear_screen_override(device_id, override_id)
                store.record_management_result(
                    device_id,
                    "resend-screen",
                    False,
                    str(err),
                )
            else:
                store.clear_screen_override(device_id, override_id)
                source_media_type = str(override_item.get("media_type") or "image/png")
                publish_screen_preview(device_id, image, source_media_type)
                image, delivery_media_type = _device_screen_payload(
                    image,
                    source_media_type,
                    model,
                )
                digest = hashlib.sha256(image).hexdigest()
                image_unchanged = bool(
                    x_flexdisplay_image_sha256 and x_flexdisplay_image_sha256 == digest
                )
                sleep_plan = _sleep_plan(
                    profile,
                    _number(x_flexdisplay_battery_percent),
                    bool(_boolean(x_flexdisplay_usb_connected)),
                    image_unchanged,
                )
                record = store.touch(
                    device_id,
                    {
                        **sleep_plan,
                        "last_image_sha256": digest,
                        "last_screen_refresh_at": datetime.now(UTC).isoformat(
                            timespec="seconds"
                        ),
                        "last_screen_history_id": override_id,
                        "screen_history_count": len(screen_history.list(device_id)),
                    },
                )
                publish_current(device_id)
                response.headers["ETag"] = f'"{digest}"'
                response.headers["X-FlexDisplay-Image-SHA256"] = digest
                response.headers["X-FlexDisplay-Image-Unchanged"] = (
                    "true" if image_unchanged else "false"
                )
                response.headers["X-FlexDisplay-Screen-Restored"] = override_id
                response.headers["X-FlexDisplay-Sleep-Action"] = sleep_plan[
                    "sleep_action"
                ]
                response.headers["X-FlexDisplay-Sleep-Seconds"] = str(
                    sleep_plan["sleep_seconds"]
                )
                response.headers["X-FlexDisplay-Sleep-Reason"] = sleep_plan[
                    "sleep_reason"
                ]
                response.headers["X-FlexDisplay-Manual-Wake-Grace"] = str(
                    profile.manual_wake_grace_seconds
                )
                response.headers["X-FlexDisplay-Commands"] = ",".join(commands)
                if command_id:
                    response.headers["X-FlexDisplay-Command-ID"] = command_id
                if provisioning_enabled:
                    response.headers["X-FlexDisplay-Provisioned"] = "true"
                    response.headers["X-FlexDisplay-Device-Name"] = _header_value(
                        profile.name
                    )
                    response.headers["X-FlexDisplay-Area"] = _header_value(profile.area)
                    response.headers["X-FlexDisplay-Profile"] = _header_value(
                        profile.profile
                    )
                    response.headers["X-FlexDisplay-Assigned-Mode"] = _header_value(
                        profile.mode
                    )
                    response.headers["X-FlexDisplay-Auto-Start"] = (
                        "true" if profile.auto_start else "false"
                    )
                    response.headers["X-FlexDisplay-Live-Mode"] = (
                        "true" if profile.live_mode else "false"
                    )
                    response.headers["X-FlexDisplay-Rendering-Profile"] = (
                        profile.rendering_profile
                    )
                    response.headers[
                        "X-FlexDisplay-OpenDisplay-Transport-Policy"
                    ] = profile.open_display_transport_policy
                    response.headers["X-FlexDisplay-Policy-Revision"] = str(
                        int(record.get("assigned_policy_revision") or 0)
                    )
                if _is_note4(model) or _is_android_display(model):
                    response.headers["X-FlexDisplay-Desired-Volume"] = str(
                        int(record.get("desired_voice_volume", record.get("voice_volume") or 45))
                    )
                    response.headers["X-FlexDisplay-Desired-Muted"] = (
                        "true" if record.get("desired_voice_muted", record.get("voice_muted") is True) else "false"
                    )
                if _is_android_display(model):
                    response.headers["X-FlexDisplay-Desired-Brightness"] = str(
                        int(record.get("desired_screen_brightness", record.get("screen_brightness") or 100))
                    )
                apply_frontlight_headers(response, record)
                if firmware_admitted and device_firmware.version:
                    response.headers["X-FlexDisplay-Latest-Firmware"] = (
                        device_firmware.version
                    )
                if firmware_admitted and "install" in commands:
                    response.headers["X-FlexDisplay-Firmware-URL"] = (
                        firmware_delivery_url(request, record)
                    )
                    response.headers["X-FlexDisplay-Firmware-SHA256"] = (
                        device_firmware.sha256
                    )
                    response.headers["X-FlexDisplay-Firmware-Size"] = str(
                        device_firmware.size
                    )
                    response.headers["X-FlexDisplay-Firmware-Min-Battery"] = str(
                        device_firmware.minimum_battery_percent
                    )
                apply_loading_screen_headers(
                    response,
                    request,
                    device_id,
                    profile,
                    width,
                    height,
                )
                apply_content_pack_headers(response, request, device_id)
                return deliver_screen(
                    device_id,
                    image,
                    delivery_media_type,
                    response,
                    image_unchanged=image_unchanged,
                    image_cached=image_cached,
                    capabilities=capabilities,
                    uncompressed_bytes=one_bit_bytes,
                )
        if profile.mode == "photo_frame":
            direction = "auto"
            if "next" in commands:
                direction = "next"
            elif "previous" in commands:
                direction = "previous"
            elif "refresh" in commands or "full-refresh" in commands:
                direction = "current"
            for event in new_button_events:
                if (
                    event.get("mode") != "photo_frame"
                    or event.get("gesture") != "short"
                ):
                    continue
                if event.get("button") in {"right", "down"}:
                    direction = "next"
                elif event.get("button") in {"left", "up"}:
                    direction = "previous"
            photo_format = (
                "PNG"
                if "png-photo" in capabilities and not _is_x3_model(model)
                else "BMP"
            )
            photo_media_type = "image/png" if photo_format == "PNG" else "image/bmp"
            try:
                image, photo_headers = photo_frames.next_for_device(
                    device_id,
                    width=width,
                    height=height,
                    direction=direction,
                    output_format=photo_format,
                )
            except PhotoFrameValidationError as err:
                raise HTTPException(status_code=409, detail=str(err)) from err
            image, photo_media_type = _device_screen_payload(
                image,
                photo_media_type,
                model,
            )
            digest = hashlib.sha256(image).hexdigest()
            image_unchanged = bool(
                x_flexdisplay_image_sha256 and x_flexdisplay_image_sha256 == digest
            )
            interval = _integer(
                photo_headers.get("X-FlexDisplay-Refresh-Interval"),
                profile.refresh_interval_seconds,
                60,
                86400,
            )
            profile = replace(
                profile,
                refresh_interval_seconds=interval,
                active_start=photo_headers["X-FlexDisplay-Photo-Start"],
                active_end=photo_headers["X-FlexDisplay-Photo-End"],
                timezone=photo_headers["X-FlexDisplay-Photo-Timezone"],
            )
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
                        datetime.now(UTC)
                        + timedelta(seconds=profile.manual_sleep_seconds)
                    ).isoformat(timespec="seconds"),
                }
            record = store.touch(
                device_id,
                {
                    **sleep_plan,
                    "photo_album": photo_headers["X-FlexDisplay-Photo-Album"],
                    "photo_id": photo_headers["X-FlexDisplay-Photo-ID"],
                    "photo_filename": photo_headers["X-FlexDisplay-Photo-Filename"],
                    "photo_index": int(photo_headers["X-FlexDisplay-Photo-Index"]),
                    "photo_count": int(photo_headers["X-FlexDisplay-Photo-Count"]),
                    "last_image_sha256": digest,
                    "last_screen_refresh_at": datetime.now(UTC).isoformat(
                        timespec="seconds"
                    ),
                    "ha_error": False,
                },
            )
            if settings.screen_history.enabled:
                captured = screen_history.record(
                    device_id,
                    image,
                    media_type=photo_media_type,
                    metadata={
                        "mode": "photo_frame",
                        "profile": profile.profile,
                        "title": photo_headers["X-FlexDisplay-Photo-Filename"],
                        "photo_album": photo_headers["X-FlexDisplay-Photo-Album"],
                    },
                )
                record = store.touch(
                    device_id,
                    {
                        "last_screen_history_id": captured["id"],
                        "screen_history_count": len(screen_history.list(device_id)),
                    },
                )
            publish_screen_preview(device_id, image, photo_media_type)
            publish_current(device_id)
            for name, value in photo_headers.items():
                response.headers[name] = _header_value(value)
            response.headers["ETag"] = f'"{digest}"'
            response.headers["X-FlexDisplay-Image-SHA256"] = digest
            response.headers["X-FlexDisplay-Image-Unchanged"] = (
                "true" if image_unchanged else "false"
            )
            response.headers["X-FlexDisplay-Sleep-Action"] = sleep_plan["sleep_action"]
            response.headers["X-FlexDisplay-Sleep-Seconds"] = str(
                sleep_plan["sleep_seconds"]
            )
            response.headers["X-FlexDisplay-Sleep-Reason"] = sleep_plan["sleep_reason"]
            response.headers["X-FlexDisplay-Manual-Wake-Grace"] = str(
                profile.manual_wake_grace_seconds
            )
            if provisioning_enabled:
                response.headers["X-FlexDisplay-Provisioned"] = "true"
                response.headers["X-FlexDisplay-Device-Name"] = _header_value(
                    profile.name
                )
                response.headers["X-FlexDisplay-Area"] = _header_value(profile.area)
                response.headers["X-FlexDisplay-Profile"] = _header_value(
                    profile.profile
                )
                response.headers["X-FlexDisplay-Assigned-Mode"] = "photo_frame"
                response.headers["X-FlexDisplay-Auto-Start"] = (
                    "true" if profile.auto_start else "false"
                )
                response.headers["X-FlexDisplay-Live-Mode"] = (
                    "true" if profile.live_mode else "false"
                )
                response.headers["X-FlexDisplay-Rendering-Profile"] = (
                    profile.rendering_profile
                )
                response.headers[
                    "X-FlexDisplay-OpenDisplay-Transport-Policy"
                ] = profile.open_display_transport_policy
                response.headers["X-FlexDisplay-Policy-Revision"] = str(
                    int(record.get("assigned_policy_revision") or 0)
                )
            response.headers["X-FlexDisplay-Commands"] = ",".join(commands)
            if command_id:
                response.headers["X-FlexDisplay-Command-ID"] = command_id
            if x_flexdisplay_command_result:
                response.headers["X-FlexDisplay-Command-Acknowledged"] = (
                    "true" if command_acknowledged else "false"
                )
            if _is_note4(model) or _is_android_display(model):
                response.headers["X-FlexDisplay-Desired-Volume"] = str(
                    int(record.get("desired_voice_volume", record.get("voice_volume") or 45))
                )
                response.headers["X-FlexDisplay-Desired-Muted"] = (
                    "true" if record.get("desired_voice_muted", record.get("voice_muted") is True) else "false"
                )
            if _is_android_display(model):
                response.headers["X-FlexDisplay-Desired-Brightness"] = str(
                    int(record.get("desired_screen_brightness", record.get("screen_brightness") or 100))
                )
            apply_frontlight_headers(response, record)
            if firmware_admitted and device_firmware.version:
                response.headers["X-FlexDisplay-Latest-Firmware"] = (
                    device_firmware.version
                )
            if firmware_admitted and "install" in commands:
                response.headers["X-FlexDisplay-Firmware-URL"] = firmware_delivery_url(
                    request, record
                )
                response.headers["X-FlexDisplay-Firmware-SHA256"] = (
                    device_firmware.sha256
                )
                response.headers["X-FlexDisplay-Firmware-Size"] = str(
                    device_firmware.size
                )
                response.headers["X-FlexDisplay-Firmware-Min-Battery"] = str(
                    device_firmware.minimum_battery_percent
                )
            apply_loading_screen_headers(
                response,
                request,
                device_id,
                profile,
                width,
                height,
            )
            apply_content_pack_headers(response, request, device_id)
            return deliver_screen(
                device_id,
                image,
                photo_media_type,
                response,
                image_unchanged=image_unchanged,
                image_cached=image_cached,
                capabilities=capabilities,
                uncompressed_bytes=one_bit_bytes,
            )
        dashboard_profile = dashboards.resolve(profile.profile)
        profile = replace(
            profile,
            profile=dashboard_profile.name,
            entities=(
                DashboardProfileStore.entity_configs(dashboard_profile)
                if dashboard_profile.pages
                else profile.entities
            ),
        )
        entity_states, ha_error = fetch_dashboard_entities(profile.entities)
        configured_pages = build_dashboard_pages(
            entity_states,
            record,
            dashboard_profile.pages if dashboard_profile else (),
        )
        pages, page_selection = select_active_pages(
            configured_pages,
            entity_states,
            record,
            profile.timezone,
        )
        mixed_pages = content_channels.pages(
            device_id, [candidate.title for candidate in pages]
        )
        playlist_count = len(mixed_pages) if mixed_pages else len(pages)
        page_index = int(record.get("dashboard_page_index") or 0) % playlist_count
        selection_changed = (
            not mixed_pages
            and bool(record.get("dashboard_selection"))
            and (record.get("dashboard_selection") != page_selection)
        )
        quick_action = str(x_flexdisplay_quick_action or "").strip().lower()
        if quick_action not in {
            "next",
            "previous",
            "overview",
            "refresh",
            "category-dashboard",
            "category-message",
            "category-news",
            "category-quote",
        }:
            quick_action = ""
        if page_selection == "alert" or selection_changed:
            page_index = 0
        elif "next" in commands or quick_action == "next":
            page_index = (page_index + 1) % playlist_count
        elif "previous" in commands or quick_action == "previous":
            page_index = (page_index - 1) % playlist_count
        elif "overview" in commands or quick_action == "overview":
            page_index = 0
        elif quick_action.startswith("category-") and mixed_pages:
            requested_kind = quick_action.removeprefix("category-")
            page_index = next(
                (
                    index
                    for index, candidate in enumerate(mixed_pages)
                    if candidate.kind == requested_kind
                ),
                page_index,
            )
        else:
            requested_page = next(
                (command for command in commands if command.startswith("page-")),
                None,
            )
            if requested_page:
                page_index = (
                    int(requested_page.removeprefix("page-")) - 1
                ) % playlist_count
            elif physical_navigation:
                for navigation in physical_navigation:
                    if navigation == "next":
                        page_index = (page_index + 1) % playlist_count
                    elif navigation == "previous":
                        page_index = (page_index - 1) % playlist_count
                    elif navigation == "overview":
                        page_index = 0
            elif dashboard_profile and _auto_rotate_due(
                record, dashboard_profile.auto_rotate_seconds
            ):
                page_index = (page_index + 1) % playlist_count
        content_page = mixed_pages[page_index] if mixed_pages else None
        page = (
            pages[content_page.dashboard_index]
            if content_page is not None and content_page.kind == "dashboard"
            else pages[page_index]
            if content_page is None
            else None
        )
        page_title = page.title if page is not None else content_page.title
        page_kind = "dashboard" if page is not None else content_page.kind
        playlist_titles = (
            [candidate.title for candidate in mixed_pages]
            if mixed_pages
            else [candidate.title for candidate in pages]
        )
        record = (
            store.set_dashboard_page(
                device_id,
                page_index,
                playlist_count,
                page_title,
                playlist_titles,
                profile.profile,
                "content_channel" if mixed_pages else page_selection,
            )
            or record
        )
        if _is_android_display(model) and page is not None:
            public_interactions, private_interactions = build_page_interactions(
                page.entities,
                width,
                height,
            )
            rook.set_interactions(
                device_id,
                page.title,
                public_interactions,
                private_interactions,
            )
        elif _is_android_display(model):
            rook.set_interactions(device_id, page_title, [], {})
        if page is not None:
            image = renderer.render(
                title=page.title,
                device={**record, "name": profile.name},
                width=width,
                height=height,
                entities=page.entities,
                page_index=page_index,
                page_count=playlist_count,
                ha_error=ha_error,
                layout=page.layout,
                button_actions=mappings_payload(record.get("button_action_mappings")),
                show_button_indicators=bool(record.get("button_action_indicators")),
            )
        else:
            image = render_content_page(
                content_page,
                device_name=profile.name,
                width=width,
                height=height,
                page_index=page_index,
                page_count=playlist_count,
            )
        preview_image = image
        image, delivery_media_type = _device_screen_payload(
            image,
            "image/png",
            model,
        )
        digest = hashlib.sha256(image).hexdigest()
        image_unchanged = bool(
            x_flexdisplay_image_sha256 and x_flexdisplay_image_sha256 == digest
        )
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
        record = store.touch(
            device_id,
            {
                **sleep_plan,
                "last_image_sha256": digest,
                "last_screen_refresh_at": datetime.now(UTC).isoformat(
                    timespec="seconds"
                ),
                "ha_error": bool(ha_error),
                "content_page_type": page_kind,
                "content_channel": (
                    str(content_channels.payload()["assignments"].get(device_id) or "")
                    if mixed_pages
                    else ""
                ),
            },
        )
        if settings.screen_history.enabled:
            captured = screen_history.record(
                device_id,
                preview_image,
                media_type="image/png",
                metadata={
                    "mode": profile.mode,
                    "profile": profile.profile,
                    "title": page_title,
                    "page_index": page_index,
                    "page_count": playlist_count,
                    "page_selection": "content_channel"
                    if mixed_pages
                    else page_selection,
                    "content_type": page_kind,
                },
            )
            record = store.touch(
                device_id,
                {
                    "last_screen_history_id": captured["id"],
                    "screen_history_count": len(screen_history.list(device_id)),
                },
            )
        publish_screen_preview(device_id, preview_image, "image/png")
        state = {
            **record,
            "name": profile.name,
            "last_image_sha256": digest,
            "refresh_interval_seconds": profile.refresh_interval_seconds,
        }
        del state
        publish_current(device_id)
        response.headers["ETag"] = f'"{digest}"'
        response.headers["X-FlexDisplay-Refresh-Interval"] = str(
            profile.refresh_interval_seconds
        )
        response.headers["X-FlexDisplay-Image-SHA256"] = digest
        response.headers["X-FlexDisplay-Image-Unchanged"] = (
            "true" if image_unchanged else "false"
        )
        response.headers["X-FlexDisplay-Sleep-Action"] = sleep_plan["sleep_action"]
        response.headers["X-FlexDisplay-Sleep-Seconds"] = str(
            sleep_plan["sleep_seconds"]
        )
        response.headers["X-FlexDisplay-Sleep-Reason"] = sleep_plan["sleep_reason"]
        response.headers["X-FlexDisplay-Manual-Wake-Grace"] = str(
            profile.manual_wake_grace_seconds
        )
        if provisioning_enabled:
            response.headers["X-FlexDisplay-Provisioned"] = "true"
            response.headers["X-FlexDisplay-Device-Name"] = _header_value(profile.name)
            response.headers["X-FlexDisplay-Area"] = _header_value(profile.area)
            response.headers["X-FlexDisplay-Profile"] = _header_value(profile.profile)
            response.headers["X-FlexDisplay-Assigned-Mode"] = _header_value(
                profile.mode
            )
            response.headers["X-FlexDisplay-Auto-Start"] = (
                "true" if profile.auto_start else "false"
            )
            response.headers["X-FlexDisplay-Live-Mode"] = (
                "true" if profile.live_mode else "false"
            )
            response.headers["X-FlexDisplay-Rendering-Profile"] = (
                profile.rendering_profile
            )
            response.headers[
                "X-FlexDisplay-OpenDisplay-Transport-Policy"
            ] = profile.open_display_transport_policy
            response.headers["X-FlexDisplay-Policy-Revision"] = str(
                int(record.get("assigned_policy_revision") or 0)
            )
        response.headers["X-FlexDisplay-Commands"] = ",".join(commands)
        if command_id:
            response.headers["X-FlexDisplay-Command-ID"] = command_id
        if x_flexdisplay_command_result:
            response.headers["X-FlexDisplay-Command-Acknowledged"] = (
                "true" if command_acknowledged else "false"
            )
        response.headers["X-FlexDisplay-Page"] = str(page_index + 1)
        response.headers["X-FlexDisplay-Page-Count"] = str(playlist_count)
        response.headers["X-FlexDisplay-Page-Title"] = page_title
        response.headers["X-FlexDisplay-Page-Selection"] = (
            "content_channel" if mixed_pages else page_selection
        )
        response.headers["X-FlexDisplay-Content-Type"] = page_kind
        if _is_android_display(model):
            response.headers["X-FlexDisplay-Interaction-Revision"] = str(
                rook.interactions(device_id)["revision"]
            )
        if _is_note4(model) or _is_android_display(model):
            response.headers["X-FlexDisplay-Desired-Volume"] = str(
                int(record.get("desired_voice_volume", record.get("voice_volume") or 45))
            )
            response.headers["X-FlexDisplay-Desired-Muted"] = (
                "true" if record.get("desired_voice_muted", record.get("voice_muted") is True) else "false"
            )
        if _is_android_display(model):
            response.headers["X-FlexDisplay-Desired-Brightness"] = str(
                int(record.get("desired_screen_brightness", record.get("screen_brightness") or 100))
            )
        apply_frontlight_headers(response, record)
        if firmware_admitted and device_firmware.version:
            response.headers["X-FlexDisplay-Latest-Firmware"] = (
                device_firmware.version
            )
        if firmware_admitted and "install" in commands:
            response.headers["X-FlexDisplay-Firmware-URL"] = firmware_delivery_url(
                request, record
            )
            response.headers["X-FlexDisplay-Firmware-SHA256"] = device_firmware.sha256
            response.headers["X-FlexDisplay-Firmware-Size"] = str(
                device_firmware.size
            )
            response.headers["X-FlexDisplay-Firmware-Min-Battery"] = str(
                device_firmware.minimum_battery_percent
            )
        apply_loading_screen_headers(
            response,
            request,
            device_id,
            profile,
            width,
            height,
        )
        apply_content_pack_headers(response, request, device_id)
        return deliver_screen(
            device_id,
            image,
            delivery_media_type,
            response,
            image_unchanged=image_unchanged,
            image_cached=image_cached,
            capabilities=capabilities,
            uncompressed_bytes=one_bit_bytes,
        )

    return app


app = create_app()
