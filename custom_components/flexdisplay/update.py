"""Queued firmware updates for FlexDisplay devices."""

from __future__ import annotations

import re

from homeassistant.components.update import (
    UpdateDeviceClass,
    UpdateEntity,
    UpdateEntityFeature,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import FlexDisplayCoordinator
from .entity import FlexDisplayEntity


class FlexDisplayFirmwareUpdate(FlexDisplayEntity, UpdateEntity):
    """Install firmware during the device's next Bridge check-in."""

    _attr_translation_key = "firmware"
    _attr_device_class = UpdateDeviceClass.FIRMWARE
    _attr_supported_features = UpdateEntityFeature.INSTALL
    _attr_title = "FlexDisplay firmware"

    def __init__(self, coordinator: FlexDisplayCoordinator, device_id: str) -> None:
        super().__init__(coordinator, device_id)
        self._attr_unique_id = f"{device_id}_firmware_update"

    @property
    def installed_version(self) -> str | None:
        """Return the running firmware."""
        return self.record.get("firmware")

    @property
    def latest_version(self) -> str | None:
        """Return the Bridge-configured release."""
        return self.record.get("latest_firmware") or self.installed_version

    @property
    def in_progress(self) -> bool:
        """Return whether an install is queued or dispatched."""
        pending = self.record.get("pending_commands") or []
        dispatched = self.record.get("dispatched_commands") or []
        return "install" in pending or "install" in dispatched

    def version_is_newer(self, latest_version: str, installed_version: str) -> bool:
        """Compare the FlexDisplay suffix instead of the CrossPoint base version."""

        def version(value: str) -> tuple[int, int, int]:
            match = re.search(r"flexdisplay[.-](\d+)\.(\d+)\.(\d+)", value)
            if not match:
                match = re.search(r"(\d+)\.(\d+)\.(\d+)", value)
            return tuple(int(part) for part in match.groups()) if match else (0, 0, 0)

        return version(latest_version) > version(installed_version)

    async def async_install(
        self,
        version: str | None,
        backup: bool,
        **kwargs,
    ) -> None:
        """Queue the configured release for the next check-in."""
        del version, backup, kwargs
        await self.coordinator.client.command(self.device_id, "install")
        await self.coordinator.async_request_refresh()


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create update entities for registered devices."""
    del hass
    coordinator: FlexDisplayCoordinator = entry.runtime_data
    async_add_entities(
        FlexDisplayFirmwareUpdate(coordinator, record["device_id"])
        for record in coordinator.data
        if record.get("device_id")
    )
