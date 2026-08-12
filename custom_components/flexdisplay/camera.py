"""Privacy-preserving cached snapshots from Android companion cameras."""

from __future__ import annotations

from homeassistant.components.camera import Camera
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .api import FlexDisplayApiError
from .coordinator import FlexDisplayCoordinator
from .device_capabilities import management_supports
from .entity import FlexDisplayEntity, setup_dynamic_entities


class FlexDisplayCamera(FlexDisplayEntity, Camera):
    """Expose only the last explicitly requested snapshot, never a live stream."""

    _attr_translation_key = "camera_snapshot"
    _attr_content_type = "image/jpeg"

    def __init__(self, coordinator: FlexDisplayCoordinator, device_id: str) -> None:
        FlexDisplayEntity.__init__(self, coordinator, device_id)
        Camera.__init__(self)
        self._attr_unique_id = f"{device_id}_camera_snapshot"

    def _record_supported(self, record: dict) -> bool:
        return management_supports(record, "camera")

    @property
    def available(self) -> bool:
        """Keep the cached image usable while an on-demand phone is inactive."""
        return (
            super().available
            and bool(self.record.get("camera_snapshot_at"))
        )

    async def async_camera_image(
        self, width: int | None = None, height: int | None = None
    ) -> bytes | None:
        """Return cached JPEG bytes without causing a device capture."""
        del width, height
        try:
            return await self.coordinator.client.camera_snapshot(self.device_id)
        except FlexDisplayApiError:
            return None


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create cameras only for trusted camera-capable receivers."""
    del hass

    def entities_for_device(
        coordinator: FlexDisplayCoordinator, device_id: str
    ) -> tuple[FlexDisplayCamera, ...]:
        record = next(
            (item for item in coordinator.data if item.get("device_id") == device_id),
            {},
        )
        if not management_supports(record, "camera"):
            return ()
        return (FlexDisplayCamera(coordinator, device_id),)

    setup_dynamic_entities(entry, async_add_entities, entities_for_device)
