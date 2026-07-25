"""Base entity for FlexDisplay devices."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import FlexDisplayCoordinator


class FlexDisplayEntity(CoordinatorEntity[FlexDisplayCoordinator]):
    """Entity linked to one bridge device."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: FlexDisplayCoordinator, device_id: str) -> None:
        super().__init__(coordinator)
        self.device_id = device_id

    @property
    def record(self) -> dict:
        """Return the latest record for this device."""
        for record in self.coordinator.data:
            if record.get("device_id") == self.device_id:
                return record
        return {}

    @property
    def device_info(self) -> DeviceInfo:
        """Describe this FlexDisplay device."""
        record = self.record
        return DeviceInfo(
            identifiers={(DOMAIN, self.device_id)},
            manufacturer="XTEINK / FlexDisplay",
            model=str(record.get("model") or "XTEINK"),
            name=str(record.get("name") or self.device_id),
            serial_number=self.device_id,
            suggested_area=str(record.get("area") or "") or None,
            sw_version=str(record.get("firmware") or "unknown"),
        )
