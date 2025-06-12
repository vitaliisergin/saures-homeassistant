"""Saures API Client."""
from __future__ import annotations

import asyncio
import logging
from typing import Any

import aiohttp
from aiohttp import ClientOSError, ContentTypeError

from .const import API_URL, REQUEST_ATTEMPTS

_LOGGER = logging.getLogger(__name__)


class SauresAPIClient:
    """Saures API client with automatic SID renewal."""
    
    def __init__(self, email: str, password: str) -> None:
        """Initialize the API client."""
        self._email = email
        self._password = password
        self._sid = None
        self._sid_renewal = False
        
    async def request(self, method: str, url: str, **kwargs) -> dict[str, Any] | bool:
        """Make API request with retry logic."""
        data = {}
        data.update(kwargs)
        
        for i in range(1, REQUEST_ATTEMPTS + 1):
            if self._sid:
                data.update({"sid": self._sid})
                
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.request(
                        method,
                        API_URL + url,
                        params=data if method == "GET" else None,
                        data=data if method == "POST" else None,
                    ) as request:
                        response = await request.json()
            except (ClientOSError, ContentTypeError) as err:
                _LOGGER.warning("Request error (attempt %d/%d): %s", i, REQUEST_ATTEMPTS, err)
                if i == REQUEST_ATTEMPTS:
                    return False
                await asyncio.sleep(60)
            else:
                if response.get("status") == "ok":
                    return response
                    
                errors = [e["name"] for e in response["errors"]]
                
                if "WrongSIDException" in errors:
                    if not self._sid_renewal:
                        self._sid = None
                    if i == REQUEST_ATTEMPTS:
                        return False
                    await self._check_sid()
                    continue
                    
                if "DuplicateRequestException" in errors:
                    if i == REQUEST_ATTEMPTS:
                        return False
                    await asyncio.sleep(10)
                    continue
                    
                _LOGGER.error("API error: %s", response.get("errors"))
                return response
                
        return False
        
    async def _update_sid(self) -> bool:
        """Update session ID by logging in."""
        if self._sid_renewal:
            return False
            
        self._sid_renewal = True
        try:
            response = await self.request(
                "POST", "/login",
                email=self._email,
                password=self._password
            )
            if response and isinstance(response, dict):
                self._sid = response["data"]["sid"]
                _LOGGER.debug("Successfully updated SID")
                return True
        except Exception as err:
            _LOGGER.error("Failed to update SID: %s", err)
        finally:
            self._sid_renewal = False
            
        return False
        
    async def _check_sid(self) -> bool:
        """Check if SID is valid and update if needed."""
        if self._sid_renewal:
            while self._sid_renewal:
                await asyncio.sleep(1)
        elif not self._sid:
            await self._update_sid()
            
        return bool(self._sid)
        
    async def user_objects(self) -> list[dict[str, Any]]:
        """Get user objects."""
        if await self._check_sid():
            response = await self.request("GET", "/user/objects")
            if response and isinstance(response, dict):
                return response["data"]["objects"]
        return []
        
    async def object_meters(self, object_id: int) -> dict[str, Any]:
        """Get meters for object."""
        if await self._check_sid():
            response = await self.request("GET", "/object/meters", id=object_id)
            if response and isinstance(response, dict):
                return response["data"]
        return {}
        
    async def sensor_battery(self, sensor_sn: str, start: str = None, finish: str = None) -> dict[str, Any]:
        """Get sensor battery data."""
        if await self._check_sid():
            params = {"sn": sensor_sn}
            if start:
                params["start"] = start
            if finish:
                params["finish"] = finish
                
            response = await self.request("GET", "/sensor/battery", **params)
            if response and isinstance(response, dict):
                return response["data"]
        return {}
        

        
    async def meter_get(self, meter_id: int, start: str = None, finish: str = None, group: str = None) -> dict[str, Any]:
        """Get detailed meter data."""
        if await self._check_sid():
            params = {"id": meter_id}
            if start:
                params["start"] = start
            if finish:
                params["finish"] = finish
            if group:
                params["group"] = group
                
            response = await self.request("GET", "/meter/get", **params)
            if response and isinstance(response, dict):
                return response["data"]
        return {} 