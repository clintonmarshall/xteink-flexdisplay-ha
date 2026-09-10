"""Explicit preview/confirmation actions; never send on discovery or setup."""

import voluptuous as vol
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import SupportsResponse
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import device_registry as dr
import homeassistant.helpers.config_validation as cv

from .api import FlexDisplayApiError
from .const import DOMAIN
from .top52810_media import async_image_path, read_media_image


IMAGE_SELECTION = vol.Any(
    str,  # Existing image_file YAML remains supported.
    vol.Schema({
        vol.Required("media_content_id"): str,
        vol.Required("media_content_type"): vol.In({"image/png", "image/jpeg"}),
        vol.Remove("metadata"): dict,
    }),
)


def _client_for_device(hass, device_id):
    """Resolve one loaded, admitted tag; never default to another Bridge."""
    from .top52810_entity import ADDRESS, admitted

    device = dr.async_get(hass).async_get(device_id)
    if device is None or device.disabled_by is not None:
        raise ServiceValidationError("Select an enabled FlexDisplay display")
    matches = []
    for entry_id in device.config_entries:
        if (DOMAIN, f"{entry_id}_top52810_{ADDRESS}") not in device.identifiers:
            continue
        entry = hass.config_entries.async_get_entry(entry_id)
        runtime = getattr(entry, "runtime_data", None)
        tag = getattr(runtime, "top52810", None)
        if (entry is not None and entry.domain == DOMAIN
                and entry.state is ConfigEntryState.LOADED and tag is not None
                and tag.last_update_success and admitted(tag.data or {})):
            matches.append(tag.client)
    if len(matches) != 1:
        raise ServiceValidationError(
            "Select one available TOP52810 F6ED display on a loaded FlexDisplay Bridge"
        )
    return matches[0]


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

    async def send_image(call):
        try:
            client = _client_for_device(hass, call.data["device_id"])
            path = await async_image_path(hass, call.data["image_file"])
            raw = await hass.async_add_executor_job(read_media_image, path)
            prepared = await client.prepare_top52810_image(raw, call.data["resize_mode"])
            image = prepared.get("image_base64")
            plan = prepared.get("plan_sha256")
            if (not isinstance(image, str) or not image or not isinstance(plan, str)
                    or len(plan) != 64 or any(c not in "0123456789abcdef" for c in plan)
                    or prepared.get("write_count") != 44
                    or prepared.get("device_io") is not False):
                raise ValueError("Bridge returned an invalid image preparation plan")
            # Recheck the selection after conversion, before queuing the write.
            if _client_for_device(hass, call.data["device_id"]) is not client:
                raise ValueError("Selected display changed while preparing the image")
            return await client.send_top52810_image(image, plan)
        except (FlexDisplayApiError, OSError, ValueError) as err:
            raise ServiceValidationError(str(err)) from err

    if not hass.services.has_service(DOMAIN, "send_image"):
        hass.services.async_register(
            DOMAIN, "send_image", send_image,
            schema=vol.Schema({
                vol.Required("device_id"): cv.string,
                vol.Required("image_file"): IMAGE_SELECTION,
                vol.Optional("resize_mode", default="fit"): vol.In({"fit", "crop"}),
            }),
            supports_response=SupportsResponse.OPTIONAL,
        )

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
