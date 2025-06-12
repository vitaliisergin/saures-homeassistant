"""Saures API Client."""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Optional
from datetime import datetime, timedelta
from collections import OrderedDict

import aiohttp
from aiohttp import ClientOSError, ContentTypeError, ClientSession

from .const import API_URL, REQUEST_ATTEMPTS

_LOGGER = logging.getLogger(__name__)


class LRUCache:
    """LRU Cache with size limit to prevent memory leaks."""
    
    def __init__(self, maxsize: int = 100) -> None:
        """Initialize LRU cache with maximum size."""
        self.cache = OrderedDict()
        self.maxsize = maxsize
        
    def get(self, key: str) -> dict | None:
        """Get item from cache and move to end (most recently used)."""
        if key in self.cache:
            self.cache.move_to_end(key)
            return self.cache[key]
        return None
        
    def set(self, key: str, value: dict) -> None:
        """Set item in cache, remove oldest if size exceeded."""
        if key in self.cache:
            self.cache.move_to_end(key)
        elif len(self.cache) >= self.maxsize:
            # Remove least recently used item
            oldest_key = next(iter(self.cache))
            del self.cache[oldest_key]
            _LOGGER.debug("Removed oldest cache entry: %s", oldest_key)
        self.cache[key] = value
        
    def clear(self) -> None:
        """Clear all cache entries."""
        self.cache.clear()
        
    def size(self) -> int:
        """Get current cache size."""
        return len(self.cache)


