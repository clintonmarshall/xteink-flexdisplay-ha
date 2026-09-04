"""FlexDisplay Home Assistant integration."""

from __future__ import annotations

import re

import homeassistant.helpers.config_validation as cv
import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_URL, Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import FlexDisplayApiClient, FlexDisplayApiError
from .const import (
    CONF_API_KEY,
    DOMAIN,
    PLATFORMS,
    SERVICE_CLEAR_MESHTASTIC_UNREAD,
    SERVICE_NOTIFY,
    SERVICE_SEND_MESHTASTIC_MESSAGE,
)
from .coordinator import FlexDisplayCoordinator
from .top52810_ble import Top52810BleManager

DATA_COORDINATORS = "coordinators"
DATA_TOP52810_MANAGERS = "top52810_managers"
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


def _meshtastic_text(value: object) -> str:
    """Validate Meshtastic's encoded payload limit, not Python character count."""
    text = cv.string(value).strip()
    if not text:
        raise vol.Invalid("Message text cannot be empty")
    if any(ord(character) < 0x20 and character not in "\t\n\r" for character in text):
        raise vol.Invalid("Message text contains an invalid control character")
    try:
        encoded = text.encode("utf-8")
    except UnicodeEncodeError as err:
        raise vol.Invalid("Message text is not valid UTF-8") from err
    if len(encoded) > 220:
        raise vol.Invalid("Message text must be no more than 220 UTF-8 bytes")
    return text


def _meshtastic_destination(value: object) -> str:
    """Validate a broadcast alias or canonical Meshtastic node ID."""
    destination = cv.string(value).strip()
    if destination.lower() == "broadcast":
        return "broadcast"
    if not re.fullmatch(r"![0-9a-fA-F]{8}", destination):
        raise vol.Invalid(
            "Destination must be broadcast or a node ID such as !12345678"
        )
    normalized = destination.lower()
    if normalized in {"!00000000", "!ffffffff"}:
        raise vol.Invalid("Destination uses a reserved Meshtastic node ID")
    return normalized


SEND_MESHTASTIC_SCHEMA = vol.Schema(
    {
        vol.Required("text"): _meshtastic_text,
        vol.Optional("destination", default="broadcast"): _meshtastic_destination,
        vol.Optional("channel", default=0): vol.All(
            vol.Coerce(int),
            vol.Range(min=0, max=7),
        ),
        vol.Optional("request_ack", default=False): cv.boolean,
        vol.Optional("config_entry_id"): cv.string,
    }
)

CLEAR_UNREAD_SCHEMA = vol.Schema({vol.Optional("config_entry_id"): cv.string})

NOTIFICATION_ACTION_SCHEMA = vol.Schema(
    {
        vol.Required("label"): cv.string,
        vol.Required("service"): cv.string,
        vol.Required("entity_id"): cv.entity_id,
        vol.Optional("data", default={}): dict,
        vol.Optional("confirmation", default=False): cv.boolean,
        vol.Optional("confirmation_text", default=""): cv.string,
    }
)


def _notification_actions(value: object) -> list[dict[str, object]]:
    actions = cv.ensure_list(value)
    if len(actions) > 3:
        raise vol.Invalid("A FlexDisplay notification may contain at most three actions")
    return [NOTIFICATION_ACTION_SCHEMA(action) for action in actions]


NOTIFY_SCHEMA = vol.Schema(
    {
        vol.Required("device_id"): cv.string,
        vol.Required("title"): cv.string,
        vol.Optional("message", default=""): cv.string,
        vol.Optional("camera_entity", default=""): vol.Any("", cv.entity_id),
        vol.Optional("chime", default="default"): vol.In(
            {"none", "default", "doorbell", "alert"}
        ),
        vol.Optional("duration", default=20): vol.All(
            vol.Coerce(int), vol.Range(min=5, max=300)
        ),
        vol.Optional("actions", default=[]): _notification_actions,
        vol.Optional("config_entry_id"): cv.string,
    }
)


