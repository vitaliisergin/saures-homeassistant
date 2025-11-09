"""Saures sensors."""
from __future__ import annotations

import logging
import re
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.statistics import (
    async_add_external_statistics,
    get_last_statistics,
    StatisticData,
    StatisticMetaData,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.const import (
    PERCENTAGE,
    UnitOfVolume,
    SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
)
from homeassistant.util import dt as dt_util

from .const import DOMAIN, DEVICE_STATES, STATISTICS_IMPORT_INTERVAL

_LOGGER = logging.getLogger(__name__)


def _parse_saures_datetime(value: str | None, hass: HomeAssistant | None = None) -> datetime | None:
    """Parse Saures datetime string and return timezone-aware UTC datetime."""
    if not value:
        return None

    value = value.strip()
    parsed: datetime | None = None

    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            parsed = datetime.strptime(value, fmt)
            break
        except ValueError:
            continue

    if not parsed:
        _LOGGER.debug("Не удалось распарсить дату '%s'", value)
        return None

    tz = ZoneInfo("UTC")
    if hass and hass.config.time_zone:
        hass_tz = dt_util.get_time_zone(hass.config.time_zone)
        if hass_tz:
            tz = hass_tz

    aware = parsed.replace(tzinfo=tz)
    return dt_util.as_utc(aware)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Saures sensors from a config entry."""
    
    data = hass.data[DOMAIN][config_entry.entry_id]
    coordinator = data["coordinator"]
    
    known_entity_ids: set[str] = set()

    @callback
    def _collect_new_entities() -> None:
        """Create and add new entities based on latest coordinator data."""
        if not coordinator.data:
            _LOGGER.debug("No coordinator data available yet")
            return

        new_entities: list[SensorEntity] = []

        _LOGGER.debug("Evaluating coordinator data for new sensors...")
        for object_id, object_data in coordinator.data["objects"].items():
            for sensor in object_data["sensors"]:
                controller_entities = [
                    SauresBatterySensor(coordinator, object_id, sensor),
                    SauresRSSISensor(coordinator, object_id, sensor),
                    SauresLastConnectionSensor(coordinator, object_id, sensor),
                    SauresRequestDateSensor(coordinator, object_id, sensor),
                    SauresReadoutDateSensor(coordinator, object_id, sensor),
                    SauresAPIDiagnosticSensor(coordinator, object_id, sensor),
                ]

                for entity in controller_entities:
                    if entity.unique_id not in known_entity_ids:
                        known_entity_ids.add(entity.unique_id)
                        new_entities.append(entity)

                for meter in sensor.get("meters", []):
                    meter_type = meter.get("type", {}).get("number")
                    if meter_type not in [1, 2]:
                        continue

                    meter_entity = SauresWaterMeterSensor(coordinator, object_id, sensor, meter)
                    if meter_entity.unique_id not in known_entity_ids:
                        known_entity_ids.add(meter_entity.unique_id)
                        new_entities.append(meter_entity)

        if new_entities:
            _LOGGER.info("Adding %d new sensors to Home Assistant", len(new_entities))
            async_add_entities(new_entities)

    # Register listener to add sensors when coordinator updates
    remove_listener = coordinator.async_add_listener(_collect_new_entities)
    config_entry.async_on_unload(remove_listener)

    # Try to add entities immediately if data exists
    _collect_new_entities()

    # If данные ещё не получены, запросим обновление после регистрации слушателей
    if not coordinator.data:
        await coordinator.async_request_refresh()


class SauresBaseEntity(CoordinatorEntity):
    """Base Saures entity."""
    
    def __init__(self, coordinator, object_id: int, sensor: dict) -> None:
        """Initialize the entity."""
        super().__init__(coordinator)
        self._object_id = object_id
        self._sensor = sensor
        
        # Device info
        controller_name = f"Контроллер {sensor['sn']}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, sensor["sn"])},
            name=controller_name,
            manufacturer="Saures",
            model=sensor.get("hardware", "R1"),
            sw_version=sensor.get("firmware"),
            serial_number=sensor["sn"],
        )
        
    def _get_sensor_data(self) -> dict | None:
        """Get current sensor data from coordinator."""
        if not self.coordinator.data:
            return None
            
        obj_data = self.coordinator.data["objects"].get(self._object_id)
        if not obj_data:
            return None
            
        for sensor in obj_data["sensors"]:
            if sensor["sn"] == self._sensor["sn"]:
                return sensor
        return None
        
    def _get_meter_data(self, meter_id: int) -> dict | None:
        """Get current meter data from coordinator."""
        sensor_data = self._get_sensor_data()
        if not sensor_data:
            return None
            
        for meter in sensor_data.get("meters", []):
            if meter["meter_id"] == meter_id:
                return meter
        return None


class SauresBatterySensor(SauresBaseEntity, SensorEntity):
    """Battery level sensor."""
    
    def __init__(self, coordinator, object_id: int, sensor: dict) -> None:
        """Initialize battery sensor."""
        super().__init__(coordinator, object_id, sensor)
        
        controller_name = f"Контроллер {sensor['sn']}"
        self._attr_unique_id = f"{sensor['sn']}_battery"
        self._attr_name = f"{controller_name} Battery"
        self._attr_device_class = SensorDeviceClass.BATTERY
        self._attr_native_unit_of_measurement = PERCENTAGE
        self._attr_state_class = SensorStateClass.MEASUREMENT
        
    @property
    def native_value(self) -> int | None:
        """Return battery level."""
        sensor_data = self._get_sensor_data()
        if sensor_data:
            return sensor_data.get("bat")
        return None


class SauresRSSISensor(SauresBaseEntity, SensorEntity):
    """RSSI sensor."""

    def __init__(self, coordinator, object_id: int, sensor: dict) -> None:
        """Initialize RSSI sensor."""
        super().__init__(coordinator, object_id, sensor)

        controller_name = f"Контроллер {sensor['sn']}"
        self._attr_unique_id = f"{sensor['sn']}_rssi"
        self._attr_name = f"{controller_name} Signal Strength"
        self._attr_device_class = SensorDeviceClass.SIGNAL_STRENGTH
        self._attr_native_unit_of_measurement = SIGNAL_STRENGTH_DECIBELS_MILLIWATT
        self._attr_state_class = SensorStateClass.MEASUREMENT

    @property
    def native_value(self) -> int | None:
        """Return RSSI value."""
        sensor_data = self._get_sensor_data()
        if sensor_data:
            rssi = sensor_data.get("rssi")
            if rssi and rssi.lstrip("-").isdigit():
                return int(rssi)
        return None


class SauresLastConnectionSensor(SauresBaseEntity, SensorEntity):
    """Last connection timestamp sensor."""

    def __init__(self, coordinator, object_id: int, sensor: dict) -> None:
        """Initialize last connection sensor."""
        super().__init__(coordinator, object_id, sensor)

        controller_name = f"Контроллер {sensor['sn']}"
        self._attr_unique_id = f"{sensor['sn']}_last_connection"
        self._attr_name = f"{controller_name} Last Connection"
        self._attr_device_class = SensorDeviceClass.TIMESTAMP
        self._attr_icon = "mdi:connection"

    @property
    def native_value(self) -> datetime | None:
        """Return last connection timestamp."""
        sensor_data = self._get_sensor_data()
        if sensor_data:
            last_conn = sensor_data.get("last_connection")
            if last_conn:
                try:
                    return _parse_saures_datetime(last_conn, self.hass)
                except (ValueError, TypeError):
                    return None
        return None


class SauresRequestDateSensor(SauresBaseEntity, SensorEntity):
    """Request date timestamp sensor - when controller last requested data."""

    def __init__(self, coordinator, object_id: int, sensor: dict) -> None:
        """Initialize request date sensor."""
        super().__init__(coordinator, object_id, sensor)

        controller_name = f"Контроллер {sensor['sn']}"
        self._attr_unique_id = f"{sensor['sn']}_request_dt"
        self._attr_name = f"{controller_name} Last Request"
        self._attr_device_class = SensorDeviceClass.TIMESTAMP
        self._attr_icon = "mdi:upload-network"

    @property
    def native_value(self) -> datetime | None:
        """Return request timestamp."""
        sensor_data = self._get_sensor_data()
        if sensor_data:
            request_dt = sensor_data.get("request_dt")
            if request_dt:
                try:
                    return _parse_saures_datetime(request_dt, self.hass)
                except (ValueError, TypeError):
                    return None
        return None


class SauresReadoutDateSensor(SauresBaseEntity, SensorEntity):
    """Readout date timestamp sensor - when last meter readings were taken."""

    def __init__(self, coordinator, object_id: int, sensor: dict) -> None:
        """Initialize readout date sensor."""
        super().__init__(coordinator, object_id, sensor)

        controller_name = f"Контроллер {sensor['sn']}"
        self._attr_unique_id = f"{sensor['sn']}_readout_dt"
        self._attr_name = f"{controller_name} Last Readout"
        self._attr_device_class = SensorDeviceClass.TIMESTAMP
        self._attr_icon = "mdi:counter"

    @property
    def native_value(self) -> datetime | None:
        """Return readout timestamp."""
        sensor_data = self._get_sensor_data()
        if sensor_data:
            readout_dt = sensor_data.get("readout_dt")
            if readout_dt:
                try:
                    return _parse_saures_datetime(readout_dt, self.hass)
                except (ValueError, TypeError):
                    return None
        return None


class SauresWaterMeterSensor(SauresBaseEntity, SensorEntity):
    """Water meter sensor."""

    def __init__(self, coordinator, object_id: int, sensor: dict, meter: dict) -> None:
        """Initialize water meter sensor."""
        super().__init__(coordinator, object_id, sensor)
        self._meter = meter
        self._last_import_time: datetime | None = None

        meter_type_num = meter.get("type", {}).get("number")
        meter_type = "Холодная вода" if meter_type_num == 1 else "Горячая вода" if meter_type_num == 2 else "Unknown"
        meter_name = meter.get("meter_name") or meter_type

        controller_name = f"Контроллер {sensor['sn']}"
        self._attr_unique_id = f"{sensor['sn']}_meter_{meter['meter_id']}"
        self._attr_name = f"{controller_name} {meter_name}"
        self._attr_device_class = SensorDeviceClass.WATER
        self._attr_native_unit_of_measurement = UnitOfVolume.CUBIC_METERS
        self._attr_state_class = SensorStateClass.TOTAL_INCREASING
        
    @property
    def native_value(self) -> float | None:
        """Return meter reading."""
        meter_data = self._get_meter_data(self._meter["meter_id"])
        if meter_data:
            vals = meter_data.get("vals", [])
            if vals and len(vals) > 0:
                return float(vals[0])
        return None
        
    @property
    def extra_state_attributes(self) -> dict:
        """Return additional attributes."""
        meter_data = self._get_meter_data(self._meter["meter_id"])
        if meter_data:
            state_info = meter_data.get("state", {})
            state_name = DEVICE_STATES.get(
                state_info.get("number"),
                state_info.get("name", "Unknown")
            )

            return {
                "meter_id": meter_data["meter_id"],
                "serial_number": meter_data.get("sn"),
                "input": meter_data.get("input"),
                "state": state_name,
                "state_number": state_info.get("number"),
                "unit": meter_data.get("unit"),
            }
        return {}

    async def async_added_to_hass(self) -> None:
        """Handle entity which will be added."""
        await super().async_added_to_hass()

        # Import historical statistics on first setup (last 7 days)
        self.hass.async_create_task(self._async_import_statistics(initial=True))

        # Schedule daily statistics import (once per 24 hours)
        async def _daily_import(_):
            """Import statistics once per day."""
            await self._async_import_statistics(initial=False)

        self.async_on_remove(
            async_track_time_interval(
                self.hass, _daily_import, timedelta(seconds=STATISTICS_IMPORT_INTERVAL)
            )
        )

    async def _async_import_statistics(self, initial: bool = False) -> None:
        """Import statistics from Saures API to Home Assistant."""
        try:
            meter_id = self._meter["meter_id"]
            
            # For external statistics, use format: "domain:object_id"
            # statistic_id MUST be lowercase and contain only [a-z0-9_:] (Home Assistant requirement)
            # Clean unique_id: convert to lowercase and ensure only valid characters
            clean_object_id = re.sub(r'[^a-z0-9_]', '_', self._attr_unique_id.lower())
            statistic_id = f"{DOMAIN}:{clean_object_id}"
            
            _LOGGER.info(
                "Importing statistics for meter %s: statistic_id=%s, unique_id=%s",
                meter_id,
                statistic_id,
                self._attr_unique_id
            )

            # Determine time range for import
            if initial:
                # On first setup, import last 7 days
                start_time = datetime.now() - timedelta(days=7)
                _LOGGER.info("Initial statistics import for meter %s, importing last 7 days", meter_id)
            else:
                # On update, import only new data since last import
                if self._last_import_time:
                    start_time = self._last_import_time
                else:
                    # Fallback: check database for last recorded statistic
                    last_stats = await get_instance(self.hass).async_add_executor_job(
                        get_last_statistics,
                        self.hass,
                        1,
                        statistic_id,
                        include_start_time=True,
                        types=set(),
                    )

                    if last_stats and statistic_id in last_stats:
                        last_stat_time = last_stats[statistic_id][0]["start"]
                        start_time = datetime.fromtimestamp(last_stat_time)
                        _LOGGER.debug("Importing statistics since last recorded: %s", start_time)
                    else:
                        # No previous data, import last 24 hours
                        start_time = datetime.now() - timedelta(hours=24)
                        _LOGGER.debug("No previous statistics found, importing last 24 hours")

            # Format dates for API (ISO 8601: YYYY-MM-DDTHH:MM:SS)
            start_str = start_time.strftime("%Y-%m-%dT%H:%M:%S")
            finish_str = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")

            # Fetch historical data from API
            # Use daily aggregation for initial import to reduce API load and data points
            # Use absolute=True to get absolute meter readings (not consumption)
            api_client = self.coordinator.api_client
            meter_data = await api_client.meter_get(
                meter_id=meter_id,
                start=start_str,
                finish=finish_str,
                group='day' if initial else None,  # Daily aggregation for historical data
                absolute=True  # Get absolute meter readings for TOTAL_INCREASING sensor
            )

            raw_vals = meter_data.get("vals")
            raw_points = meter_data.get("points")
            raw_times = meter_data.get("times") or meter_data.get("datetimes") or meter_data.get("dates")

            _LOGGER.debug(
                "Meter %s API response: points=%s, vals=%s, times=%s, full_data=%s",
                meter_id,
                type(raw_points).__name__ if raw_points else None,
                type(raw_vals).__name__ if raw_vals else None,
                type(raw_times).__name__ if raw_times else None,
                meter_data
            )

            samples: list[tuple[float, float]] = []

            def _normalize_value(raw_value: float | str | int | list | tuple | None) -> float | None:
                """Convert Saures value representations to float."""
                if raw_value is None:
                    return None

                candidate = raw_value

                if isinstance(candidate, (list, tuple)):
                    if not candidate:
                        return None
                    candidate = candidate[0]

                try:
                    return float(candidate)
                except (ValueError, TypeError):
                    return None

            def _append_sample(raw_time: str | None, raw_value: float | str | int | list | tuple | None) -> None:
                """Validate and append a single sample if possible."""
                if raw_time is None:
                    return

                point_time = _parse_saures_datetime(str(raw_time), self.hass)
                if not point_time:
                    return

                value = _normalize_value(raw_value)
                if value is None:
                    return

                samples.append((point_time.timestamp(), value))

            if isinstance(raw_points, list):
                for point in raw_points:
                    try:
                        if isinstance(point, dict):
                            time_field = point.get("time") or point.get("datetime") or point.get("start")
                            value_field = point.get("val") or point.get("value") or point.get("vals")
                            _append_sample(time_field, value_field)
                        elif isinstance(point, (list, tuple)) and len(point) >= 2:
                            _append_sample(point[0], point[1])
                    except Exception as err:  # pylint: disable=broad-except
                        _LOGGER.debug("Failed to parse point %s: %s", point, err)
                        continue

            if isinstance(raw_vals, list):
                if raw_vals and isinstance(raw_vals[0], dict):
                    for entry in raw_vals:
                        try:
                            time_field = entry.get("time") or entry.get("datetime") or entry.get("start")
                            value_field = entry.get("val") or entry.get("value") or entry.get("vals")
                            _append_sample(time_field, value_field)
                        except Exception as err:  # pylint: disable=broad-except
                            _LOGGER.debug("Failed to parse val entry %s: %s", entry, err)
                            continue
                else:
                    if raw_times and len(raw_times) == len(raw_vals):
                        for idx, value in enumerate(raw_vals):
                            time_field = raw_times[idx]
                            _append_sample(time_field, value)
                    else:
                        _LOGGER.debug(
                            "Skipping vals for meter %s: missing or mismatched times (vals=%d, times=%s)",
                            meter_id,
                            len(raw_vals),
                            len(raw_times) if isinstance(raw_times, list) else "none",
                        )

            if not samples:
                _LOGGER.warning(
                    "No valid statistics to import for meter %s. API returned: points=%s (len=%d), vals=%s (len=%s), times=%s (len=%s). "
                    "Check if API response format matches documentation.",
                    meter_id,
                    raw_points is not None,
                    len(raw_points) if isinstance(raw_points, list) else 0,
                    raw_vals is not None,
                    len(raw_vals) if isinstance(raw_vals, list) else 0,
                    raw_times is not None,
                    len(raw_times) if isinstance(raw_times, list) else 0,
                )
                return

            samples.sort(key=lambda item: item[0])

            _LOGGER.info("Parsed %d samples from API for meter %s", len(samples), meter_id)

            # Create metadata according to HA 2025.10+ API changes
            # https://developers.home-assistant.io/blog/2025/10/16/recorder-statistics-api-changes/
            # StatisticMetaData requires: unit_class and mean_type (both mandatory as of Oct 2025)
            metadata = StatisticMetaData(
                has_mean=False,
                has_sum=True,
                name=self._attr_name,
                source=DOMAIN,
                statistic_id=statistic_id,
                unit_of_measurement="m³",
            )
            
            # Add required fields for HA 2025.10+ as dict (StatisticMetaData is TypedDict)
            if isinstance(metadata, dict):
                metadata["unit_class"] = None  # No unit conversion for water meters
                metadata["mean_type"] = 0  # 0 = no mean (for TOTAL_INCREASING sensors)
            else:
                # If StatisticMetaData is not a dict, create as dict
                metadata = {
                    "has_mean": False,
                    "has_sum": True,
                    "name": self._attr_name,
                    "source": DOMAIN,
                    "statistic_id": statistic_id,
                    "unit_of_measurement": "m³",
                    "unit_class": None,  # No unit conversion
                    "mean_type": 0,  # 0 = no mean
                }

            # Home Assistant requires timestamps to be at the top of the hour
            # (minutes and seconds must be 0)
            statistics = [
                StatisticData(
                    start=dt_util.utc_from_timestamp(timestamp).replace(minute=0, second=0, microsecond=0),
                    state=value,
                    sum=value,
                )
                for timestamp, value in samples
            ]

            # Log what we're about to import
            _LOGGER.info(
                "Attempting to import statistics: statistic_id=%s, source=%s, samples=%d, "
                "time_range=%s to %s",
                statistic_id,
                DOMAIN,
                len(statistics),
                statistics[0]["start"].isoformat() if statistics else "N/A",
                statistics[-1]["start"].isoformat() if statistics else "N/A"
            )

            # Import statistics to Home Assistant
            # Pass mean_type parameter for HA 2026.11+ compatibility
            try:
                async_add_external_statistics(self.hass, metadata, statistics, mean_type=None)
            except TypeError:
                # Fallback for older HA versions that don't support mean_type parameter
                async_add_external_statistics(self.hass, metadata, statistics)

            # Update last import time to the newest sample timestamp
            self._last_import_time = statistics[-1]["start"]

            _LOGGER.info(
                "Successfully imported %d statistics for meter %s (statistic_id=%s, source=%s, from %s to %s)",
                len(statistics),
                meter_id,
                statistic_id,
                DOMAIN,
                statistics[0]["start"].isoformat() if statistics else "N/A",
                statistics[-1]["start"].isoformat() if statistics else "N/A"
            )

        except Exception as err:
            _LOGGER.error("Failed to import statistics for meter %s: %s", self._meter["meter_id"], err, exc_info=True)


class SauresAPIDiagnosticSensor(SauresBaseEntity, SensorEntity):
    """API diagnostic sensor for monitoring errors and performance."""
    
    def __init__(self, coordinator, object_id: int, sensor: dict) -> None:
        """Initialize API diagnostic sensor."""
        super().__init__(coordinator, object_id, sensor)
        
        controller_name = f"Контроллер {sensor['sn']}"
        self._attr_unique_id = f"{sensor['sn']}_api_status"
        self._attr_name = f"{controller_name} API Status"
        self._attr_icon = "mdi:api"
        
    @property
    def native_value(self) -> str:
        """Return API status."""
        api_client = self.coordinator.api_client
        stats = api_client.get_error_stats()
        
        # Determine status based on error counts and timing
        total_errors = stats["total_errors"]
        rate_limit_errors = stats["rate_limit_errors"]
        last_error_time = stats.get("last_error_time", 0)
        time_since_error = time.time() - last_error_time if last_error_time > 0 else float('inf')
        
        if total_errors == 0 or time_since_error > 3600:  # No errors or errors older than 1 hour
            return "healthy"
        elif rate_limit_errors > 0 or total_errors > 20:  # Rate limiting or many errors
            return "critical"
        elif total_errors > 5:  # Some errors
            return "warning"
        else:
            return "healthy"
            
    @property
    def extra_state_attributes(self) -> dict:
        """Return API statistics."""
        api_client = self.coordinator.api_client
        stats = api_client.get_error_stats()
        
        # Add time since last error for better monitoring
        last_error_time = stats.get("last_error_time", 0)
        if last_error_time > 0:
            stats["time_since_last_error"] = int(time.time() - last_error_time)
        else:
            stats["time_since_last_error"] = None
            
        return stats


 