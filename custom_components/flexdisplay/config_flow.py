"""Configuration flow for FlexDisplay."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.const import CONF_URL
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import FlexDisplayApiClient, FlexDisplayApiError
from .const import CONF_API_KEY, DEFAULT_URL, DOMAIN


class FlexDisplayConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Configure a local FlexDisplay bridge."""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Handle initial setup."""
        errors: dict[str, str] = {}
        if user_input is not None:
            url = user_input[CONF_URL].rstrip("/")
            client = FlexDisplayApiClient(
                async_get_clientsession(self.hass),
                url,
                user_input.get(CONF_API_KEY, ""),
            )
            try:
                await client.health()
            except FlexDisplayApiError:
                errors["base"] = "cannot_connect"
            else:
                try:
                    await client.devices()
                except FlexDisplayApiError:
                    errors["base"] = (
                        "invalid_auth"
                        if user_input.get(CONF_API_KEY)
                        else "api_key_required"
                    )
                else:
                    await self.async_set_unique_id(url.lower())
                    self._abort_if_unique_id_configured()
                    return self.async_create_entry(
                        title="FlexDisplay Bridge",
                        data={
                            CONF_URL: url,
                            CONF_API_KEY: user_input.get(CONF_API_KEY, ""),
                        },
                    )

        schema = vol.Schema(
            {
                vol.Required(CONF_URL, default=DEFAULT_URL): str,
                vol.Optional(CONF_API_KEY, default=""): str,
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)
