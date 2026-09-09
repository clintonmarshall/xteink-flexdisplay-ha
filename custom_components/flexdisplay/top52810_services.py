"""Explicit preview/confirmation actions; never send on discovery or setup."""

import voluptuous as vol
from homeassistant.core import SupportsResponse
from homeassistant.exceptions import ServiceValidationError
import homeassistant.helpers.config_validation as cv

from .api import FlexDisplayApiError
from .const import DOMAIN


def register_image_services(hass, resolve):
    fields = {
        vol.Required("image_base64"): vol.All(cv.string, vol.Length(min=1, max=174764)),
        vol.Optional("config_entry_id"): cv.string,
    }

    async def preview(call):
        try:
            return await resolve(hass, call).client.preview_top52810_image(call.data["image_base64"])
        except FlexDisplayApiError as err:
            raise ServiceValidationError(str(err)) from err

    async def send(call):
        try:
            return await resolve(hass, call).client.send_top52810_image(
                call.data["image_base64"], call.data["expected_plan_sha256"],
            )
        except FlexDisplayApiError as err:
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
