"""Config flow for Saures integration."""
from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResult
from homeassistant.exceptions import HomeAssistantError

from .api import SauresAPIClient
from .const import DOMAIN, MIN_UPDATE_INTERVAL, MAX_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL_MINUTES

_LOGGER = logging.getLogger(__name__)

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required("email"): str,
        vol.Required("password"): str,
        vol.Required("update_interval", default=DEFAULT_UPDATE_INTERVAL_MINUTES): vol.All(
            vol.Coerce(int),
            vol.Range(min=MIN_UPDATE_INTERVAL, max=MAX_UPDATE_INTERVAL)
        ),
    }
)


async def validate_input(hass: HomeAssistant, data: dict[str, Any]) -> dict[str, Any]:
    """Validate the user input allows us to connect."""
    
    api_client = SauresAPIClient(data["email"], data["password"])
    
    try:
        objects = await api_client.user_objects()
        if not objects:
            raise InvalidAuth("No objects found")
            
        # Return info that you want to store in the config entry.
        return {
            "title": f"Saures ({data['email']})",
            "objects_count": len(objects)
        }
    except Exception as err:
        _LOGGER.error("Failed to connect: %s", err)
        raise CannotConnect from err
    finally:
        # Always close the API client to free resources
        await api_client.close()


class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Saures."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the initial step."""
        errors: dict[str, str] = {}
        
        if user_input is not None:
            try:
                info = await validate_input(self.hass, user_input)
            except CannotConnect:
                errors["base"] = "cannot_connect"
            except InvalidAuth:
                errors["base"] = "invalid_auth"
            except Exception:  # pylint: disable=broad-except
                _LOGGER.exception("Unexpected exception")
                errors["base"] = "unknown"
            else:
                # Set unique_id to prevent duplicate entries
                await self.async_set_unique_id(user_input["email"])
                self._abort_if_unique_id_configured()
                
                return self.async_create_entry(title=info["title"], data=user_input)

        return self.async_show_form(
            step_id="user", data_schema=STEP_USER_DATA_SCHEMA, errors=errors
        )


class CannotConnect(HomeAssistantError):
    """Error to indicate we cannot connect."""


class InvalidAuth(HomeAssistantError):
    """Error to indicate there is invalid auth.""" 