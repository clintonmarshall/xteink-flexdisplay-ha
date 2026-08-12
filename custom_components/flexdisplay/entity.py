"""Base entity for FlexDisplay devices."""

from __future__ import annotations

from collections.abc import Callable, Iterable

from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import FlexDisplayCoordinator
from .device_capabilities import device_manufacturer, device_model_label
from .entity_lifecycle import collect_new_entities

EntityFactory = Callable[[FlexDisplayCoordinator, str], Iterable[Entity]]
FlexHubEntityFactory = Callable[[FlexDisplayCoordinator], Iterable[Entity]]


def setup_dynamic_entities(
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
    factory: EntityFactory,
) -> None:
    """Add entities now and whenever a new fleet device checks in."""
    coordinator: FlexDisplayCoordinator = entry.runtime_data
    known_entity_ids: set[str] = set()

    def add_new_devices() -> None:
        entities = collect_new_entities(
            coordinator,
            coordinator.data or [],
            factory,
            known_entity_ids,
        )
        if entities:
            async_add_entities(entities)

    add_new_devices()
    entry.async_on_unload(coordinator.async_add_listener(add_new_devices))


def setup_flexhub_entities(
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
    factory: FlexHubEntityFactory,
) -> None:
    """Add optional FlexHub entities once a hub is configured on this Bridge."""
    coordinator: FlexDisplayCoordinator = entry.runtime_data
    added = False

    def add_when_configured() -> None:
        nonlocal added
        if added or not coordinator.flexhub_summary.get("configured"):
            return
        added = True
        async_add_entities(factory(coordinator))

    add_when_configured()
    entry.async_on_unload(coordinator.async_add_listener(add_when_configured))


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

    def _record_supported(self, record: dict) -> bool:
        """Return whether this entity still applies to the current identity."""
        return True

    @property
    def available(self) -> bool:
        """Hide stale controls immediately after a capability correction."""
        record = self.record
        return super().available and bool(record) and self._record_supported(record)

    @property
    def device_info(self) -> DeviceInfo:
        """Describe this FlexDisplay device."""
        record = self.record
        return DeviceInfo(
            identifiers={(DOMAIN, self.device_id)},
            manufacturer=device_manufacturer(record),
            model=device_model_label(record),
            name=str(record.get("name") or self.device_id),
            serial_number=self.device_id,
            suggested_area=str(record.get("area") or "") or None,
            sw_version=str(record.get("firmware") or "unknown"),
        )


class FlexHubEntity(CoordinatorEntity[FlexDisplayCoordinator]):
    """Entity linked to the optional SenseCAP FlexHub."""

    _attr_has_entity_name = True

    @property
    def available(self) -> bool:
        """Report availability without affecting the X3/X4 coordinator."""
        return super().available and bool(
            self.coordinator.flexhub_summary.get("connected")
        )

    @property
    def device_info(self) -> DeviceInfo:
        """Describe the SenseCAP gateway as its own Home Assistant device."""
        summary = self.coordinator.flexhub_summary
        status = (
            summary.get("status") if isinstance(summary.get("status"), dict) else {}
        )
        return DeviceInfo(
            identifiers={(DOMAIN, self.coordinator.flexhub_id)},
            manufacturer="Seeed Studio / FlexDisplay",
            model="SenseCAP Indicator FlexHub",
            name="SenseCAP FlexHub",
            serial_number=str(
                (status.get("meshtastic") or {}).get("node_id") or "flexhub"
            ),
            sw_version=str(
                status.get("platform_version") or status.get("firmware") or "unknown"
            ),
            configuration_url=str(summary.get("url") or "") or None,
        )