class SauresAPIClient:
    """Optimized Saures API client with session reuse, LRU caching and proper error handling."""
    
    def __init__(self, email: str, password: str) -> None:
        """Initialize the API client."""
        self._email = email
        self._password = password
        
        # Session management
        self._session: Optional[ClientSession] = None
        
        # SID management with TTL (15 minutes as per API docs)
        self._sid = None
        self._sid_expires = None
        self._sid_renewal = False
        
        # LRU Cache for data with size limit
        self._cache = LRUCache(maxsize=50)  # Limit to 50 entries
        
        # Error monitoring
        self._error_count = 0
        self._last_error_time = 0
        self._duplicate_errors = 0
        self._rate_limit_errors = 0
        
    async def _get_session(self) -> ClientSession:
        """Get or create HTTP session."""
        if self._session is None or self._session.closed:
            timeout = aiohttp.ClientTimeout(total=30, connect=10)
            self._session = ClientSession(timeout=timeout)
        return self._session
        
    async def close(self) -> None:
        """Close HTTP session and clear cache."""
        if self._session and not self._session.closed:
            await self._session.close()
        self._cache.clear()
        _LOGGER.debug("API client closed and cache cleared")
            
    def _is_sid_valid(self) -> bool:
        """Check if current SID is still valid (within 15 minutes TTL)."""
        if not self._sid or not self._sid_expires:
            return False
        return datetime.now() < self._sid_expires
        
    def _calculate_backoff_delay(self, attempt: int, base_delay: float = 1.0) -> float:
        """Calculate exponential backoff delay."""
        return min(base_delay * (2 ** (attempt - 1)), 60.0)  # Max 60 seconds
        
    def _get_cache_key(self, method: str, url: str, **kwargs) -> str:
        """Generate cache key for request."""
        params_str = "_".join(f"{k}={v}" for k, v in sorted(kwargs.items()))
        return f"{method}_{url}_{params_str}"
        
    def _is_cache_valid(self, cache_entry: dict, ttl_minutes: int = 5) -> bool:
        """Check if cached data is still valid."""
        if not cache_entry:
            return False
        cache_time = cache_entry.get("timestamp", 0)
        ttl_remaining = ttl_minutes * 60 - (time.time() - cache_time)
        
        if ttl_remaining > 0:
            _LOGGER.debug("Cache hit, TTL remaining: %.1f seconds", ttl_remaining)
            return True
        return False
        
    async def request(self, method: str, url: str, use_cache: bool = True, cache_ttl: int = 5, **kwargs) -> dict[str, Any] | bool:
        """Make API request with retry logic, LRU caching and exponential backoff."""
        
        # Check cache first
        cache_key = self._get_cache_key(method, url, **kwargs)
        if use_cache:
            cached = self._cache.get(cache_key)
            if cached and self._is_cache_valid(cached, cache_ttl):
                _LOGGER.debug("Using cached data", extra={
                    "cache_key": cache_key[:50],  # Truncate for readability
                    "cache_size": self._cache.size()
                })
                return cached["data"]
                
        data = {}
        data.update(kwargs)
        
        for attempt in range(1, REQUEST_ATTEMPTS + 1):
            # Add SID if available and valid
            if self._is_sid_valid():
                data.update({"sid": self._sid})
                
            try:
                session = await self._get_session()
                async with session.request(
                    method,
                    API_URL + url,
                    params=data if method == "GET" else None,
                    data=data if method == "POST" else None,
                ) as request:
                    response = await request.json()
                    
            except (ClientOSError, ContentTypeError) as err:
                _LOGGER.warning("Network error (attempt %d/%d): %s", attempt, REQUEST_ATTEMPTS, err)
                self._error_count += 1
                self._last_error_time = time.time()
                
                if attempt == REQUEST_ATTEMPTS:
                    # Return stale cached data if available as fallback
                    if use_cache:
                        cached = self._cache.get(cache_key)
                        if cached:
                            _LOGGER.warning("API unavailable, using stale cached data")
                            return cached["data"]
                    return False
                    
                # Exponential backoff for network errors
                delay = self._calculate_backoff_delay(attempt, 2.0)
                await asyncio.sleep(delay)
                continue
                
            except Exception as err:
                _LOGGER.error("Unexpected error: %s", err)
                if attempt == REQUEST_ATTEMPTS:
                    return False
                await asyncio.sleep(self._calculate_backoff_delay(attempt))
                continue
                
            # Process response
            if response.get("status") == "ok":
                # Cache successful response
                if use_cache:
                    self._cache.set(cache_key, {
                        "data": response,
                        "timestamp": time.time()
                    })
                    
                # Reset error counters on success
                if self._error_count > 0:
                    _LOGGER.info("API connection recovered after %d errors", self._error_count)
                    self._error_count = 0
                    
                return response
                
            # Handle API errors
            errors = [e["name"] for e in response.get("errors", [])]
            
            if "WrongSIDException" in errors:
                _LOGGER.debug("SID expired, will renew")
                self._sid = None
                self._sid_expires = None
                
                if not self._sid_renewal:
                    await self._update_sid()
                    
                if attempt == REQUEST_ATTEMPTS:
                    return False
                continue
                
            if "DuplicateRequestException" in errors:
                self._duplicate_errors += 1
                _LOGGER.warning("Duplicate request detected", extra={
                    "count": self._duplicate_errors,
                    "cache_key": cache_key[:50]
                })
                
                if attempt == REQUEST_ATTEMPTS:
                    return False
                    
                # API docs recommend 10 seconds for duplicate requests
                await asyncio.sleep(10)
                continue
                
            # Check for rate limiting indicators
            if any(error in ["TooManyRequestsException", "RateLimitException"] for error in errors):
                self._rate_limit_errors += 1
                _LOGGER.warning("Rate limit hit", extra={
                    "count": self._rate_limit_errors,
                    "backoff_attempt": attempt
                })
                
                # Longer delay for rate limiting
                delay = self._calculate_backoff_delay(attempt, 30.0)
                await asyncio.sleep(delay)
                continue
                
            _LOGGER.error("API error (attempt %d/%d): %s", attempt, REQUEST_ATTEMPTS, response.get("errors"))
            
            # For other errors, try exponential backoff
            if attempt < REQUEST_ATTEMPTS:
                delay = self._calculate_backoff_delay(attempt, 5.0)
                await asyncio.sleep(delay)
            else:
                return response
                
        return False
        
    async def _update_sid(self) -> bool:
        """Update session ID with proper TTL management."""
        if self._sid_renewal:
            return False
            
        self._sid_renewal = True
        try:
            login_data = {
                "email": self._email,
                "password": self._password
            }
            
            session = await self._get_session()
            async with session.post(
                API_URL + "/login",
                data=login_data
            ) as request:
                response = await request.json()
                
            if response.get("status") == "ok":
                self._sid = response["data"]["sid"]
                # Set expiration to 14 minutes (1 minute buffer before 15 min limit)
                self._sid_expires = datetime.now() + timedelta(minutes=14)
                _LOGGER.debug("Successfully updated SID, expires at %s", self._sid_expires)
                return True
            else:
                _LOGGER.error("Login failed: %s", response.get("errors"))
                return False
                
        except Exception as err:
            _LOGGER.error("Failed to update SID: %s", err)
            return False
        finally:
            self._sid_renewal = False
            
    async def _check_sid(self) -> bool:
        """Check if SID is valid and update if needed."""
        if self._sid_renewal:
            # Wait for ongoing renewal with timeout
            timeout = 30  # 30 second timeout
            start_time = time.time()
            while self._sid_renewal and (time.time() - start_time) < timeout:
                await asyncio.sleep(0.5)
                
            # Check if timeout exceeded
            if time.time() - start_time >= timeout:
                _LOGGER.error("SID renewal timeout exceeded")
                self._sid_renewal = False
                return False
                
        if not self._is_sid_valid():
            await self._update_sid()
            
        return self._is_sid_valid()
        
    async def user_objects(self) -> list[dict[str, Any]]:
        """Get user objects with caching."""
        if await self._check_sid():
            response = await self.request("GET", "/user/objects", cache_ttl=30)  # Cache for 30 minutes
            if response and isinstance(response, dict):
                return response["data"]["objects"]
        return []
        
    async def object_meters(self, object_id: int) -> dict[str, Any]:
        """Get meters for object with caching."""
        if await self._check_sid():
            response = await self.request("GET", "/object/meters", id=object_id, cache_ttl=5)  # Cache for 5 minutes
            if response and isinstance(response, dict):
                return response["data"]
        return {}
        
    async def sensor_battery(self, sensor_sn: str, start: str = None, finish: str = None) -> dict[str, Any]:
        """Get sensor battery data with caching."""
        if await self._check_sid():
            params = {"sn": sensor_sn}
            if start:
                params["start"] = start
            if finish:
                params["finish"] = finish
                
            response = await self.request("GET", "/sensor/battery", cache_ttl=60, **params)  # Cache for 1 hour
            if response and isinstance(response, dict):
                return response["data"]
        return {}
        
    async def meter_get(self, meter_id: int, start: str = None, finish: str = None, group: str = None) -> dict[str, Any]:
        """Get detailed meter data with caching."""
        if await self._check_sid():
            params = {"id": meter_id}
            if start:
                params["start"] = start
            if finish:
                params["finish"] = finish
            if group:
                params["group"] = group
                
            cache_ttl = 60 if group else 5  # Cache longer for aggregated data
            response = await self.request("GET", "/meter/get", cache_ttl=cache_ttl, **params)
            if response and isinstance(response, dict):
                return response["data"]
        return {}
        
    def get_error_stats(self) -> dict[str, Any]:
        """Get error statistics for monitoring."""
        return {
            "total_errors": self._error_count,
            "duplicate_errors": self._duplicate_errors,
            "rate_limit_errors": self._rate_limit_errors,
            "last_error_time": self._last_error_time,
            "sid_valid": self._is_sid_valid(),
            "sid_expires": self._sid_expires.isoformat() if self._sid_expires else None,
            "cache_entries": self._cache.size(),
            "cache_max_size": self._cache.maxsize
        }