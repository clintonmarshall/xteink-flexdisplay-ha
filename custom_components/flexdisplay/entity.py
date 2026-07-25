"""Base entity for FlexDisplay devices."""

from __future__ import annotations

from collections.abc import Callable, Iterable

from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import FlexDisplayCoordinator

EntityFactory = Callable[[FlexDisplayCoordinator, str], Iterable[Entity]]


def setup_dynamic_entities(
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
    factory: EntityFactory,
) -> None:
    """Add entities now and whenever a new fleet device checks in."""
    coordinator: FlexDisplayCoordinator = entry.runtime_data
    known_device_ids: set[str] = set()

    def add_new_devices() -> None:
        entities: list[Entity] = []
        for record in coordinator.data or []:
            device_id = str(record.get("device_id") or "")
            if not device_id or device_id in known_device_ids:
                continue
            known_device_ids.add(device_id)
            entities.extend(factory(coordinator, device_id))
        if entities:
            async_add_entities(entities)

    add_new_devices()
    entry.async_on_unload(coordinator.async_add_listener(add_new_devices))


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
