"""Fleet policy switches for FlexDisplay devices."""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.components.switch import SwitchEntity, SwitchEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import FlexDisplayCoordinator
from .device_capabilities import management_supports, reports_usb_power
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


class FlexDisplayVoiceMute(FlexDisplayEntity, SwitchEntity):
    """Mute the Note4 speaker without losing its selected volume."""

    _attr_translation_key = "voice_mute"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: FlexDisplayCoordinator, device_id: str) -> None:
        super().__init__(coordinator, device_id)
        self._attr_unique_id = f"{device_id}_voice_mute"

    @property
    def is_on(self) -> bool:
        return bool(self.record.get("desired_voice_muted", self.record.get("voice_muted")))

    async def _set(self, enabled: bool) -> None:
        await self.coordinator.client.voice_settings(
            self.device_id, {"muted": enabled}
        )
        await self.coordinator.async_request_refresh()

    async def async_turn_on(self, **kwargs: object) -> None:
        del kwargs
        await self._set(True)

    async def async_turn_off(self, **kwargs: object) -> None:
        del kwargs
        await self._set(False)


def _entities_for_device(
    coordinator: FlexDisplayCoordinator, device_id: str
) -> tuple[SwitchEntity, ...]:
    record = next(
        (item for item in coordinator.data if item.get("device_id") == device_id), {}
    )
    entities: list[SwitchEntity] = [
        FlexDisplayPolicySwitch(coordinator, device_id, description)
        for description in DESCRIPTIONS
        if (
            description.key in {"live_mode", "auto_start"}
            and management_supports(record, "fleet_policy")
        )
        or (
            description.key == "intelligent_sleep"
            and management_supports(record, "sleep_policy")
        )
        or (
            description.key == "stay_awake_on_usb"
            and management_supports(record, "sleep_policy")
            and reports_usb_power(record)
        )
    ]
    if str(record.get("model") or "").upper() in {"N4", "NOTE4", "ZECTRIX_NOTE4"}:
        entities.append(FlexDisplayVoiceMute(coordinator, device_id))
    return tuple(entities)


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
        _entities_for_device,
    )
