from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable
from typing import Any

import paho.mqtt.client as mqtt

from . import __version__
from .config import DeviceConfig, MqttConfig
from .device_capabilities import (
    DeliveryCapabilities,
    DeviceCapabilityDescriptor,
    DisplayCapabilities,
    FirmwareCapabilities,
    FrontlightCapabilities,
    HardwareCapabilities,
    InputCapabilities,
    ManagementCapabilities,
    PowerCapabilities,
    resolve_device_capabilities,
)

LOGGER = logging.getLogger(__name__)


def _device_descriptor(
    profile: DeviceConfig, state: dict[str, Any]
) -> DeviceCapabilityDescriptor:
    reported = state.get("device_capabilities")
    if isinstance(reported, dict):
        try:
            display = reported["display"]
            power = reported["power"]
            delivery = reported["delivery"]
            firmware = reported["firmware"]
            management = reported["management"]
            hardware = reported.get("hardware") or {}
            inputs = reported.get("inputs") or {}
            frontlight = reported.get("frontlight") or {}
            firmware_provider = str(firmware.get("provider") or "none")
            model_key = str(reported.get("model_key") or "unknown")
            return DeviceCapabilityDescriptor(
                family=str(reported.get("family") or "unknown"),
                model_key=model_key,
                label=str(reported.get("label") or "Unknown display"),
                known_model=bool(reported.get("known_model")),
                display=DisplayCapabilities(
                    width=display.get("width"),
                    height=display.get("height"),
                    technology=str(display.get("technology") or "unknown"),
                    color=bool(display.get("color")),
                    shape=str(display.get("shape") or "rectangular"),
                    image_format=str(display.get("image_format") or "png"),
                    touch=(
                        bool(display.get("touch"))
                        if display.get("touch") is not None
                        else None
                    ),
                ),
                hardware=HardwareCapabilities(
                    board_id=str(hardware.get("board_id") or ""),
                    hardware_revision=str(hardware.get("hardware_revision") or ""),
                    mcu_family=str(hardware.get("mcu_family") or ""),
                    flash_size_bytes=hardware.get("flash_size_bytes"),
                    psram_size_bytes=hardware.get("psram_size_bytes"),
                    reported_identity_complete=bool(
                        hardware.get("reported_identity_complete")
                    ),
                    management_profile=str(
                        hardware.get("management_profile") or "read_only"
                    ),
                ),
                inputs=InputCapabilities(
                    touch=(
                        bool(inputs.get("touch"))
                        if inputs.get("touch") is not None
                        else None
                    ),
                    touch_controller=str(inputs.get("touch_controller") or ""),
                    physical_buttons=tuple(inputs.get("physical_buttons") or ()),
                    capacitive_buttons=tuple(
                        inputs.get("capacitive_buttons") or ()
                    ),
                    event_types=tuple(inputs.get("event_types") or ()),
                ),
                frontlight=FrontlightCapabilities(
                    available=(
                        bool(frontlight.get("available"))
                        if frontlight.get("available") is not None
                        else None
                    ),
                    supports_on=bool(frontlight.get("supports_on")),
                    supports_brightness=bool(
                        frontlight.get("supports_brightness")
                    ),
                    supports_warmth=bool(frontlight.get("supports_warmth")),
                    minimum=int(frontlight.get("minimum", 0)),
                    maximum=int(frontlight.get("maximum", 100)),
                ),
                power=PowerCapabilities(**power),
                delivery=DeliveryCapabilities(**delivery),
                firmware=FirmwareCapabilities(
                    provider=firmware_provider,
                    artifact_family=str(
                        firmware.get("artifact_family")
                        or (
                            "x_series"
                            if firmware_provider == "xteink"
                            else firmware_provider
                        )
                    ),
                    manageable=bool(firmware.get("manageable")),
                    supports_xteink_ota=bool(firmware.get("supports_xteink_ota")),
                ),
                management=ManagementCapabilities(
                    **{
                        **management,
                        "actions": tuple(management.get("actions") or ()),
                        "modes": tuple(management.get("modes") or ()),
                        "supports_button_actions": bool(
                            management.get(
                                "supports_button_actions",
                                model_key in {"x3", "x4", "note4"},
                            )
                        ),
                    }
                ),
                reported_capabilities=tuple(
                    reported.get("reported_capabilities") or ()
                ),
            )
        except (KeyError, TypeError, ValueError):
            # An incomplete contract must not grant a trusted identity.
            return resolve_device_capabilities("")

    model = str(state.get("model") or profile.model or "")
    if state.get("model_reported") is False:
        return resolve_device_capabilities("")

    def dimension(name: str) -> int | None:
        try:
            return int(state[name]) if state.get(name) is not None else None
        except (TypeError, ValueError):
            return None

    return resolve_device_capabilities(
        model,
        capabilities=state.get("transfer_capabilities") or (),
        width=dimension("width"),
        height=dimension("height"),
        board_id=str(state.get("board_id") or ""),
        hardware_revision=str(state.get("hardware_revision") or ""),
        mcu_family=str(state.get("mcu_family") or ""),
        flash_size_bytes=dimension("flash_size_bytes"),
        psram_size_bytes=dimension("psram_size_bytes"),
    )


def _is_android_receiver(profile: DeviceConfig, state: dict[str, Any]) -> bool:
    return _device_descriptor(profile, state).family == "android_receiver"


CommandHandler = Callable[[str, str, str], None]


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")


