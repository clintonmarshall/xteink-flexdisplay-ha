"""Separate, experimental HA entities for the admitted stock-BLE canary.

Never feed these records into generic receiver/firmware entity factories.
"""

from datetime import timedelta
import logging
from time import monotonic

from homeassistant.components import bluetooth
from homeassistant.components.button import ButtonEntity
from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import (
    CoordinatorEntity, DataUpdateCoordinator, UpdateFailed,
)
from homeassistant.util.dt import parse_datetime, utcnow

from .api import FlexDisplayApiError
from .const import DOMAIN

LOGGER = logging.getLogger(__name__)
ADDRESS = "DF:84:6B:DE:F6:ED"
NAME = "TRSEPD_F6ED"
MANUFACTURER_ID = 0x1A28
MANUFACTURER_PAYLOAD = "ffffff00000d"


def admitted(record):
    """Do not turn this one-unit canary into automatic family enrollment."""
    return (
        record.get("address") == ADDRESS
        and record.get("expected_name") == NAME
        and record.get("manufacturer_id") == MANUFACTURER_ID
        and record.get("manufacturer_payload_hex") == MANUFACTURER_PAYLOAD
    )


class Top52810Coordinator(DataUpdateCoordinator):
    """Poll job state independently of the general receiver inventory."""

    def __init__(self, hass, client, entry_id):
        super().__init__(hass, LOGGER, name="FlexDisplay stock tag",
                         update_interval=timedelta(seconds=5))
        self.client = client
        self.entry_id = entry_id
        self._last_seen = None
        self._observation_time = None

    async def _async_update_data(self):
        try:
            records = await self.client.top52810_devices()
        except FlexDisplayApiError as err:
            raise UpdateFailed(str(err)) from err
        record = next((dict(item) for item in records if admitted(item)), None)
        if record is None:
            return {}
        info = bluetooth.async_last_service_info(self.hass, ADDRESS, connectable=True)
        fresh = False
        if (info is not None and info.address == ADDRESS and info.name == NAME
                and info.manufacturer_data.get(MANUFACTURER_ID) == bytes.fromhex(MANUFACTURER_PAYLOAD)):
            age = monotonic() - info.time
            fresh = 0 <= age <= 10
            if age >= 0 and info.time != self._observation_time:
                self._observation_time = info.time
                self._last_seen = utcnow() - timedelta(seconds=age)
        record["connection"] = "advertising" if fresh else "waiting_for_window"
        record["last_seen"] = self._last_seen
        return record


class Top52810Entity(CoordinatorEntity):
    _attr_has_entity_name = True

    def __init__(self, coordinator, key, name):
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.entry_id}_top52810_{ADDRESS.replace(':', '')}_{key}"
        self._attr_name = name
        self.key = key
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{coordinator.entry_id}_top52810_{ADDRESS}")},
            name="TOP52810 F6ED (experimental)",
            model="TOP52810M-D01 / MS136F6 V1.0",
            sw_version="Stock firmware",
        )

    @property
    def available(self):
        # Sleeping is normal; API failure, removal or identity mismatch is not.
        return super().available and bool(self.coordinator.data)

    @property
    def extra_state_attributes(self):
        record = self.coordinator.data or {}
        return {"address": ADDRESS, "experimental": True,
                "physical_image_verified": False,
                "job_id": record.get("job_id"),
                "attempt_count": record.get("attempt_count")}


class Top52810Sensor(Top52810Entity, SensorEntity):
    def __init__(self, coordinator, key, name):
        super().__init__(coordinator, key, name)
        if key in {"last_seen", "last_refresh_ack"}:
            self._attr_device_class = SensorDeviceClass.TIMESTAMP

    @property
    def native_value(self):
        value = (self.coordinator.data or {}).get(self.key)
        if self.key == "last_refresh_ack" and value:
            return parse_datetime(value)
        return value


class Top52810SendButton(Top52810Entity, ButtonEntity):
    def __init__(self, coordinator):
        super().__init__(coordinator, "send_diagnostic", "Send diagnostic image")
        self._sending = False

    @property
    def available(self):
        return (super().available and not self._sending
                and not self.coordinator.data.get("active"))

    async def async_press(self):
        # HA service calls may still invoke unavailable entities: guard here too.
        if not self.available:
            raise HomeAssistantError("Tag unavailable or a transfer is already pending")
        self._sending = True
        try:
            await self.coordinator.client.send_top52810_diagnostic(ADDRESS, NAME)
        except FlexDisplayApiError as err:
            raise HomeAssistantError(str(err)) from err
        finally:
            self._sending = False
            await self.coordinator.async_request_refresh()


def setup_top52810_entities(entry, async_add_entities, *, buttons=False):
    """Keep listener alive even when a legacy Bridge lacks the status endpoint."""
    coordinator = entry.runtime_data.top52810
    added = False

    def add_tag():
        nonlocal added
        if added or not coordinator.data:
            return
        added = True
        if buttons:
            async_add_entities([Top52810SendButton(coordinator)])
        else:
            async_add_entities([
                Top52810Sensor(coordinator, key, name) for key, name in (
                    ("connection", "Bluetooth window"),
                    ("last_seen", "Last seen"),
                    ("status", "Delivery status"),
                    ("last_refresh_ack", "Last refresh acknowledgement"),
                )
            ])

    add_tag()
    entry.async_on_unload(coordinator.async_add_listener(add_tag))
