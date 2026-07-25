"""Command buttons for FlexDisplay devices."""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.components.button import ButtonEntity, ButtonEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import FlexDisplayCoordinator
from .entity import FlexDisplayEntity, setup_dynamic_entities


@dataclass(frozen=True, kw_only=True)
class FlexDisplayButtonDescription(ButtonEntityDescription):
    """Describe a queued FlexDisplay command."""

    command: str


DESCRIPTIONS = (
    FlexDisplayButtonDescription(key="refresh", translation_key="refresh", command="refresh"),
    FlexDisplayButtonDescription(
        key="full_refresh",
        translation_key="full_refresh",
        command="full-refresh",
    ),
    FlexDisplayButtonDescription(key="previous", translation_key="previous", command="previous"),
    FlexDisplayButtonDescription(key="next", translation_key="next", command="next"),
    FlexDisplayButtonDescription(key="overview", translation_key="overview", command="overview"),
    FlexDisplayButtonDescription(key="clear", translation_key="clear", command="clear"),
    FlexDisplayButtonDescription(key="sleep", translation_key="sleep", command="sleep"),
    FlexDisplayButtonDescription(key="power_off", translation_key="power_off", command="power-off"),
    FlexDisplayButtonDescription(key="restart", translation_key="restart", command="restart"),
)


class FlexDisplayCommandButton(FlexDisplayEntity, ButtonEntity):
    """Queue a command for the next device check-in."""

    entity_description: FlexDisplayButtonDescription

    def __init__(
        self,
        coordinator: FlexDisplayCoordinator,
        device_id: str,
        description: FlexDisplayButtonDescription,
    ) -> None:
        super().__init__(coordinator, device_id)
        self.entity_description = description
        self._attr_unique_id = f"{device_id}_{description.key}"

    async def async_press(self) -> None:
        """Queue the described command."""
        await self.coordinator.client.command(self.device_id, self.entity_description.command)
        await self.coordinator.async_request_refresh()


class FlexDisplayCancelCommandsButton(FlexDisplayEntity, ButtonEntity):
    """Cancel commands that have not yet reached the device."""

    _attr_translation_key = "cancel_commands"

    def __init__(self, coordinator: FlexDisplayCoordinator, device_id: str) -> None:
        super().__init__(coordinator, device_id)
        self._attr_unique_id = f"{device_id}_cancel_commands"

    async def async_press(self) -> None:
        """Clear the Bridge's pending command queue."""
        await self.coordinator.client.cancel_commands(self.device_id)
        await self.coordinator.async_request_refresh()


class FlexDisplayVerifyUsbRecoveryButton(FlexDisplayEntity, ButtonEntity):
    """Explicitly reconcile a canary that was recovered and verified over USB."""

    _attr_translation_key = "verify_usb_recovery"

    def __init__(self, coordinator: FlexDisplayCoordinator, device_id: str) -> None:
        super().__init__(coordinator, device_id)
        self._attr_unique_id = f"{device_id}_verify_usb_recovery"

    @property
    def available(self) -> bool:
        """Expose the action only while every Bridge safety condition passes."""
        return super().available and bool(self.record.get("usb_recovery_verification_ready"))

    async def async_press(self) -> None:
        """Record an operator-confirmed USB recovery and release the canary gate."""
        await self.coordinator.client.verify_usb_recovery(
            self.device_id,
            str(self.record.get("firmware_update_target") or ""),
            str(self.record.get("dispatched_command_id") or ""),
        )
        await self.coordinator.async_request_refresh()


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create refresh buttons for registered devices."""
    del hass
    setup_dynamic_entities(
        entry,
        async_add_entities,
        lambda coordinator, device_id: (
            *(
                FlexDisplayCommandButton(coordinator, device_id, description)
                for description in DESCRIPTIONS
            ),
            FlexDisplayCancelCommandsButton(coordinator, device_id),
            FlexDisplayVerifyUsbRecoveryButton(coordinator, device_id),
        ),
    )
