from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable
from typing import Any

import paho.mqtt.client as mqtt

from . import __version__
from .config import DeviceConfig, MqttConfig

LOGGER = logging.getLogger(__name__)
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

    @property
    def discovery_enabled(self) -> bool:
        return self.config.entity_source in {"mqtt", "both"}

    def start(self) -> None:
        if not self.config.enabled:
            return
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="flexdisplay-ha-bridge")
        if self.config.username:
            client.username_pw_set(self.config.username, self.config.password)
        client.on_connect = self._on_connect
        client.on_disconnect = self._on_disconnect
        client.on_message = self._on_message
        client.will_set(f"{self.config.topic_prefix}/bridge/status", "offline", retain=True)
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
            self.client.publish(f"{self.config.topic_prefix}/bridge/status", "offline", retain=True)
            self.client.disconnect()
        self.client.loop_stop()

    def _on_connect(self, client, userdata, flags, reason_code, properties) -> None:
        del userdata, flags, properties
        if reason_code != 0:
            LOGGER.warning("MQTT connection rejected: %s", reason_code)
            return
        self.connected = True
        client.publish(f"{self.config.topic_prefix}/bridge/status", "online", retain=True)
        client.subscribe(f"{self.config.topic_prefix}/+/command/+")
        LOGGER.info(
            "Connected to MQTT broker %s:%s (entity source: %s)",
            self.config.host,
            self.config.port,
            self.config.entity_source,
        )

    def _on_disconnect(self, client, userdata, disconnect_flags, reason_code, properties) -> None:
        del client, userdata, disconnect_flags, properties
        self.connected = False
        if reason_code != 0:
            LOGGER.warning("MQTT disconnected: %s", reason_code)

    def _on_message(self, client, userdata, message) -> None:
        del client, userdata
        parts = message.topic.split("/")
        if len(parts) != 4 or parts[0] != self.config.topic_prefix or parts[2] != "command":
            return
        device_id, command = parts[1], parts[3]
        payload = message.payload.decode("utf-8", errors="replace").strip()
        try:
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
        slug = _slug(device_id)
        state_topic = f"{self.config.topic_prefix}/{device_id}/state"
        command_root = f"{self.config.topic_prefix}/{device_id}/command"
        device = {
            "identifiers": [f"flexdisplay_{device_id}"],
            "name": profile.name,
            "manufacturer": "XTEINK / FlexDisplay",
            "model": profile.model or state.get("model") or "XTEINK",
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
        for key, (name, command) in buttons.items():
            payload = {
                **self._base(device=device, unique_id=f"flexdisplay_{slug}_{key}"),
                "name": name,
                "command_topic": f"{command_root}/{command}",
                "payload_press": "PRESS",
            }
            configs.append((f"button/{slug}/{key}", payload))

        switches = {
            "auto_start": ("Automatic display mode", "assigned_auto_start", "set-auto-start"),
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
        }
        for key, (name, field, command, minimum, maximum, step, unit) in numbers.items():
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
                "options": list(dict.fromkeys(str(option) for option in options if option)),
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

    def publish_device(self, device_id: str, profile: DeviceConfig, state: dict[str, Any]) -> None:
        if not self.client or not self.connected:
            return
        configs = self._configs(device_id, profile, state)
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
            identity = ":".join(
                str(event.get(key) or "") for key in ("sequence", "button", "gesture", "uptime")
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
                            "occurred_at": event.get("occurred_at") or state.get("last_seen"),
                        }
                    ),
                    retain=False,
                )

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