class MqttService:
    """Publish a complete, optional Home Assistant device through MQTT Discovery."""

    def __init__(self, config: MqttConfig, on_command: CommandHandler):
        self.config = config
        self.on_command = on_command
        self.client: mqtt.Client | None = None
        self.connected = False
        self._last_event: dict[str, str] = {}
        self._last_flexhub_message = ""
        self._flexhub_console_state: dict[str, Any] = {
            "last_message": "",
            "last_sender": "",
            "last_channel": 0,
            "last_message_at": "",
            "unread_count": 0,
            "last_send_status": "idle",
            "last_send_error": "",
        }
        self._pending_device_removals: dict[
            str, tuple[DeviceConfig, dict[str, Any]]
        ] = {}

    @property
    def discovery_enabled(self) -> bool:
        return self.config.entity_source in {"mqtt", "both"}

    def start(self) -> None:
        if not self.config.enabled:
            return
        client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2, client_id="flexdisplay-ha-bridge"
        )
        if self.config.username:
            client.username_pw_set(self.config.username, self.config.password)
        client.on_connect = self._on_connect
        client.on_disconnect = self._on_disconnect
        client.on_message = self._on_message
        client.will_set(
            f"{self.config.topic_prefix}/bridge/status", "offline", retain=True
        )
        self.client = client
        try:
            client.connect_async(self.config.host, self.config.port, keepalive=60)
            client.loop_start()
        except OSError as exc:
            LOGGER.warning("MQTT startup failed: %s", exc)

    def stop(self) -> None:
        if not self.client:
            return
        if self.connected:
            self.client.publish(
                f"{self.config.topic_prefix}/bridge/status", "offline", retain=True
            )
            self.client.disconnect()
        self.client.loop_stop()

    def _on_connect(self, client, userdata, flags, reason_code, properties) -> None:
        del userdata, flags, properties
        if reason_code != 0:
            LOGGER.warning("MQTT connection rejected: %s", reason_code)
            return
        self.connected = True
        client.publish(
            f"{self.config.topic_prefix}/bridge/status", "online", retain=True
        )
        client.subscribe(f"{self.config.topic_prefix}/+/command/+")
        LOGGER.info(
            "Connected to MQTT broker %s:%s (entity source: %s)",
            self.config.host,
            self.config.port,
            self.config.entity_source,
        )
        for device_id, (profile, state) in list(self._pending_device_removals.items()):
            self._clear_device(device_id, profile, state)

    def _on_disconnect(
        self, client, userdata, disconnect_flags, reason_code, properties
    ) -> None:
        del client, userdata, disconnect_flags, properties
        self.connected = False
        if reason_code != 0:
            LOGGER.warning("MQTT disconnected: %s", reason_code)

    def _on_message(self, client, userdata, message) -> None:
        del client, userdata
        parts = message.topic.split("/")
        if (
            len(parts) != 4
            or parts[0] != self.config.topic_prefix
            or parts[2] != "command"
        ):
            return
        device_id, command = parts[1], parts[3]
        payload = message.payload.decode("utf-8", errors="replace").strip()
        try:
            if device_id == "flexhub" and command == "clear-meshtastic-unread":
                self._flexhub_console_state["unread_count"] = 0
                self._publish_flexhub_console_state()
            self.on_command(device_id, command, payload)
        except Exception:
            LOGGER.exception("MQTT command %s failed for %s", command, device_id)

    @staticmethod
    def _origin() -> dict[str, str]:
        return {
            "name": "FlexDisplay Bridge",
            "sw_version": __version__,
            "support_url": "https://github.com/clintonmarshall/xteink-flexdisplay-ha",
        }

    def _base(
        self,
        *,
        device: dict[str, Any],
        unique_id: str,
        state_topic: str | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "unique_id": unique_id,
            "device": device,
            "origin": self._origin(),
            "availability_topic": f"{self.config.topic_prefix}/bridge/status",
            "payload_available": "online",
            "payload_not_available": "offline",
        }
        if state_topic:
            payload["state_topic"] = state_topic
        return payload

    def _configs(
        self,
        device_id: str,
        profile: DeviceConfig,
        state: dict[str, Any],
    ) -> list[tuple[str, dict[str, Any]]]:
        descriptor = _device_descriptor(profile, state)
        slug = _slug(device_id)
        state_topic = f"{self.config.topic_prefix}/{device_id}/state"
        command_root = f"{self.config.topic_prefix}/{device_id}/command"
        device = {
            "identifiers": [f"flexdisplay_{device_id}"],
            "name": profile.name,
            "manufacturer": "XTEINK / FlexDisplay",
            "model": state.get("model") or profile.model or "XTEINK",
            "serial_number": device_id,
            "sw_version": state.get("firmware") or "unknown",
        }
        if profile.area:
            device["suggested_area"] = profile.area
        configs: list[tuple[str, dict[str, Any]]] = []

        sensors: dict[str, dict[str, Any]] = {
            "battery": {
                "name": "Battery",
                "device_class": "battery",
                "state_class": "measurement",
                "unit_of_measurement": "%",
                "value_template": "{{ value_json.battery_percent }}",
            },
            "battery_voltage": {
                "name": "Battery voltage",
                "device_class": "voltage",
                "state_class": "measurement",
                "unit_of_measurement": "V",
                "value_template": "{{ value_json.battery_voltage }}",
                "entity_category": "diagnostic",
            },
            "rssi": {
                "name": "Wi-Fi signal",
                "device_class": "signal_strength",
                "state_class": "measurement",
                "unit_of_measurement": "dBm",
                "value_template": "{{ value_json.rssi }}",
                "entity_category": "diagnostic",
            },
            "wifi_average": {
                "name": "Wi-Fi average",
                "device_class": "signal_strength",
                "state_class": "measurement",
                "unit_of_measurement": "dBm",
                "value_template": "{{ value_json.wifi_average_rssi }}",
                "entity_category": "diagnostic",
            },
            "wifi_trend": {
                "name": "Wi-Fi trend",
                "value_template": "{{ value_json.wifi_trend }}",
                "entity_category": "diagnostic",
            },
            "battery_runtime": {
                "name": "Estimated battery runtime",
                "state_class": "measurement",
                "unit_of_measurement": "h",
                "value_template": "{{ value_json.estimated_battery_runtime_hours }}",
                "entity_category": "diagnostic",
            },
            "battery_drain": {
                "name": "Battery drain",
                "state_class": "measurement",
                "unit_of_measurement": "%/d",
                "value_template": "{{ value_json.battery_drain_percent_per_day }}",
                "entity_category": "diagnostic",
            },
            "last_seen": {
                "name": "Last check-in",
                "device_class": "timestamp",
                "value_template": "{{ value_json.last_seen }}",
                "entity_category": "diagnostic",
            },
            "last_screen_refresh": {
                "name": "Last screen refresh",
                "device_class": "timestamp",
                "value_template": "{{ value_json.last_screen_refresh_at }}",
            },
            "next_wake": {
                "name": "Next scheduled wake",
                "device_class": "timestamp",
                "value_template": "{{ value_json.next_wake_at }}",
            },
            "firmware": {
                "name": "Firmware",
                "value_template": "{{ value_json.firmware }}",
                "entity_category": "diagnostic",
            },
            "board_id": {
                "name": "Board ID",
                "value_template": "{{ value_json.board_id }}",
                "entity_category": "diagnostic",
            },
            "hardware_revision": {
                "name": "Hardware revision",
                "value_template": "{{ value_json.hardware_revision }}",
                "entity_category": "diagnostic",
            },
            "mcu_family": {
                "name": "MCU family",
                "value_template": "{{ value_json.mcu_family }}",
                "entity_category": "diagnostic",
            },
            "flash_size": {
                "name": "Flash size",
                "device_class": "data_size",
                "unit_of_measurement": "B",
                "value_template": "{{ value_json.flash_size_bytes }}",
                "entity_category": "diagnostic",
            },
            "frontlight_brightness": {
                "name": "Frontlight brightness",
                "unit_of_measurement": "%",
                "value_template": "{{ value_json.frontlight_brightness }}",
                "entity_category": "diagnostic",
            },
            "frontlight_warmth": {
                "name": "Frontlight warmth",
                "unit_of_measurement": "%",
                "value_template": "{{ value_json.frontlight_warmth }}",
                "entity_category": "diagnostic",
            },
            "mode": {
                "name": "Current mode",
                "value_template": "{{ value_json.mode }}",
            },
            "power_state": {
                "name": "Power state",
                "value_template": "{{ value_json.power_state }}",
            },
            "health": {
                "name": "Health",
                "value_template": "{{ value_json.health_state }}",
            },
            "health_detail": {
                "name": "Health detail",
                "value_template": "{{ value_json.health_detail }}",
                "entity_category": "diagnostic",
            },
            "dashboard_page": {
                "name": "Dashboard page",
                "value_template": "{{ value_json.dashboard_page_title }}",
            },
            "dashboard_position": {
                "name": "Dashboard position",
                "value_template": (
                    "{{ (value_json.dashboard_page_index | int + 1) ~ ' / ' ~ "
                    "(value_json.dashboard_page_count | default(1)) }}"
                ),
            },
            "profile": {
                "name": "Dashboard profile",
                "value_template": "{{ value_json.assigned_profile }}",
            },
            "policy_sync": {
                "name": "Fleet policy sync",
                "value_template": "{{ value_json.policy_sync_state }}",
                "entity_category": "diagnostic",
            },
            "policy_revision": {
                "name": "Fleet policy revision",
                "state_class": "measurement",
                "value_template": "{{ value_json.policy_revision }}",
                "entity_category": "diagnostic",
            },
            "reported_policy_revision": {
                "name": "Reported fleet policy revision",
                "state_class": "measurement",
                "value_template": "{{ value_json.reported_policy_revision }}",
                "entity_category": "diagnostic",
            },
            "sleep_reason": {
                "name": "Sleep reason",
                "value_template": "{{ value_json.sleep_reason }}",
                "entity_category": "diagnostic",
            },
            "wake_reason": {
                "name": "Wake reason",
                "value_template": "{{ value_json.wake_reason }}",
                "entity_category": "diagnostic",
            },
            "reset_reason": {
                "name": "Reset reason",
                "value_template": "{{ value_json.reset_reason }}",
                "entity_category": "diagnostic",
            },
            "reset_count": {
                "name": "Recorded boots",
                "state_class": "total_increasing",
                "value_template": "{{ value_json.reset_count }}",
                "entity_category": "diagnostic",
            },
            "watchdog_resets": {
                "name": "Watchdog resets",
                "state_class": "total_increasing",
                "value_template": "{{ value_json.watchdog_reset_count }}",
                "entity_category": "diagnostic",
            },
            "sd_failure_events": {
                "name": "SD failure events",
                "state_class": "total_increasing",
                "value_template": "{{ value_json.sd_failure_events }}",
                "entity_category": "diagnostic",
            },
            "checkin_health": {
                "name": "Check-in health",
                "value_template": "{{ value_json.checkin_health }}",
                "entity_category": "diagnostic",
            },
            "missed_checkins": {
                "name": "Estimated missed check-ins",
                "state_class": "measurement",
                "value_template": "{{ value_json.missed_checkins }}",
                "entity_category": "diagnostic",
            },
            "maintenance_window": {
                "name": "Firmware maintenance window",
                "value_template": (
                    "{{ value_json.firmware_maintenance_start ~ '-' ~ "
                    "value_json.firmware_maintenance_end ~ ' ' ~ "
                    "value_json.firmware_maintenance_timezone }}"
                ),
                "entity_category": "diagnostic",
            },
            "uptime": {
                "name": "Uptime",
                "device_class": "duration",
                "unit_of_measurement": "s",
                "value_template": "{{ value_json.uptime_seconds }}",
                "entity_category": "diagnostic",
            },
            "free_heap": {
                "name": "Free memory",
                "device_class": "data_size",
                "unit_of_measurement": "B",
                "value_template": "{{ value_json.free_heap }}",
                "entity_category": "diagnostic",
            },
            "open_display_transport": {
                "name": "OpenDisplay last transport",
                "value_template": "{{ value_json.open_display_last_transport }}",
                "entity_category": "diagnostic",
            },
            "open_display_fallback": {
                "name": "OpenDisplay fallback reason",
                "value_template": "{{ value_json.open_display_fallback }}",
                "entity_category": "diagnostic",
            },
            "open_display_min_heap": {
                "name": "OpenDisplay minimum free memory",
                "device_class": "data_size",
                "unit_of_measurement": "B",
                "value_template": "{{ value_json.open_display_min_free_heap }}",
                "entity_category": "diagnostic",
            },
            "open_display_min_block": {
                "name": "OpenDisplay minimum largest memory block",
                "device_class": "data_size",
                "unit_of_measurement": "B",
                "value_template": "{{ value_json.open_display_min_largest_block }}",
                "entity_category": "diagnostic",
            },
            "transfer_encoding": {
                "name": "Last screen transfer",
                "value_template": "{{ value_json.last_transfer_encoding }}",
                "entity_category": "diagnostic",
            },
            "transfer_bytes": {
                "name": "Last transfer size",
                "device_class": "data_size",
                "state_class": "measurement",
                "unit_of_measurement": "B",
                "value_template": "{{ value_json.last_transfer_bytes }}",
                "entity_category": "diagnostic",
            },
            "transfer_saved_bytes": {
                "name": "Transfer bytes saved",
                "device_class": "data_size",
                "state_class": "measurement",
                "unit_of_measurement": "B",
                "value_template": "{{ value_json.last_transfer_saved_bytes }}",
                "entity_category": "diagnostic",
            },
            "transfer_savings": {
                "name": "Transfer savings",
                "state_class": "measurement",
                "unit_of_measurement": "%",
                "value_template": "{{ value_json.last_transfer_savings_percent }}",
                "entity_category": "diagnostic",
            },
            "last_image_error": {
                "name": "Last image conversion error",
                "value_template": "{{ value_json.last_image_error | default('') }}",
                "entity_category": "diagnostic",
            },
            "screen_history_count": {
                "name": "Saved screens",
                "value_template": "{{ value_json.screen_history_count }}",
                "entity_category": "diagnostic",
            },
            "firmware_stage": {
                "name": "Firmware update stage",
                "value_template": "{{ value_json.firmware_update_stage }}",
                "entity_category": "diagnostic",
            },
            "firmware_progress": {
                "name": "Firmware update progress",
                "unit_of_measurement": "%",
                "value_template": "{{ value_json.firmware_update_percent }}",
                "entity_category": "diagnostic",
            },
            "firmware_error": {
                "name": "Firmware update error",
                "value_template": "{{ value_json.firmware_update_error }}",
                "entity_category": "diagnostic",
            },
            "firmware_rollout": {
                "name": "Firmware rollout",
                "value_template": "{{ value_json.firmware_rollout_status }}",
                "entity_category": "diagnostic",
            },
            "firmware_install_blockers": {
                "name": "Firmware install blockers",
                "value_template": (
                    "{{ value_json.firmware_install_blockers | default([]) | join('; ') }}"
                ),
                "entity_category": "diagnostic",
            },
            "last_action": {
                "name": "Last management action",
                "value_template": "{{ value_json.last_management_action_detail }}",
                "entity_category": "diagnostic",
            },
        }
        if descriptor.firmware.provider != "xteink":
            sensors.pop("sd_failure_events", None)
        if not descriptor.hardware.board_id:
            sensors.pop("board_id", None)
        if not descriptor.hardware.hardware_revision:
            sensors.pop("hardware_revision", None)
        if not descriptor.hardware.mcu_family:
            sensors.pop("mcu_family", None)
        if descriptor.hardware.flash_size_bytes is None:
            sensors.pop("flash_size", None)
        if not descriptor.frontlight.supports_brightness:
            sensors.pop("frontlight_brightness", None)
        if not descriptor.frontlight.supports_warmth:
            sensors.pop("frontlight_warmth", None)
        if not descriptor.power.reports_battery:
            for key in ("battery", "battery_voltage", "battery_runtime", "battery_drain"):
                sensors.pop(key, None)
        if not descriptor.firmware.manageable:
            for key in (
                "firmware_stage",
                "firmware_progress",
                "firmware_error",
                "firmware_rollout",
                "firmware_install_blockers",
            ):
                sensors.pop(key, None)
        for key, extra in sensors.items():
            payload = {
                **self._base(
                    device=device,
                    unique_id=f"flexdisplay_{slug}_{key}",
                    state_topic=state_topic,
                ),
                **extra,
            }
            configs.append((f"sensor/{slug}/{key}", payload))

        binary_sensors: dict[str, dict[str, Any]] = {
            "online": {
                "name": "Online",
                "device_class": "connectivity",
                "value_template": "{{ 'ON' if value_json.online else 'OFF' }}",
            },
            "usb_connected": {
                "name": "USB power",
                "device_class": "plug",
                "value_template": "{{ 'ON' if value_json.usb_connected else 'OFF' }}",
            },
            "checkin_overdue": {
                "name": "Check-in overdue",
                "device_class": "problem",
                "value_template": (
                    "{{ 'ON' if (value_json.missed_checkins | default(0) | int) >= 2 "
                    "else 'OFF' }}"
                ),
                "entity_category": "diagnostic",
            },
            "problem_reset": {
                "name": "Problem reset detected",
                "device_class": "problem",
                "value_template": (
                    "{{ 'ON' if value_json.reset_reason in "
                    "['panic','interrupt_watchdog','task_watchdog','watchdog','brownout'] "
                    "else 'OFF' }}"
                ),
                "entity_category": "diagnostic",
            },
            "repeated_sd_failure": {
                "name": "Repeated SD failure",
                "device_class": "problem",
                "value_template": (
                    "{{ 'ON' if (value_json.consecutive_sd_failures | default(0) | int) >= 2 "
                    "else 'OFF' }}"
                ),
                "entity_category": "diagnostic",
            },
            "maintenance_window_open": {
                "name": "Firmware maintenance window open",
                "value_template": (
                    "{{ 'ON' if value_json.firmware_maintenance_window_open else 'OFF' }}"
                ),
                "entity_category": "diagnostic",
            },
            "sd_ready": {
                "name": "SD card ready",
                "value_template": "{{ 'ON' if value_json.sd_ready else 'OFF' }}",
                "entity_category": "diagnostic",
            },
            "low_battery": {
                "name": "Low battery",
                "device_class": "battery",
                "value_template": "{{ 'ON' if value_json.low_battery else 'OFF' }}",
            },
            "home_assistant_error": {
                "name": "Home Assistant error",
                "device_class": "problem",
                "value_template": "{{ 'ON' if value_json.ha_error else 'OFF' }}",
            },
            "frontlight_on": {
                "name": "Frontlight on",
                "value_template": (
                    "{{ 'ON' if value_json.frontlight_on else 'OFF' }}"
                ),
            },
            "image_conversion_error": {
                "name": "Image conversion error",
                "device_class": "problem",
                "value_template": (
                    "{{ 'ON' if value_json.image_conversion_error else 'OFF' }}"
                ),
                "entity_category": "diagnostic",
            },
            "firmware_update_available": {
                "name": "Firmware update available",
                "device_class": "update",
                "value_template": "{{ 'ON' if value_json.update_available else 'OFF' }}",
            },
            "firmware_update_problem": {
                "name": "Firmware update problem",
                "device_class": "problem",
                "value_template": (
                    "{{ 'ON' if value_json.firmware_update_status in "
                    "['failed', 'cancelled'] else 'OFF' }}"
                ),
            },
        }
        if descriptor.firmware.provider != "xteink":
            binary_sensors.pop("repeated_sd_failure", None)
            binary_sensors.pop("sd_ready", None)
        if not descriptor.power.reports_battery:
            binary_sensors.pop("low_battery", None)
        if not descriptor.frontlight.supports_on:
            binary_sensors.pop("frontlight_on", None)
        if not descriptor.firmware.manageable:
            binary_sensors.pop("firmware_update_available", None)
            binary_sensors.pop("firmware_update_problem", None)
        for key, extra in binary_sensors.items():
            payload = {
                **self._base(
                    device=device,
                    unique_id=f"flexdisplay_{slug}_{key}",
                    state_topic=state_topic,
                ),
                "payload_on": "ON",
                "payload_off": "OFF",
                **extra,
            }
            configs.append((f"binary_sensor/{slug}/{key}", payload))

        buttons = {
            "refresh": ("Refresh", "refresh"),
            "full_refresh": ("Force full refresh", "full-refresh"),
            "previous": ("Previous screen", "previous"),
            "next": ("Next screen", "next"),
            "overview": ("Return to overview", "overview"),
            "clear": ("Clear display", "clear"),
            "sleep": ("Sleep now", "sleep"),
            "power_off": ("Power off until button wake", "power-off"),
            "restart": ("Restart", "restart"),
            "cancel": ("Cancel active commands", "cancel"),
            "firmware_retry": ("Retry firmware update", "firmware-retry"),
            "rollout_reset": ("Reset firmware rollout", "rollout-reset"),
            "resend_screen": ("Resend current screen", "resend-screen"),
        }
        buttons = {
            key: value
            for key, value in buttons.items()
            if value[1] in descriptor.management.actions
            or value[1] == "cancel"
            or (
                value[1] in {"firmware-retry", "rollout-reset"}
                and descriptor.supports_xteink_ota
            )
            or (
                value[1] == "resend-screen"
                and "refresh" in descriptor.management.actions
                and descriptor.management.supports_screen_history
            )
        }
        for key, (name, command) in buttons.items():
            payload = {
                **self._base(device=device, unique_id=f"flexdisplay_{slug}_{key}"),
                "name": name,
                "command_topic": f"{command_root}/{command}",
                "payload_press": "PRESS",
            }
            configs.append((f"button/{slug}/{key}", payload))

        switches = {
            "auto_start": (
                "Automatic display mode",
                "assigned_auto_start",
                "set-auto-start",
            ),
            "live_mode": ("Live polling", "assigned_live_mode", "set-live-mode"),
            "intelligent_sleep": (
                "Intelligent sleep",
                "assigned_intelligent_sleep",
                "set-intelligent-sleep",
            ),
            "stay_awake_on_usb": (
                "Stay awake on USB",
                "assigned_stay_awake_on_usb",
                "set-stay-awake-on-usb",
            ),
            "frontlight": (
                "Frontlight",
                "desired_frontlight_on",
                "set-frontlight-on",
            ),
        }
        switches = {
            key: value
            for key, value in switches.items()
            if (
                key in {"auto_start", "live_mode"}
                and descriptor.management.supports_fleet_policy
            )
            or (
                key == "intelligent_sleep"
                and descriptor.management.supports_sleep_policy
            )
            or (
                key == "stay_awake_on_usb"
                and descriptor.management.supports_sleep_policy
                and descriptor.power.reports_usb_power
            )
            or (key == "frontlight" and descriptor.frontlight.supports_on)
        }
        for key, (name, field, command) in switches.items():
            payload = {
                **self._base(
                    device=device,
                    unique_id=f"flexdisplay_{slug}_{key}",
                    state_topic=state_topic,
                ),
                "name": name,
                "value_template": f"{{{{ 'ON' if value_json.{field} else 'OFF' }}}}",
                "command_topic": f"{command_root}/{command}",
                "payload_on": "ON",
                "payload_off": "OFF",
                "state_on": "ON",
                "state_off": "OFF",
                "entity_category": "config",
            }
            configs.append((f"switch/{slug}/{key}", payload))

        numbers = {
            "refresh_interval": (
                "Refresh interval",
                "assigned_refresh_interval_seconds",
                "set-refresh-interval",
                60,
                86400,
                60,
                "s",
            ),
            "manual_sleep": (
                "Manual sleep duration",
                "assigned_manual_sleep_seconds",
                "set-manual-sleep",
                60,
                86400,
                60,
                "s",
            ),
            "critical_battery": (
                "Critical battery threshold",
                "assigned_critical_battery_percent",
                "set-critical-battery",
                5,
                50,
                1,
                "%",
            ),
            "low_battery": (
                "Low battery threshold",
                "assigned_low_battery_percent",
                "set-low-battery",
                10,
                80,
                1,
                "%",
            ),
            "low_battery_multiplier": (
                "Low battery interval multiplier",
                "assigned_low_battery_multiplier",
                "set-low-battery-multiplier",
                1,
                12,
                1,
                "x",
            ),
            "unchanged_multiplier": (
                "Unchanged image interval multiplier",
                "assigned_unchanged_image_multiplier",
                "set-unchanged-multiplier",
                1,
                12,
                1,
                "x",
            ),
            "manual_wake_grace": (
                "Manual wake grace",
                "assigned_manual_wake_grace_seconds",
                "set-manual-wake-grace",
                0,
                600,
                5,
                "s",
            ),
            "frontlight_brightness": (
                "Frontlight brightness",
                "desired_frontlight_brightness",
                "set-frontlight-brightness",
                descriptor.frontlight.minimum,
                descriptor.frontlight.maximum,
                1,
                "%",
            ),
            "frontlight_warmth": (
                "Frontlight warmth",
                "desired_frontlight_warmth",
                "set-frontlight-warmth",
                descriptor.frontlight.minimum,
                descriptor.frontlight.maximum,
                1,
                "%",
            ),
        }
        numbers = {
            key: value
            for key, value in numbers.items()
            if (key == "refresh_interval" and descriptor.management.supports_fleet_policy)
            or (
                key in {"manual_sleep", "manual_wake_grace"}
                and descriptor.management.supports_sleep_policy
            )
            or (
                key
                in {
                    "critical_battery",
                    "low_battery",
                    "low_battery_multiplier",
                }
                and descriptor.management.supports_battery_policy
            )
            or (
                key == "unchanged_multiplier"
                and descriptor.management.supports_rendering_profile
            )
            or (
                key == "frontlight_brightness"
                and descriptor.frontlight.supports_brightness
            )
            or (
                key == "frontlight_warmth"
                and descriptor.frontlight.supports_warmth
            )
        }
        for key, (
            name,
            field,
            command,
            minimum,
            maximum,
            step,
            unit,
        ) in numbers.items():
            payload = {
                **self._base(
                    device=device,
                    unique_id=f"flexdisplay_{slug}_{key}",
                    state_topic=state_topic,
                ),
                "name": name,
                "value_template": f"{{{{ value_json.{field} }}}}",
                "command_topic": f"{command_root}/{command}",
                "min": minimum,
                "max": maximum,
                "step": step,
                "mode": "box",
                "entity_category": "config",
            }
            if unit:
                payload["unit_of_measurement"] = unit
            configs.append((f"number/{slug}/{key}", payload))

        selects = {
            "mode": (
                "Assigned mode",
                "assigned_mode",
                "set-mode",
                state.get("available_modes")
                or ["home_assistant", "reader", "trmnl", "opendisplay", "photo_frame"],
            ),
            "profile": (
                "Dashboard profile",
                "assigned_profile",
                "set-profile",
                state.get("available_profiles") or [profile.profile],
            ),
            "policy": (
                "Fleet policy",
                "assigned_policy_name",
                "set-policy",
                state.get("available_policy_profiles")
                or ["battery_saver", "balanced", "usb_kiosk"],
            ),
            "open_display_transport": (
                "OpenDisplay transport",
                "assigned_open_display_transport_policy",
                "set-opendisplay-transport",
                ["auto", "lan_preferred", "ble_only"],
            ),
        }
        selects = {
            key: value
            for key, value in selects.items()
            if (key == "mode" and bool(descriptor.management.modes))
            or (
                key == "profile"
                and descriptor.management.supports_dashboard_profiles
            )
            or (key == "policy" and descriptor.management.supports_fleet_policy)
            or (
                key == "open_display_transport"
                and descriptor.management.supports_opendisplay_policy
            )
        }
        for key, (name, field, command, options) in selects.items():
            payload = {
                **self._base(
                    device=device,
                    unique_id=f"flexdisplay_{slug}_{key}",
                    state_topic=state_topic,
                ),
                "name": name,
                "value_template": f"{{{{ value_json.{field} }}}}",
                "command_topic": f"{command_root}/{command}",
                "options": list(
                    dict.fromkeys(str(option) for option in options if option)
                ),
                "entity_category": "config",
            }
            configs.append((f"select/{slug}/{key}", payload))

        texts = {
            "name": ("Device name", "name", "set-name", 64),
            "area": ("Area", "area", "set-area", 64),
            "timezone": (
                "Timezone",
                "assigned_timezone",
                "set-timezone",
                64,
            ),
            "active_start": (
                "Active hours start",
                "assigned_active_start",
                "set-active-start",
                5,
            ),
            "active_end": (
                "Active hours end",
                "assigned_active_end",
                "set-active-end",
                5,
            ),
        }
        texts = {
            key: value
            for key, value in texts.items()
            if (
                key in {"name", "area"}
                and descriptor.management.supports_provisioning
            )
            or (
                key in {"timezone", "active_start", "active_end"}
                and descriptor.management.supports_sleep_policy
            )
        }
        for key, (name, field, command, maximum) in texts.items():
            payload = {
                **self._base(
                    device=device,
                    unique_id=f"flexdisplay_{slug}_{key}",
                    state_topic=state_topic,
                ),
                "name": name,
                "value_template": f"{{{{ value_json.{field} }}}}",
                "command_topic": f"{command_root}/{command}",
                "mode": "text",
                "min": 0,
                "max": maximum,
                "entity_category": "config",
            }
            configs.append((f"text/{slug}/{key}", payload))

        if descriptor.firmware.manageable:
            update = {
                **self._base(
                    device=device,
                    unique_id=f"flexdisplay_{slug}_firmware_update",
                    state_topic=state_topic,
                ),
                "name": "Firmware",
                "title": "FlexDisplay firmware",
                "device_class": "firmware",
                "value_template": (
                    "{{ {'installed_version': value_json.firmware, "
                    "'latest_version': value_json.latest_firmware, "
                    "'in_progress': value_json.firmware_update_stage in "
                    "['queued', 'dispatched', 'preflight', 'downloading', "
                    "'validating', 'flashing', 'rebooting'], "
                    "'update_percentage': value_json.firmware_update_percent "
                    "if value_json.firmware_update_stage in "
                    "['queued', 'dispatched', 'preflight', 'downloading', "
                    "'validating', 'flashing', 'rebooting'] else none} | to_json }}"
                ),
                "command_topic": f"{command_root}/install",
                "payload_install": "PRESS",
                "entity_category": "config",
            }
            configs.append((f"update/{slug}/firmware", update))

        if descriptor.inputs.event_types:
            event = {
                **self._base(
                    device=device,
                    unique_id=f"flexdisplay_{slug}_physical_button",
                    state_topic=f"{self.config.topic_prefix}/{device_id}/event",
                ),
                "name": "Physical button",
                "event_types": ["short", "double", "long"],
                "value_template": "{{ value_json.event_type }}",
            }
            configs.append((f"event/{slug}/physical_button", event))
        image = {
            **self._base(
                device=device,
                unique_id=f"flexdisplay_{slug}_current_screen",
            ),
            "name": "Current screen",
            "image_topic": f"{self.config.topic_prefix}/{device_id}/screen",
            "content_type": "image/png",
        }
        configs.append((f"image/{slug}/current_screen", image))
        return configs

    def publish_device(
        self, device_id: str, profile: DeviceConfig, state: dict[str, Any]
    ) -> None:
        if not self.client or not self.connected:
            return
        configs = self._configs(device_id, profile, state)
        descriptor = _device_descriptor(profile, state)
        if descriptor.firmware.provider != "xteink" or not descriptor.firmware.manageable:
            # Clear retained inapplicable entities published by an older Bridge.
            slug = _slug(device_id)
            topic_suffixes = []
            if not descriptor.firmware.manageable:
                topic_suffixes.extend(
                    (
                        f"update/{slug}/firmware",
                        f"sensor/{slug}/firmware_stage",
                        f"sensor/{slug}/firmware_progress",
                        f"sensor/{slug}/firmware_error",
                        f"sensor/{slug}/firmware_rollout",
                        f"sensor/{slug}/firmware_install_blockers",
                        f"binary_sensor/{slug}/firmware_update_available",
                        f"binary_sensor/{slug}/firmware_update_problem",
                    )
                )
            if descriptor.firmware.provider != "xteink":
                topic_suffixes.extend(
                    (
                        f"sensor/{slug}/sd_failure_events",
                        f"binary_sensor/{slug}/repeated_sd_failure",
                        f"binary_sensor/{slug}/sd_ready",
                    )
                )
            if not descriptor.power.reports_battery:
                topic_suffixes.extend(
                    (
                        f"sensor/{slug}/battery",
                        f"sensor/{slug}/battery_voltage",
                        f"sensor/{slug}/battery_runtime",
                        f"sensor/{slug}/battery_drain",
                        f"binary_sensor/{slug}/low_battery",
                    )
                )
            for topic_suffix in topic_suffixes:
                self.client.publish(
                    f"{self.config.discovery_prefix}/{topic_suffix}/config",
                    "",
                    retain=True,
                )
        slug = _slug(device_id)
        published_topics = {topic_suffix for topic_suffix, _payload in configs}
        capability_topics = {
            *(f"button/{slug}/{key}" for key in (
                "refresh",
                "full_refresh",
                "previous",
                "next",
                "overview",
                "clear",
                "sleep",
                "power_off",
                "restart",
                "cancel",
                "firmware_retry",
                "rollout_reset",
                "resend_screen",
            )),
            *(f"switch/{slug}/{key}" for key in (
                "auto_start",
                "live_mode",
                "intelligent_sleep",
                "stay_awake_on_usb",
                "frontlight",
            )),
            *(f"number/{slug}/{key}" for key in (
                "refresh_interval",
                "manual_sleep",
                "critical_battery",
                "low_battery",
                "low_battery_multiplier",
                "unchanged_multiplier",
                "manual_wake_grace",
                "frontlight_brightness",
                "frontlight_warmth",
            )),
            *(f"select/{slug}/{key}" for key in (
                "mode",
                "profile",
                "policy",
                "open_display_transport",
            )),
            *(f"text/{slug}/{key}" for key in (
                "name",
                "area",
                "timezone",
                "active_start",
                "active_end",
            )),
            *(f"sensor/{slug}/{key}" for key in (
                "board_id",
                "hardware_revision",
                "mcu_family",
                "flash_size",
                "frontlight_brightness",
                "frontlight_warmth",
            )),
            f"binary_sensor/{slug}/frontlight_on",
            f"event/{slug}/physical_button",
            f"update/{slug}/firmware",
        }
        for topic_suffix in capability_topics - published_topics:
            self.client.publish(
                f"{self.config.discovery_prefix}/{topic_suffix}/config",
                "",
                retain=True,
            )
        for topic_suffix, payload in configs:
            topic = f"{self.config.discovery_prefix}/{topic_suffix}/config"
            self.client.publish(
                topic,
                json.dumps(payload) if self.discovery_enabled else "",
                retain=True,
            )

        state_topic = f"{self.config.topic_prefix}/{device_id}/state"
        self.client.publish(state_topic, json.dumps(state), retain=True)
        events = state.get("recent_button_events") or []
        if events and isinstance(events[-1], dict):
            event = events[-1]
            if event.get("button") not in descriptor.inputs.event_types:
                return
            identity = ":".join(
                str(event.get(key) or "")
                for key in ("sequence", "button", "gesture", "uptime")
            )
            if identity and self._last_event.get(device_id) != identity:
                self._last_event[device_id] = identity
                self.client.publish(
                    f"{self.config.topic_prefix}/{device_id}/event",
                    json.dumps(
                        {
                            "event_type": str(event.get("gesture") or "short"),
                            "button": event.get("button"),
                            "mode": event.get("mode"),
                            "sequence": event.get("sequence"),
                            "occurred_at": event.get("occurred_at")
                            or state.get("last_seen"),
                        }
                    ),
                    retain=False,
                )

    def _clear_device(
        self,
        device_id: str,
        profile: DeviceConfig,
        state: dict[str, Any],
    ) -> None:
        if not self.client or not self.connected:
            return
        for topic_suffix, _payload in self._configs(device_id, profile, state):
            self.client.publish(
                f"{self.config.discovery_prefix}/{topic_suffix}/config",
                "",
                retain=True,
            )
        for suffix in ("state", "event", "screen"):
            self.client.publish(
                f"{self.config.topic_prefix}/{device_id}/{suffix}",
                "",
                retain=True,
            )
        self._last_event.pop(device_id, None)
        self._pending_device_removals.pop(device_id, None)

    def remove_device(
        self,
        device_id: str,
        profile: DeviceConfig | None = None,
        state: dict[str, Any] | None = None,
    ) -> None:
        """Clear retained discovery and state for a removed fleet display."""
        selected_profile = profile or DeviceConfig(name=device_id)
        selected_state = state or {"device_id": device_id}
        self._pending_device_removals[device_id] = (
            selected_profile,
            selected_state,
        )
        self._clear_device(device_id, selected_profile, selected_state)

    def publish_screen(self, device_id: str, content: bytes) -> None:
        """Retain the current PNG for the App-only Home Assistant image entity."""
        if (
            not self.client
            or not self.connected
            or not self.discovery_enabled
            or not content
        ):
            return
        self.client.publish(
            f"{self.config.topic_prefix}/{device_id}/screen",
            content,
            retain=True,
        )

    def publish_screen_refresh(
        self,
        device_id: str,
        *,
        reason: str = "refresh",
        command_id: str = "",
        queued_at: str = "",
    ) -> bool:
        """Publish a non-retained wake hint for MQTT-capable colour displays."""
        if not self.client or not self.connected:
            return False
        payload = {
            "event": "screen_refresh",
            "device_id": device_id,
            "reason": str(reason or "refresh")[:80],
            "command_id": str(command_id or ""),
            "queued_at": str(queued_at or ""),
        }
        self.client.publish(
            f"{self.config.topic_prefix}/{device_id}/event/screen",
            json.dumps(payload),
            retain=False,
        )
        return True

    def publish_flexhub(self, summary: dict[str, Any]) -> None:
        """Publish the SenseCAP FlexHub as a Home Assistant MQTT device."""
        if not self.client or not self.connected:
            return
        status = (
            summary.get("status") if isinstance(summary.get("status"), dict) else {}
        )
        fleet = status.get("fleet") if isinstance(status.get("fleet"), dict) else {}
        storage = (
            status.get("storage") if isinstance(status.get("storage"), dict) else {}
        )
        network = (
            status.get("network") if isinstance(status.get("network"), dict) else {}
        )
        meshtastic = (
            status.get("meshtastic")
            if isinstance(status.get("meshtastic"), dict)
            else {}
        )
        console = (
            summary.get("meshtastic_console")
            if isinstance(summary.get("meshtastic_console"), dict)
            else {}
        )
        last_record = console.get("last_message")
        if isinstance(last_record, dict):
            self._flexhub_console_state.update(
                {
                    "last_message": last_record.get("text") or "",
                    "last_sender": last_record.get("sender_name")
                    or last_record.get("sender")
                    or last_record.get("from")
                    or console.get("last_sender")
                    or "",
                    "last_channel": last_record.get("channel_name")
                    or last_record.get("channel")
                    or console.get("last_channel")
                    or 0,
                    "last_message_at": last_record.get("received_at")
                    or last_record.get("timestamp")
                    or last_record.get("time")
                    or console.get("last_message_at")
                    or "",
                }
            )
        elif last_record is not None:
            self._flexhub_console_state["last_message"] = last_record
        for source, target in {
            "last_sender": "last_sender",
            "last_channel": "last_channel",
            "last_message_at": "last_message_at",
            "unread_count": "unread_count",
        }.items():
            if source in console:
                self._flexhub_console_state[target] = console[source]
        devices = fleet.get("devices") if isinstance(fleet.get("devices"), list) else []
        state = {
            "connected": bool(summary.get("connected")),
            "url": summary.get("url") or "",
            "last_seen": summary.get("last_seen") or "",
            "error": summary.get("error") or "",
            "state": status.get("state") or "offline",
            "detail": status.get("detail")
            or summary.get("error")
            or "Waiting for FlexHub",
            "firmware": status.get("firmware") or "unknown",
            "platform_version": status.get("platform_version") or "unknown",
            "target_count": status.get("target_count") or 0,
            "delivered": status.get("delivered") or 0,
            "failed": status.get("failed") or 0,
            "active_job": status.get("active_job") or "",
            "remote_commands_enabled": bool(status.get("remote_commands_enabled")),
            "access_control_enabled": bool(status.get("access_control_enabled")),
            "slideshow_enabled": bool((status.get("slideshow") or {}).get("enabled")),
            "slideshow_interval_seconds": (status.get("slideshow") or {}).get(
                "interval_seconds"
            ),
            "healthy_boots": (status.get("boot_health") or {}).get("count"),
            "fleet_bridge_connected": bool(fleet.get("connected")),
            "fleet_devices": len(devices),
            "fleet_online": sum(
                bool(item.get("online")) for item in devices if isinstance(item, dict)
            ),
            "fleet_policy_pending": sum(
                item.get("policy_sync_state") == "pending"
                for item in devices
                if isinstance(item, dict)
            ),
            "selected_policy": fleet.get("selected_policy") or "unknown",
            "selected_scope": fleet.get("selected_scope") or "unknown",
            "storage_ready": bool(storage.get("ready")),
            "storage_free_bytes": storage.get("free_bytes") or 0,
            "ip_address": network.get("ip") or "",
            "wifi_rssi": network.get("rssi"),
            "free_heap": network.get("free_heap"),
            "uptime_seconds": network.get("uptime_seconds"),
            "meshtastic_node_id": meshtastic.get("node_id") or "",
            "meshtastic_firmware": meshtastic.get("firmware")
            or status.get("firmware")
            or "unknown",
            "meshtastic_nodes": meshtastic.get("node_count"),
            "meshtastic_online_nodes": meshtastic.get("online_node_count"),
            "meshtastic_mqtt_enabled": bool(meshtastic.get("mqtt_enabled")),
            "meshtastic_mqtt_connected": bool(meshtastic.get("mqtt_connected")),
        }
        state_topic = f"{self.config.topic_prefix}/flexhub/state"
        availability_topic = f"{self.config.topic_prefix}/flexhub/availability"
        device = {
            "identifiers": ["flexdisplay_flexhub"],
            "name": "SenseCAP FlexHub",
            "manufacturer": "Seeed Studio / FlexDisplay",
            "model": "SenseCAP Indicator FlexHub",
            "sw_version": state["firmware"],
        }
        if summary.get("url"):
            device["configuration_url"] = summary["url"]
        sensor_fields = {
            "state": ("State", None, None),
            "detail": ("Current task", None, None),
            "firmware": ("Meshtastic firmware", None, "diagnostic"),
            "platform_version": ("FlexDisplay platform", None, "diagnostic"),
            "target_count": ("Known receivers", None, None),
            "delivered": ("Last delivered", None, None),
            "failed": ("Last failed", None, None),
            "active_job": ("Active saved job", None, None),
            "slideshow_interval_seconds": ("Slideshow interval", "s", "config"),
            "healthy_boots": ("Healthy boots", None, "diagnostic"),
            "fleet_devices": ("Managed displays", None, None),
            "fleet_online": ("Online displays", None, None),
            "fleet_policy_pending": ("Policy acknowledgements pending", None, None),
            "selected_policy": ("Selected fleet policy", None, "config"),
            "selected_scope": ("Selected fleet scope", None, "config"),
            "storage_free_bytes": ("SD free space", "B", "diagnostic"),
            "ip_address": ("IP address", None, "diagnostic"),
            "wifi_rssi": ("Wi-Fi signal", "dBm", "diagnostic"),
            "free_heap": ("Free memory", "B", "diagnostic"),
            "uptime_seconds": ("Uptime", "s", "diagnostic"),
            "meshtastic_node_id": ("Meshtastic node ID", None, "diagnostic"),
            "meshtastic_nodes": ("Meshtastic nodes", None, "diagnostic"),
            "meshtastic_online_nodes": ("Meshtastic online nodes", None, "diagnostic"),
        }
        for field, (name, unit, category) in sensor_fields.items():
            payload = {
                **self._base(
                    device=device,
                    unique_id=f"flexdisplay_flexhub_{field}",
                    state_topic=state_topic,
                ),
                "name": name,
                "value_template": f"{{{{ value_json.{field} }}}}",
                "availability_topic": availability_topic,
            }
            if unit:
                payload["unit_of_measurement"] = unit
            if category:
                payload["entity_category"] = category
            self.client.publish(
                f"{self.config.discovery_prefix}/sensor/flexhub/{field}/config",
                json.dumps(payload) if self.discovery_enabled else "",
                retain=True,
            )
        for field, name in {
            "fleet_bridge_connected": "FlexDisplay Bridge connected",
            "storage_ready": "SD storage ready",
            "remote_commands_enabled": "Remote commands enabled",
            "access_control_enabled": "Access control enabled",
            "slideshow_enabled": "Slideshow active",
            "meshtastic_mqtt_enabled": "Meshtastic MQTT enabled",
            "meshtastic_mqtt_connected": "Meshtastic MQTT connected",
        }.items():
            payload = {
                **self._base(
                    device=device,
                    unique_id=f"flexdisplay_flexhub_{field}",
                    state_topic=state_topic,
                ),
                "name": name,
                "value_template": f"{{{{ 'ON' if value_json.{field} else 'OFF' }}}}",
                "payload_on": "ON",
                "payload_off": "OFF",
                "entity_category": "diagnostic",
                "availability_topic": availability_topic,
            }
            self.client.publish(
                f"{self.config.discovery_prefix}/binary_sensor/flexhub/{field}/config",
                json.dumps(payload) if self.discovery_enabled else "",
                retain=True,
            )

        console_state_topic = f"{self.config.topic_prefix}/flexhub/meshtastic/state"
        for field, name in {
            "last_message": "Meshtastic last message",
            "last_sender": "Meshtastic last sender",
            "last_channel": "Meshtastic last channel",
            "last_message_at": "Meshtastic last message time",
            "unread_count": "Meshtastic unread messages",
            "last_send_status": "Meshtastic last send status",
            "last_send_error": "Meshtastic last send error",
        }.items():
            payload = {
                **self._base(
                    device=device,
                    unique_id=f"flexdisplay_flexhub_meshtastic_{field}",
                    state_topic=console_state_topic,
                ),
                "name": name,
                "value_template": f"{{{{ value_json.{field} }}}}",
                "availability_topic": availability_topic,
            }
            self.client.publish(
                f"{self.config.discovery_prefix}/sensor/flexhub/meshtastic_{field}/config",
                json.dumps(payload) if self.discovery_enabled else "",
                retain=True,
            )

        event_payload = {
            **self._base(
                device=device,
                unique_id="flexdisplay_flexhub_meshtastic_message",
                state_topic=f"{self.config.topic_prefix}/flexhub/meshtastic/event",
            ),
            "name": "Meshtastic message",
            "event_types": ["message_received", "message_sent", "message_failed"],
            "value_template": "{{ value_json.event_type }}",
            "availability_topic": availability_topic,
        }
        self.client.publish(
            f"{self.config.discovery_prefix}/event/flexhub/meshtastic_message/config",
            json.dumps(event_payload) if self.discovery_enabled else "",
            retain=True,
        )
        text_payload = {
            **self._base(
                device=device,
                unique_id="flexdisplay_flexhub_send_meshtastic",
            ),
            "name": "Send Meshtastic broadcast (ASCII)",
            "command_topic": f"{self.config.topic_prefix}/flexhub/command/send-meshtastic",
            "mode": "text",
            "min": 1,
            "max": 220,
            "pattern": r"^[\x20-\x7E]{1,220}$",
            "availability_topic": availability_topic,
        }
        self.client.publish(
            f"{self.config.discovery_prefix}/text/flexhub/send_meshtastic/config",
            json.dumps(text_payload) if self.discovery_enabled else "",
            retain=True,
        )
        clear_payload = {
            **self._base(
                device=device,
                unique_id="flexdisplay_flexhub_clear_meshtastic_unread",
            ),
            "name": "Clear Meshtastic unread count",
            "command_topic": f"{self.config.topic_prefix}/flexhub/command/clear-meshtastic-unread",
            "payload_press": "PRESS",
            "availability_topic": availability_topic,
        }
        self.client.publish(
            f"{self.config.discovery_prefix}/button/flexhub/clear_meshtastic_unread/config",
            json.dumps(clear_payload) if self.discovery_enabled else "",
            retain=True,
        )
        for action, name in {
            "scan": "Scan for FlexDisplay receivers",
            "deliver": "Deliver selected FlexHub job",
            "retry": "Retry failed FlexHub receivers",
            "cancel": "Cancel FlexHub operation",
        }.items():
            action_payload = {
                **self._base(
                    device=device,
                    unique_id=f"flexdisplay_flexhub_{action}",
                ),
                "name": name,
                "command_topic": f"{self.config.topic_prefix}/flexhub/command/{action}",
                "payload_press": "PRESS",
                "availability_topic": availability_topic,
            }
            self.client.publish(
                f"{self.config.discovery_prefix}/button/flexhub/{action}/config",
                json.dumps(action_payload) if self.discovery_enabled else "",
                retain=True,
            )
        self.client.publish(
            availability_topic,
            "online" if summary.get("connected") else "offline",
            retain=True,
        )
        self.client.publish(state_topic, json.dumps(state), retain=True)
        self._publish_flexhub_console_state()

    def _publish_flexhub_console_state(self) -> None:
        """Publish the retained Meshtastic summary used by App-only entities."""
        if not self.client or not self.connected:
            return
        self.client.publish(
            f"{self.config.topic_prefix}/flexhub/meshtastic/state",
            json.dumps(self._flexhub_console_state),
            retain=True,
        )

    def publish_flexhub_message(self, message: dict[str, Any]) -> None:
        """Publish one non-retained Meshtastic event and update retained summary."""
        if not self.client or not self.connected or not isinstance(message, dict):
            return
        has_stable_identity = any(
            message.get(key) not in {None, ""}
            for key in ("sequence", "packet_id", "id")
        )
        identity = (
            ":".join(
                str(message.get(key) or "")
                for key in (
                    "session_id",
                    "sequence",
                    "packet_id",
                    "id",
                    "direction",
                    "delivery_state",
                    "status",
                )
            )
            if has_stable_identity
            else ""
        )
        if identity and identity == self._last_flexhub_message:
            return
        self._last_flexhub_message = identity
        direction = str(message.get("direction") or "incoming")
        delivery = str(message.get("delivery_state") or message.get("status") or "")
        event_type = (
            "message_received"
            if direction in {"incoming", "inbound", "received"}
            else "message_failed"
            if delivery in {"failed", "rejected", "timeout"}
            else "message_sent"
        )
        self._flexhub_console_state.update(
            {
                "last_message": message.get("text") or "",
                "last_sender": message.get("sender_name")
                or message.get("sender")
                or message.get("from")
                or "",
                "last_channel": message.get("channel_name")
                or message.get("channel")
                or 0,
                "last_message_at": message.get("received_at")
                or message.get("timestamp")
                or message.get("time")
                or "",
            }
        )
        if event_type == "message_received":
            self._flexhub_console_state["unread_count"] = (
                int(self._flexhub_console_state.get("unread_count") or 0) + 1
            )
        else:
            self._flexhub_console_state["last_send_status"] = delivery or "submitted"
            self._flexhub_console_state["last_send_error"] = str(
                message.get("error") or ""
            )[:180]
        self._publish_flexhub_console_state()
        self.client.publish(
            f"{self.config.topic_prefix}/flexhub/meshtastic/event",
            json.dumps({**message, "event_type": event_type}),
            retain=False,
        )
