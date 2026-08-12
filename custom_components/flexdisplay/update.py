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
from .entity import FlexDisplayEntity, setup_dynamic_entities


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
        return (
            "install" in pending
            or "install" in dispatched
            or self.record.get("firmware_update_stage")
            in {"downloading", "validating", "flashing", "rebooting"}
        )

    @property
    def update_percentage(self) -> int | None:
        """Expose device-reported OTA progress."""
        if not self.in_progress:
            return None
        try:
            return max(
                0,
                min(100, int(self.record.get("firmware_update_percent") or 0)),
            )
        except (TypeError, ValueError):
            return None

    @property
    def release_summary(self) -> str | None:
        """Explain the guarded rollout state in the update dialog."""
        blockers = self.record.get("firmware_install_blockers") or []
        if blockers:
            return "Update paused: " + "; ".join(str(item) for item in blockers)
        if self.record.get("firmware_update_role") == "canary":
            return "Canary update: the fleet remains blocked until this device boots and acknowledges."
        if self.record.get("firmware_canary_verified"):
            return "Canary verified. This device is eligible for the staged fleet rollout."
        if self.record.get("firmware_update_status") == "failed":
            return (
                "Update failed: "
                + str(self.record.get("firmware_update_error") or "unknown error")
            )
        if self.record.get("firmware_update_role") == "device":
            return "Note4 update: checksum verified, rollback protected, and delivered over your local network."
        return "The first eligible device becomes the USB-powered canary."

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        """Expose rollout and acknowledgement details."""
        return {
            "install_ready": self.record.get("firmware_install_ready", False),
            "install_blockers": self.record.get("firmware_install_blockers") or [],
            "rollout_status": self.record.get("firmware_rollout_status") or "not_started",
            "canary_device_id": self.record.get("firmware_canary_device_id"),
            "canary_verified": self.record.get("firmware_canary_verified", False),
            "update_role": self.record.get("firmware_update_role"),
            "update_status": self.record.get("firmware_update_status"),
            "update_stage": self.record.get("firmware_update_stage"),
            "update_percent": self.record.get("firmware_update_percent"),
            "update_detail": self.record.get("firmware_update_detail"),
            "last_error": self.record.get("firmware_update_error"),
            "last_error_at": self.record.get("firmware_update_error_at"),
            "stage_changed_at": self.record.get("firmware_update_stage_at"),
            "retry_ready": self.record.get("firmware_retry_ready", False),
            "retry_blockers": self.record.get("firmware_retry_blockers") or [],
            "retry_count": self.record.get("firmware_retry_count", 0),
            "retry_limit": self.record.get("firmware_retry_limit", 0),
            "retry_backoff_seconds": self.record.get(
                "firmware_retry_backoff_seconds", 0
            ),
            "command_id": self.record.get("dispatched_command_id")
            or self.record.get("pending_command_id"),
            "last_command_id": self.record.get("last_command_id"),
            "verification_method": self.record.get("firmware_verification_method"),
            "verified_at": self.record.get("firmware_verified_at"),
            "usb_recovery_ready": self.record.get("usb_recovery_verification_ready", False),
            "usb_recovery_blockers": self.record.get("usb_recovery_verification_blockers") or [],
        }

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


class FlexDisplayContentUpdate(FlexDisplayEntity, UpdateEntity):
    """Install the Bridge-assigned content pack independently of firmware."""

    _attr_translation_key = "content"
    _attr_supported_features = UpdateEntityFeature.INSTALL
    _attr_title = "FlexDisplay content"

    def __init__(self, coordinator: FlexDisplayCoordinator, device_id: str) -> None:
        super().__init__(coordinator, device_id)
        self._attr_unique_id = f"{device_id}_content_update"

    @property
    def installed_version(self) -> str | None:
        """Return the acknowledged SD content version."""
        return str(self.record.get("content_pack_version") or "none")

    @property
    def latest_version(self) -> str | None:
        """Return the content version assigned by Fleet Manager."""
        return str(
            self.record.get("content_pack_desired") or self.installed_version or "none"
        )

    @property
    def in_progress(self) -> bool:
        """Return whether the device still needs to apply its assigned pack."""
        return str(self.record.get("content_pack_status") or "") in {
            "scheduled",
            "pending",
            "downloading",
            "installing",
        }

    @property
    def release_summary(self) -> str | None:
        """Explain sleeping-device delivery in the Home Assistant update dialog."""
        if self.record.get("content_pack_error"):
            return f"Content update failed: {self.record['content_pack_error']}"
        if self.record.get("content_pack_status") == "scheduled":
            return "Scheduled by FlexDisplay Content Manager; it will install at the selected time."
        if self.in_progress:
            return "Queued for the next Bridge check-in; waking the display applies it sooner."
        return "Content packs update SD-managed cards and assets without replacing firmware."

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        """Expose content acknowledgement independently of firmware state."""
        return {
            "desired_version": self.record.get("content_pack_desired"),
            "status": self.record.get("content_pack_status"),
            "last_error": self.record.get("content_pack_error"),
        }

    def version_is_newer(self, latest_version: str, installed_version: str) -> bool:
        """Content versions are opaque pack IDs, so any mismatch is actionable."""
        return latest_version != installed_version

    async def async_install(
        self,
        version: str | None,
        backup: bool,
        **kwargs,
    ) -> None:
        """Ask an awake device to check the assigned pack immediately."""
        del version, backup, kwargs
        await self.coordinator.client.command(self.device_id, "refresh")
        await self.coordinator.async_request_refresh()


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create update entities for registered devices."""
    del hass
    def entities_for_device(
        coordinator: FlexDisplayCoordinator, device_id: str
    ) -> tuple[FlexDisplayFirmwareUpdate | FlexDisplayContentUpdate, ...]:
        record = next(
            (item for item in coordinator.data if item.get("device_id") == device_id),
            {},
        )
        model = "".join(
            character
            for character in str(record.get("model") or "").upper()
            if character.isalnum()
        )
        if model in {"ROOK", "ECHOSPOT", "ECHOSPOT2017", "AMAZONECHOSPOT"}:
            return ()
        return (
            FlexDisplayFirmwareUpdate(coordinator, device_id),
            FlexDisplayContentUpdate(coordinator, device_id),
        )

    setup_dynamic_entities(entry, async_add_entities, entities_for_device)
