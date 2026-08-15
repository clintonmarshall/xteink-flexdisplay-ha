"""Speaker controls for audio-capable FlexDisplay receivers."""

from __future__ import annotations

from homeassistant.components.media_player import (
    MediaPlayerDeviceClass,
    MediaPlayerEntity,
    MediaPlayerEntityFeature,
    MediaPlayerState,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import FlexDisplayCoordinator
from .device_capabilities import management_supports
from .entity import FlexDisplayEntity, setup_dynamic_entities


class FlexDisplaySpeaker(FlexDisplayEntity, MediaPlayerEntity):
    """Represent volume/mute controls without advertising unsupported playback."""

    _attr_translation_key = "speaker"
    _attr_device_class = MediaPlayerDeviceClass.SPEAKER
    _attr_supported_features = (
        MediaPlayerEntityFeature.VOLUME_SET
        | MediaPlayerEntityFeature.VOLUME_MUTE
        | MediaPlayerEntityFeature.VOLUME_STEP
    )
    _attr_volume_step = 0.05

    def __init__(self, coordinator: FlexDisplayCoordinator, device_id: str) -> None:
        super().__init__(coordinator, device_id)
        self._attr_unique_id = f"{device_id}_speaker"

    def _record_supported(self, record: dict) -> bool:
        return management_supports(record, "audio")

    @property
    def state(self) -> MediaPlayerState:
        """Report an inactive phone as off and a connected receiver as idle."""
        return MediaPlayerState.IDLE if self.record.get("online") else MediaPlayerState.OFF

    @property
    def volume_level(self) -> float | None:
        """Return desired volume while waiting for the next phone check-in."""
        value = self.record.get("desired_voice_volume", self.record.get("voice_volume"))
        return max(0.0, min(1.0, float(value) / 100)) if value is not None else None

    @property
    def is_volume_muted(self) -> bool | None:
        """Return the desired mute state."""
        value = self.record.get("desired_voice_muted", self.record.get("voice_muted"))
        return bool(value) if value is not None else None

    async def async_set_volume_level(self, volume: float) -> None:
        """Set speaker volume from Home Assistant's normalized 0..1 range."""
        await self.coordinator.client.voice_settings(
            self.device_id, {"volume": round(max(0.0, min(1.0, volume)) * 100)}
        )
        await self.coordinator.async_request_refresh()

    async def async_mute_volume(self, mute: bool) -> None:
        """Set the receiver mute state."""
        await self.coordinator.client.voice_settings(
            self.device_id, {"muted": mute}
        )
        await self.coordinator.async_request_refresh()

    async def async_volume_up(self) -> None:
        """Increase volume by the advertised five-percent step."""
        await self.async_set_volume_level((self.volume_level or 0.0) + 0.05)

    async def async_volume_down(self) -> None:
        """Decrease volume by the advertised five-percent step."""
        await self.async_set_volume_level((self.volume_level or 0.0) - 0.05)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create speaker entities only for trusted audio-capable receivers."""
    del hass

    def entities_for_device(
        coordinator: FlexDisplayCoordinator, device_id: str
    ) -> tuple[FlexDisplaySpeaker, ...]:
        record = next(
            (item for item in coordinator.data if item.get("device_id") == device_id),
            {},
        )
        if not management_supports(record, "audio"):
            return ()
        return (FlexDisplaySpeaker(coordinator, device_id),)

    setup_dynamic_entities(entry, async_add_entities, entities_for_device)
