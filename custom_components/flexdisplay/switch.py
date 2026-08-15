"""Fleet policy switches for FlexDisplay devices."""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.components.switch import SwitchEntity, SwitchEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import FlexDisplayCoordinator
from .device_capabilities import (
    desired_microphone_enabled,
    management_supports,
    reports_usb_power,
    supports_audio,
    supports_frontlight,
)
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

    def _record_supported(self, record: dict) -> bool:
        key = self.entity_description.key
        if key in {"live_mode", "auto_start"}:
            return management_supports(record, "fleet_policy")
        if key == "intelligent_sleep":
            return management_supports(record, "sleep_policy")
        if key == "stay_awake_on_usb":
            return management_supports(
                record, "sleep_policy"
            ) and reports_usb_power(record)
        return False

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
    """Mute receiver speaker without losing its selected volume."""

    _attr_translation_key = "voice_mute"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: FlexDisplayCoordinator, device_id: str) -> None:
        super().__init__(coordinator, device_id)
        self._attr_unique_id = f"{device_id}_voice_mute"

    def _record_supported(self, record: dict) -> bool:
        return supports_audio(record)

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


class FlexDisplayFrontlightSwitch(FlexDisplayEntity, SwitchEntity):
    """Control the admitted X4 Pro frontlight power state."""

    _attr_translation_key = "frontlight"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: FlexDisplayCoordinator, device_id: str) -> None:
        super().__init__(coordinator, device_id)
        self._attr_unique_id = f"{device_id}_frontlight"

    def _record_supported(self, record: dict) -> bool:
        return supports_frontlight(record, "on")

    @property
    def is_on(self) -> bool:
        return bool(
            self.record.get(
                "desired_frontlight_on",
                self.record.get("frontlight_on"),
            )
        )

    async def _set(self, enabled: bool) -> None:
        await self.coordinator.client.display_settings(
            self.device_id,
            {"frontlight_on": enabled},
        )
        await self.coordinator.async_request_refresh()

    async def async_turn_on(self, **kwargs: object) -> None:
        del kwargs
        await self._set(True)

    async def async_turn_off(self, **kwargs: object) -> None:
        del kwargs
        await self._set(False)


class FlexDisplayMicrophoneEnabled(FlexDisplayEntity, SwitchEntity):
    """Allow local push-to-talk Assist without starting remote recording."""

    _attr_translation_key = "microphone_enabled"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: FlexDisplayCoordinator, device_id: str) -> None:
        super().__init__(coordinator, device_id)
        self._attr_unique_id = f"{device_id}_microphone_enabled"

    def _record_supported(self, record: dict) -> bool:
        return management_supports(record, "microphone")

    @property
    def is_on(self) -> bool:
        """Return whether local Assist recording is permitted."""
        return desired_microphone_enabled(self.record)

    async def _set(self, enabled: bool) -> None:
        await self.coordinator.client.voice_settings(
            self.device_id, {"microphone_enabled": enabled}
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
    if supports_audio(record):
        entities.append(FlexDisplayVoiceMute(coordinator, device_id))
    if supports_frontlight(record, "on"):
        entities.append(FlexDisplayFrontlightSwitch(coordinator, device_id))
    if management_supports(record, "microphone"):
        entities.append(FlexDisplayMicrophoneEnabled(coordinator, device_id))
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
