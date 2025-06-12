"""Saures sensors."""
from __future__ import annotations

import logging
import time

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.const import (
    PERCENTAGE,
    UnitOfVolume,
    SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
)

from .const import DOMAIN, DEVICE_STATES

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Saures sensors from a config entry."""
    
    data = hass.data[DOMAIN][config_entry.entry_id]
    coordinator = data["coordinator"]
    
    @callback
    def _add_entities():
        """Add entities when coordinator data becomes available."""
        if not coordinator.data:
            _LOGGER.debug("No coordinator data available yet")
            return
            
        entities = []
        
        _LOGGER.debug("Creating sensors from coordinator data...")
        for object_id, object_data in coordinator.data["objects"].items():
            for sensor in object_data["sensors"]:
                # Add controller sensors
                entities.extend([
                    SauresBatterySensor(coordinator, object_id, sensor),
                    SauresRSSISensor(coordinator, object_id, sensor),
                    SauresAPIDiagnosticSensor(coordinator, object_id, sensor),
                ])
                
                # Add meter sensors
                for meter in sensor.get("meters", []):
                    meter_type = meter.get("type", {}).get("number")
                    
                    if meter_type in [1, 2]:  # Water meters only
                        entities.append(
                            SauresWaterMeterSensor(coordinator, object_id, sensor, meter)
                        )
        
        if entities:
            _LOGGER.info("Adding %d sensors to Home Assistant", len(entities))
            async_add_entities(entities)
        else:
            _LOGGER.warning("No sensors created - check API data")
    
    # Try to add entities immediately if data exists
    if coordinator.data:
        _LOGGER.debug("Coordinator data available, adding sensors immediately")
        _add_entities()
    else:
        _LOGGER.debug("Coordinator data not ready, setting up listener")
        # Add listener for when data becomes available
        @callback
        def _data_updated():
            if coordinator.data:
                _LOGGER.debug("Coordinator data became available, adding sensors")
                # Remove this listener after first successful execution
                remove_listener()
                _add_entities()
        
        remove_listener = coordinator.async_add_listener(_data_updated)


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


class SauresWaterMeterSensor(SauresBaseEntity, SensorEntity):
    """Water meter sensor."""
    
    def __init__(self, coordinator, object_id: int, sensor: dict, meter: dict) -> None:
        """Initialize water meter sensor."""
        super().__init__(coordinator, object_id, sensor)
        self._meter = meter
        
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


 