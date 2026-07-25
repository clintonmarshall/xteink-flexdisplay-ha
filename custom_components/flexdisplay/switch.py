"""Fleet policy switches for FlexDisplay devices."""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.components.switch import SwitchEntity, SwitchEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import FlexDisplayCoordinator
from .entity import FlexDisplayEntity, setup_dynamic_entities


@dataclass(frozen=True, kw_only=True)
class FlexDisplaySwitchDescription(SwitchEntityDescription):
    """Describe a boolean provisioning field."""

    field: str
    record_key: str


DESCRIPTIONS = (
    FlexDisplaySwitchDescription(
        key="live_mode",
        translation_key="live_mode",
        field="live_mode",
        record_key="assigned_live_mode",
    ),
    FlexDisplaySwitchDescription(
        key="auto_start",
        translation_key="auto_start",
        field="auto_start",
        record_key="assigned_auto_start",
    ),
    FlexDisplaySwitchDescription(
        key="intelligent_sleep",
        translation_key="intelligent_sleep",
        field="intelligent_sleep",
        record_key="assigned_intelligent_sleep",
    ),
    FlexDisplaySwitchDescription(
        key="stay_awake_on_usb",
        translation_key="stay_awake_on_usb",
        field="stay_awake_on_usb",
        record_key="assigned_stay_awake_on_usb",
    ),
)


class FlexDisplayPolicySwitch(FlexDisplayEntity, SwitchEntity):
    """Edit one Bridge-backed boolean policy."""

    _attr_entity_category = EntityCategory.CONFIG
    entity_description: FlexDisplaySwitchDescription

    def __init__(
        self,
        coordinator: FlexDisplayCoordinator,
        device_id: str,
        description: FlexDisplaySwitchDescription,
    ) -> None:
        super().__init__(coordinator, device_id)
        self.entity_description = description
        self._attr_unique_id = f"{device_id}_{description.key}"

    @property
    def is_on(self) -> bool:
        """Return the assigned policy state."""
        return bool(self.record.get(self.entity_description.record_key))

    async def _set(self, enabled: bool) -> None:
        await self.coordinator.client.provision(
            self.device_id,
            {self.entity_description.field: enabled},
        )
        await self.coordinator.async_request_refresh()

    async def async_turn_on(self, **kwargs: object) -> None:
        """Enable the policy."""
        del kwargs
        await self._set(True)

    async def async_turn_off(self, **kwargs: object) -> None:
        """Disable the policy."""
        del kwargs
        await self._set(False)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create policy switches for registered devices."""
    del hass
    setup_dynamic_entities(
        entry,
        async_add_entities,
        lambda coordinator, device_id: (
            FlexDisplayPolicySwitch(coordinator, device_id, description)
            for description in DESCRIPTIONS
        ),
    )
