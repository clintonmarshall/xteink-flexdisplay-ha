"""Dashboard page selector for FlexDisplay devices."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import FlexDisplayCoordinator
from .device_capabilities import management_modes, management_supports
from .entity import FlexDisplayEntity, setup_dynamic_entities


class FlexDisplayPageSelect(FlexDisplayEntity, SelectEntity):
    """Select the dashboard page rendered at the next device check-in."""

    _attr_translation_key = "dashboard_page"

    def __init__(self, coordinator: FlexDisplayCoordinator, device_id: str) -> None:
        super().__init__(coordinator, device_id)
        self._attr_unique_id = f"{device_id}_dashboard_page_select"

    @property
    def options(self) -> list[str]:
        """Return the available page titles."""
        return list(self.record.get("dashboard_pages") or ["OVERVIEW"])

    @property
    def current_option(self) -> str | None:
        """Return the selected page title."""
        current = str(self.record.get("dashboard_page_title") or "")
        return current if current in self.options else None

    async def async_select_option(self, option: str) -> None:
        """Queue the selected page for the device."""
        if option not in self.options:
            raise ValueError(f"Unknown FlexDisplay dashboard page: {option}")
        await self.coordinator.client.command(
            self.device_id,
            f"page-{self.options.index(option) + 1}",
        )
        await self.coordinator.async_request_refresh()


class FlexDisplayProfileSelect(FlexDisplayEntity, SelectEntity):
    """Assign the dashboard profile used at the next device check-in."""

    _attr_translation_key = "dashboard_profile"

    def __init__(self, coordinator: FlexDisplayCoordinator, device_id: str) -> None:
        super().__init__(coordinator, device_id)
        self._attr_unique_id = f"{device_id}_dashboard_profile_select"

    @property
    def options(self) -> list[str]:
        return list(self.record.get("available_profiles") or ["default"])

    @property
    def current_option(self) -> str | None:
        current = str(self.record.get("assigned_profile") or "")
        return current if current in self.options else None

    async def async_select_option(self, option: str) -> None:
        if option not in self.options:
            raise ValueError(f"Unknown FlexDisplay profile: {option}")
        await self.coordinator.client.provision(self.device_id, {"profile": option})
        await self.coordinator.async_request_refresh()


class FlexDisplayModeSelect(FlexDisplayEntity, SelectEntity):
    """Assign the mode applied at the next Home Assistant check-in."""

    _attr_translation_key = "assigned_mode"

    def __init__(self, coordinator: FlexDisplayCoordinator, device_id: str) -> None:
        super().__init__(coordinator, device_id)
        self._attr_unique_id = f"{device_id}_assigned_mode_select"

    @property
    def options(self) -> list[str]:
        return list(self.record.get("available_modes") or ["home_assistant"])

    @property
    def current_option(self) -> str | None:
        current = str(self.record.get("assigned_mode") or "")
        return current if current in self.options else None

    async def async_select_option(self, option: str) -> None:
        if option not in self.options:
            raise ValueError(f"Unknown FlexDisplay mode: {option}")
        await self.coordinator.client.provision(self.device_id, {"mode": option})
        await self.coordinator.async_request_refresh()


class FlexDisplayPolicySelect(FlexDisplayEntity, SelectEntity):
    """Apply a named operating policy to this display."""

    _attr_translation_key = "fleet_policy"

    def __init__(self, coordinator: FlexDisplayCoordinator, device_id: str) -> None:
        super().__init__(coordinator, device_id)
        self._attr_unique_id = f"{device_id}_fleet_policy_select"

    @property
    def options(self) -> list[str]:
        return list(
            self.record.get("available_policy_profiles")
            or ["battery_saver", "balanced", "usb_kiosk"]
        )

    @property
    def current_option(self) -> str | None:
        current = str(self.record.get("assigned_policy_name") or "")
        return current if current in self.options else None

    async def async_select_option(self, option: str) -> None:
        if option not in self.options:
            raise ValueError(f"Unknown FlexDisplay fleet policy: {option}")
        await self.coordinator.client.apply_policy(
            option,
            scope="devices",
            device_ids=[self.device_id],
            delivery="apply_now",
        )
        await self.coordinator.async_request_refresh()


class FlexDisplayRenderingProfileSelect(FlexDisplayEntity, SelectEntity):
    """Choose the panel refresh/rendering strategy for this display."""

    _attr_translation_key = "rendering_profile"
    _attr_options = ["standard", "photo"]

    def __init__(self, coordinator: FlexDisplayCoordinator, device_id: str) -> None:
        super().__init__(coordinator, device_id)
        self._attr_unique_id = f"{device_id}_rendering_profile_select"

    @property
    def current_option(self) -> str | None:
        current = str(self.record.get("assigned_rendering_profile") or "standard")
        return current if current in self.options else "standard"

    async def async_select_option(self, option: str) -> None:
        if option not in self.options:
            raise ValueError(f"Unknown FlexDisplay rendering profile: {option}")
        await self.coordinator.client.provision(
            self.device_id,
            {"rendering_profile": option},
        )
        await self.coordinator.async_request_refresh()


class FlexDisplayOpenDisplayTransportSelect(FlexDisplayEntity, SelectEntity):
    """Choose the memory-safe OpenDisplay receiver transport policy."""

    _attr_translation_key = "open_display_transport"
    _attr_options = ["auto", "lan_preferred", "ble_only"]

    def __init__(self, coordinator: FlexDisplayCoordinator, device_id: str) -> None:
        super().__init__(coordinator, device_id)
        self._attr_unique_id = f"{device_id}_open_display_transport_select"

    @property
    def current_option(self) -> str | None:
        current = str(
            self.record.get("assigned_open_display_transport_policy") or "auto"
        )
        return current if current in self.options else "auto"

    async def async_select_option(self, option: str) -> None:
        if option not in self.options:
            raise ValueError(f"Unknown OpenDisplay transport policy: {option}")
        await self.coordinator.client.provision(
            self.device_id,
            {"open_display_transport_policy": option},
        )
        await self.coordinator.async_request_refresh()


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create a page selector for each registered device."""
    del hass

    def entities_for_device(
        coordinator: FlexDisplayCoordinator, device_id: str
    ) -> tuple[SelectEntity, ...]:
        record = next(
            (item for item in coordinator.data if item.get("device_id") == device_id),
            {},
        )
        entities: list[SelectEntity] = []
        if management_supports(record, "page_selection"):
            entities.append(FlexDisplayPageSelect(coordinator, device_id))
        if management_supports(record, "dashboard_profiles"):
            entities.append(FlexDisplayProfileSelect(coordinator, device_id))
        if management_modes(record):
            entities.append(FlexDisplayModeSelect(coordinator, device_id))
        if management_supports(record, "fleet_policy"):
            entities.append(FlexDisplayPolicySelect(coordinator, device_id))
        if management_supports(record, "rendering_profile"):
            entities.append(FlexDisplayRenderingProfileSelect(coordinator, device_id))
        if management_supports(record, "opendisplay_policy"):
            entities.append(
                FlexDisplayOpenDisplayTransportSelect(coordinator, device_id)
            )
        return tuple(entities)

    setup_dynamic_entities(
        entry,
        async_add_entities,
        entities_for_device,
    )
