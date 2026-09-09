"""Explicit preview/confirmation actions; never send on discovery or setup."""

import voluptuous as vol
from homeassistant.core import SupportsResponse
from homeassistant.exceptions import ServiceValidationError
import homeassistant.helpers.config_validation as cv

from .api import FlexDisplayApiError
from .const import DOMAIN
from .top52810_media import read_media_image


def register_image_services(hass, resolve):
    fields = {
        vol.Exclusive("image_base64", "image"): vol.All(cv.string, vol.Length(min=1, max=174764)),
        vol.Exclusive("image_file", "image"): cv.string,
        vol.Optional("resize_mode", default="fit"): vol.In({"fit", "crop"}),
        vol.Optional("config_entry_id"): cv.string,
    }

    async def image_input(call):
        client = resolve(hass, call).client
        if "image_file" in call.data:
            raw = await hass.async_add_executor_job(read_media_image, call.data["image_file"])
            prepared = await client.prepare_top52810_image(raw, call.data["resize_mode"])
            return client, prepared["image_base64"], prepared
        if "image_base64" not in call.data:
            raise ValueError("Choose image_file or image_base64")
        return client, call.data["image_base64"], None

    async def preview(call):
        try:
            client, image, prepared = await image_input(call)
            return prepared if prepared is not None else await client.preview_top52810_image(image)
        except (FlexDisplayApiError, OSError, ValueError) as err:
            raise ServiceValidationError(str(err)) from err

    async def send(call):
        try:
            client, image, _ = await image_input(call)
            return await client.send_top52810_image(
                image, call.data["expected_plan_sha256"],
            )
        except (FlexDisplayApiError, OSError, ValueError) as err:
            raise ServiceValidationError(str(err)) from err

    if not hass.services.has_service(DOMAIN, "preview_top52810_image"):
        hass.services.async_register(
            DOMAIN, "preview_top52810_image", preview, schema=vol.Schema(fields),
            supports_response=SupportsResponse.ONLY,
        )
    if not hass.services.has_service(DOMAIN, "send_top52810_image"):
        hass.services.async_register(
            DOMAIN, "send_top52810_image", send,
            schema=vol.Schema({**fields, vol.Required("expected_plan_sha256"):
                              vol.All(cv.string, vol.Match(r"^[0-9a-f]{64}$"))}),
            supports_response=SupportsResponse.OPTIONAL,
        )
