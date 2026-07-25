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


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")


class MqttService:
    def __init__(self, config: MqttConfig, on_command: Callable[[str, str], None]):
        self.config = config
        self.on_command = on_command
        self.client: mqtt.Client | None = None
        self.connected = False

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
        LOGGER.info("Connected to MQTT broker %s:%s", self.config.host, self.config.port)

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
        self.on_command(device_id, command)

    def publish_device(self, device_id: str, profile: DeviceConfig, state: dict[str, Any]) -> None:
        if not self.client or not self.connected:
            return
        slug = _slug(device_id)
        state_topic = f"{self.config.topic_prefix}/{device_id}/state"
        device = {
            "identifiers": [f"flexdisplay_{device_id}"],
            "name": profile.name,
            "manufacturer": "XTEINK / FlexDisplay",
            "model": profile.model or state.get("model") or "XTEINK",
            "serial_number": device_id,
            "sw_version": state.get("firmware") or "unknown",
        }
        sensors = {
            "battery": {
                "name": "Battery",
                "device_class": "battery",
                "unit_of_measurement": "%",
                "value_template": "{{ value_json.battery_percent }}",
            },
            "rssi": {
                "name": "Wi-Fi signal",
                "device_class": "signal_strength",
                "unit_of_measurement": "dBm",
                "value_template": "{{ value_json.rssi }}",
            },
            "last_seen": {
                "name": "Last seen",
                "device_class": "timestamp",
                "value_template": "{{ value_json.last_seen }}",
            },
            "firmware": {
                "name": "Firmware",
                "value_template": "{{ value_json.firmware }}",
            },
            "mode": {
                "name": "Mode",
                "value_template": "{{ value_json.mode }}",
            },
        }
        for key, extra in sensors.items():
            payload = {
                **extra,
                "unique_id": f"flexdisplay_{slug}_{key}",
                "state_topic": state_topic,
                "device": device,
                "origin": {
                    "name": "FlexDisplay HA Bridge",
                    "sw_version": __version__,
                    "support_url": "https://github.com/",
                },
            }
            topic = f"{self.config.discovery_prefix}/sensor/{slug}/{key}/config"
            self.client.publish(topic, json.dumps(payload), retain=True)

        for command, name in {
            "refresh": "Refresh",
            "full-refresh": "Force full refresh",
            "previous": "Previous screen",
            "next": "Next screen",
            "overview": "Return to overview",
            "clear": "Clear display",
            "sleep": "Sleep now",
            "power-off": "Power off until button wake",
            "restart": "Restart",
        }.items():
            command_slug = command.replace("-", "_")
            button = {
                "name": name,
                "unique_id": f"flexdisplay_{slug}_{command_slug}",
                "command_topic": f"{self.config.topic_prefix}/{device_id}/command/{command}",
                "payload_press": "PRESS",
                "device": device,
                "origin": {
                    "name": "FlexDisplay HA Bridge",
                    "sw_version": __version__,
                    "support_url": "https://github.com/",
                },
            }
            self.client.publish(
                f"{self.config.discovery_prefix}/button/{slug}/{command_slug}/config",
                json.dumps(button),
                retain=True,
            )
        self.client.publish(state_topic, json.dumps(state), retain=True)
