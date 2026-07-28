"""Dashboard page selector for FlexDisplay devices."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import FlexDisplayCoordinator
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


class FlexDisplayRotationSelect(FlexDisplayEntity, SelectEntity):
    """Select the persistent physical mounting orientation."""

    _attr_translation_key = "display_rotation"
    _attr_options = ["0°", "180°"]

    def __init__(self, coordinator: FlexDisplayCoordinator, device_id: str) -> None:
        super().__init__(coordinator, device_id)
        self._attr_unique_id = f"{device_id}_display_rotation_select"

    @property
    def current_option(self) -> str:
        return "180°" if int(self.record.get("rotation_degrees") or 0) == 180 else "0°"

    async def async_select_option(self, option: str) -> None:
        if option not in self.options:
            raise ValueError(f"Unsupported FlexDisplay orientation: {option}")
        await self.coordinator.client.command(
            self.device_id,
            f"rotate-{option.removesuffix('°')}",
        )
        await self.coordinator.async_request_refresh()


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create a page selector for each registered device."""
    del hass
    setup_dynamic_entities(
        entry,
        async_add_entities,
        lambda coordinator, device_id: (
            FlexDisplayPageSelect(coordinator, device_id),
            FlexDisplayProfileSelect(coordinator, device_id),
            FlexDisplayModeSelect(coordinator, device_id),
            FlexDisplayRotationSelect(coordinator, device_id),
        ),
    )