def _coordinator_for_call(
    hass: HomeAssistant,
    call: ServiceCall,
) -> FlexDisplayCoordinator:
    """Resolve an optional config entry or use the first configured bridge."""
    coordinators: dict[str, FlexDisplayCoordinator] = hass.data.get(DOMAIN, {}).get(
        DATA_COORDINATORS,
        {},
    )
    requested = str(call.data.get("config_entry_id") or "")
    if requested:
        coordinator = coordinators.get(requested)
        if coordinator:
            return coordinator
        raise ServiceValidationError(
            "The selected FlexDisplay config entry is unavailable"
        )
    if len(coordinators) == 1:
        return next(iter(coordinators.values()))
    if len(coordinators) > 1:
        raise ServiceValidationError(
            "Select a FlexDisplay config entry when more than one Bridge is configured"
        )
    raise ServiceValidationError("No FlexDisplay Bridge is configured")


def _register_services(hass: HomeAssistant) -> None:
    """Register integration actions once for all config entries."""
    if not hass.services.has_service(DOMAIN, SERVICE_SEND_MESHTASTIC_MESSAGE):

        async def send_meshtastic(call: ServiceCall) -> None:
            coordinator = _coordinator_for_call(hass, call)
            try:
                await coordinator.client.send_meshtastic_message(
                    text=call.data["text"],
                    destination=call.data["destination"],
                    channel=call.data["channel"],
                    request_ack=call.data["request_ack"],
                )
            except FlexDisplayApiError as err:
                raise ServiceValidationError(str(err)) from err
            await coordinator.async_request_refresh()

        hass.services.async_register(
            DOMAIN,
            SERVICE_SEND_MESHTASTIC_MESSAGE,
            send_meshtastic,
            schema=SEND_MESHTASTIC_SCHEMA,
        )

    if not hass.services.has_service(DOMAIN, SERVICE_CLEAR_MESHTASTIC_UNREAD):

        async def clear_meshtastic_unread(call: ServiceCall) -> None:
            coordinator = _coordinator_for_call(hass, call)
            try:
                await coordinator.client.mark_meshtastic_read()
            except FlexDisplayApiError as err:
                raise ServiceValidationError(str(err)) from err
            coordinator.clear_meshtastic_unread()

        hass.services.async_register(
            DOMAIN,
            SERVICE_CLEAR_MESHTASTIC_UNREAD,
            clear_meshtastic_unread,
            schema=CLEAR_UNREAD_SCHEMA,
        )

    if not hass.services.has_service(DOMAIN, SERVICE_NOTIFY):

        async def notify(call: ServiceCall) -> None:
            coordinator = _coordinator_for_call(hass, call)
            try:
                await coordinator.client.notify(
                    call.data["device_id"],
                    title=call.data["title"],
                    message=call.data["message"],
                    camera_entity=call.data["camera_entity"],
                    chime=call.data["chime"],
                    duration=call.data["duration"],
                    actions=call.data["actions"],
                )
            except FlexDisplayApiError as err:
                raise ServiceValidationError(str(err)) from err
            await coordinator.async_request_refresh()

        hass.services.async_register(
            DOMAIN,
            SERVICE_NOTIFY,
            notify,
            schema=NOTIFY_SCHEMA,
        )


async def async_setup(hass: HomeAssistant, _config: dict) -> bool:
    """Register actions independently of config-entry loading."""
    _register_services(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up FlexDisplay from a config entry."""
    client = FlexDisplayApiClient(
        async_get_clientsession(hass),
        entry.data[CONF_URL],
        entry.data.get(CONF_API_KEY, ""),
    )
    coordinator = FlexDisplayCoordinator(hass, client, entry.entry_id)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    hass.data.setdefault(DOMAIN, {}).setdefault(DATA_COORDINATORS, {})[
        entry.entry_id
    ] = coordinator
    _register_services(hass)
    await hass.config_entries.async_forward_entry_setups(
        entry,
        [Platform(platform) for platform in PLATFORMS],
    )
    ble_manager = Top52810BleManager(
        hass,
        client,
        f"home-assistant:{entry.entry_id}",
    )
    ble_manager.start()
    hass.data.setdefault(DOMAIN, {}).setdefault(DATA_TOP52810_MANAGERS, {})[
        entry.entry_id
    ] = ble_manager
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a FlexDisplay config entry."""
    unloaded = await hass.config_entries.async_unload_platforms(
        entry,
        [Platform(platform) for platform in PLATFORMS],
    )
    if not unloaded:
        return False
    coordinators = hass.data.get(DOMAIN, {}).get(DATA_COORDINATORS, {})
    coordinators.pop(entry.entry_id, None)
    managers = hass.data.get(DOMAIN, {}).get(DATA_TOP52810_MANAGERS, {})
    manager = managers.pop(entry.entry_id, None)
    if manager:
        manager.stop()
    return True
