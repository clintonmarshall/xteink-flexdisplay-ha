"""Data coordinator for FlexDisplay bridge devices."""

from __future__ import annotations

from datetime import timedelta
import logging

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import FlexDisplayApiClient, FlexDisplayApiError

LOGGER = logging.getLogger(__name__)


class FlexDisplayCoordinator(DataUpdateCoordinator[list[dict]]):
    """Poll the bridge for its registered devices."""

    def __init__(self, hass: HomeAssistant, client: FlexDisplayApiClient) -> None:
        super().__init__(
            hass,
            LOGGER,
            name="FlexDisplay",
            update_interval=timedelta(seconds=60),
        )
        self.client = client

    async def _async_update_data(self) -> list[dict]:
        try:
            return await self.client.devices()
        except FlexDisplayApiError as err:
            raise UpdateFailed(f"Unable to update FlexDisplay devices: {err}") from err
