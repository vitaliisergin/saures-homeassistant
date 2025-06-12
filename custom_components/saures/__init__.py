"""Saures Water Meters integration for Home Assistant."""
from __future__ import annotations

import logging
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import SauresAPIClient
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.SENSOR,
]

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Saures from a config entry."""
    email = entry.data["email"]
    password = entry.data["password"]
    update_interval = entry.data.get("update_interval", 5)  # Default 5 minutes
    
    api_client = SauresAPIClient(email, password)
    
    # Create data coordinator
    coordinator = SauresDataUpdateCoordinator(hass, api_client, update_interval)
    
    # Store coordinator before first refresh
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = {
        "api_client": api_client,
        "coordinator": coordinator,
    }
    
    # Setup platforms first
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    
    # Try first refresh but don't fail setup if it doesn't work
    try:
        await coordinator.async_config_entry_first_refresh()
        _LOGGER.info("Initial data fetch successful")
    except Exception as err:
        _LOGGER.warning("Initial data fetch failed, will retry later: %s", err)
    
    return True

async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        hass.data[DOMAIN].pop(entry.entry_id)
    
    return unload_ok

class SauresDataUpdateCoordinator(DataUpdateCoordinator):
    """Class to manage fetching Saures data."""
    
    def __init__(self, hass: HomeAssistant, api_client: SauresAPIClient, update_interval: int) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(minutes=update_interval),
        )
        self.api_client = api_client
        
    async def _async_update_data(self):
        """Update data via library."""
        try:
            # Get user objects
            objects = await self.api_client.user_objects()
            
            if not objects:
                raise UpdateFailed("No objects returned from API")
            
            data = {"objects": {}}
            
            for obj in objects:
                obj_id = obj["id"]
                
                # Get meters for each object
                meters_data = await self.api_client.object_meters(obj_id)
                
                data["objects"][obj_id] = {
                    "info": obj,
                    "sensors": meters_data.get("sensors", [])
                }
                
            _LOGGER.debug("Successfully updated data for %d objects", len(data["objects"]))
            return data
            
        except Exception as err:
            _LOGGER.error("Error updating Saures data: %s", err)
            raise UpdateFailed(f"Error communicating with API: {err}") from err 