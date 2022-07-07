"""Scinan climates."""
import logging

from homeassistant.components.climate import (
    ClimateEntity,
    HVACMode,
)
from homeassistant.components.climate.const import (
    ClimateEntityFeature,
    HVACAction, PRESET_COMFORT,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    ATTR_TEMPERATURE,
    CONF_USERNAME, PRECISION_HALVES, TEMP_CELSIUS,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER, MAX_TEMP, MIN_TEMP
from .data_coordinator import ScinanDataUpdateCoordinator
from .scinan import ScinanDevice, ScinanDeviceMode

_LOGGER = logging.getLogger(__name__)

CLIMATE_PRESET_AUTO = "Auto"
CLIMATE_PRESET_DAY_OR_NIGHT = "Day or night"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback
) -> None:
    """Set up Scinan climate."""
    username = entry.data[CONF_USERNAME]
    scinan_data_coordinator: ScinanDataUpdateCoordinator = hass.data[DOMAIN][
        username
    ]

    entities = [
        ScinanClimate(scinan_data_coordinator, device)
        for device in scinan_data_coordinator.data.values()
    ]
    async_add_entities(entities)


# noinspection PyAbstractClass
class ScinanClimate(CoordinatorEntity, ClimateEntity):
    """Representation of Scinan climate device."""
    _attr_max_temp = MAX_TEMP
    _attr_min_temp = MIN_TEMP
    _attr_temperature_unit = TEMP_CELSIUS
    _attr_target_temperature_step = PRECISION_HALVES
    _attr_supported_features = (
        ClimateEntityFeature.TARGET_TEMPERATURE
        | ClimateEntityFeature.PRESET_MODE
    )
    _attr_preset_modes = [
        CLIMATE_PRESET_AUTO,
        PRESET_COMFORT,
        CLIMATE_PRESET_DAY_OR_NIGHT,
    ]
    """Off represents away"""
    _attr_hvac_modes = [HVACMode.HEAT, HVACMode.OFF]

    coordinator: ScinanDataUpdateCoordinator

    def __init__(
        self,
        coordinator: ScinanDataUpdateCoordinator,
        device: ScinanDevice
    ):
        """Initiate the thermostat."""
        super().__init__(coordinator)

        self._id = device.device_id
        self._attr_unique_id = device.device_id
        self._attr_name = device.name
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, device.device_id)},
            manufacturer=MANUFACTURER,
            model="WiFi Thermostat",
            name=device.name
        )

        self._update_attr(device)

    @property
    def device(self) -> ScinanDevice:
        """Return the Scinan device from data coordinator."""
        return self.coordinator.data[self._id]

    @property
    def available(self) -> bool:
        """Return True if entity is available and online."""
        return super().available and self.device.online

    @property
    def current_temperature(self) -> float:
        """Return the current temperature."""
        return self.device.measure_temperature

    @property
    def target_temperature(self) -> float:
        """Return the temperature we try to reach."""
        return self.device.target_temperature

    @property
    def hvac_mode(self) -> HVACMode:
        """Return device HVAC mode."""
        return (
            HVACMode.OFF
            if self.is_away else
            HVACMode.HEAT
        )

    @property
    def hvac_action(self) -> HVACAction:
        """Return the device HVAC operation."""
        return (
            HVACAction.HEATING
            if self.device.is_heating else
            HVACAction.IDLE
        )

    @property
    def preset_mode(self) -> str:
        """Return device preset mode."""
        if self.device.mode == ScinanDeviceMode.COMFORT:
            return PRESET_COMFORT
        if self.device.mode == ScinanDeviceMode.DAY_OR_NIGHT:
            return CLIMATE_PRESET_DAY_OR_NIGHT
        return CLIMATE_PRESET_AUTO

    @property
    def is_away(self) -> bool:
        """Return True if device is away."""
        return self.device.away

    async def async_set_temperature(self, **kwargs) -> None:
        """Set new target temperatures."""
        if (temperature := kwargs.get(ATTR_TEMPERATURE)) is None:
            return

        await self.coordinator.api_wrapper(
            self.coordinator.scinan_api.set_temperature(
                self._id,
                float(temperature)
            ),
            # no need to refresh data
        )
        # state has been updated
        await self.async_update_ha_state()

    async def async_turn_on(self) -> None:
        """Turn the device on. Using the away feature to turn on."""
        await self.coordinator.api_wrapper(
            self.coordinator.scinan_api.set_home_away(self._id, False),
            True,
        )

    async def async_turn_off(self) -> None:
        """Turn the device off. Using the away feature to turn off."""
        await self.coordinator.api_wrapper(
            self.coordinator.scinan_api.set_home_away(self._id, True),
            True,
        )

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        """Set new target hvac mode."""
        await self.coordinator.api_wrapper(
            self.coordinator.scinan_api.set_home_away(
                self._id,
                hvac_mode == HVACMode.OFF
            ),
            True,
        )

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        """Set new preset mode."""
        mode = ScinanDeviceMode.AUTO
        if preset_mode == PRESET_COMFORT:
            mode = ScinanDeviceMode.COMFORT
        elif preset_mode == CLIMATE_PRESET_DAY_OR_NIGHT:
            mode = ScinanDeviceMode.DAY_OR_NIGHT

        await self.coordinator.api_wrapper(
            self.coordinator.scinan_api.set_mode(self._id, mode),
            True,
        )

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        self._update_attr(self.coordinator.data[self._id])
        self.async_write_ha_state()

    @callback
    def _update_attr(self, device: ScinanDevice) -> None:
        self._attr_extra_state_attributes = {
            "is_heating": device.is_heating,
            "last_updated": device.last_updated,
            "scinan_id": device.device_id,
            "scinan_name": device.name,
            "scinan_type": device.type,
            "scinan_company_id": device.company_id,
            "scinan_actual_mode": device.actual_mode,
        }
