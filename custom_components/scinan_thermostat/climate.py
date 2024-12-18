"""Scinan climates."""
import logging

from homeassistant.components.climate import (
    ClimateEntity,
    HVACMode,
)
from homeassistant.components.climate.const import (
    ClimateEntityFeature,
    HVACAction,
    PRESET_AWAY,
    PRESET_COMFORT,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    ATTR_TEMPERATURE,
    CONF_USERNAME,
    PRECISION_HALVES,
    UnitOfTemperature,
)
from homeassistant.core import (
    callback,
    HomeAssistant,
)
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import (
    DEVICE_TEMP_MAX,
    DEVICE_TEMP_MIN,
    DEVICE_TYPE_FHL_THERMOSTAT,
    ScinanDevice,
    ScinanDeviceMode,
)
from .const import (
    DOMAIN,
    MANUFACTURER,
)
from .data_coordinator import ScinanDataUpdateCoordinator

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
    _attr_max_temp = DEVICE_TEMP_MAX
    _attr_min_temp = DEVICE_TEMP_MIN
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_target_temperature_step = PRECISION_HALVES
    _attr_supported_features = (
        ClimateEntityFeature.TARGET_TEMPERATURE
        | ClimateEntityFeature.PRESET_MODE
        | ClimateEntityFeature.TURN_ON
        | ClimateEntityFeature.TURN_OFF
    )
    _attr_preset_modes = [
        CLIMATE_PRESET_AUTO,
        PRESET_AWAY,
        PRESET_COMFORT,
        CLIMATE_PRESET_DAY_OR_NIGHT,
    ]
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
            HVACMode.HEAT
            if self.is_on else
            HVACMode.OFF
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
        if self.is_away:
            return PRESET_AWAY
        if self.device.mode == ScinanDeviceMode.COMFORT:
            return PRESET_COMFORT
        if self.device.mode == ScinanDeviceMode.DAY_OR_NIGHT:
            return CLIMATE_PRESET_DAY_OR_NIGHT
        return CLIMATE_PRESET_AUTO

    @property
    def is_away(self) -> bool:
        """Return True if device is away."""
        return self.device.away

    @property
    def is_on(self) -> bool:
        """Return True if device is on."""
        if self.device.type is DEVICE_TYPE_FHL_THERMOSTAT:
            return not self.device.away

        return self.device.is_on

    async def async_set_temperature(self, **kwargs) -> None:
        """Set new target temperatures."""
        if (temperature := kwargs.get(ATTR_TEMPERATURE)) is None:
            return

        was_away = self.is_away
        await self.coordinator.api_wrapper(
            self.coordinator.scinan_api.set_temperature(
                self._id,
                float(temperature)
            ),
            # changing temperature when away will turn off away mode
            # refresh device data if it was away
            was_away
        )
        if not was_away:
            self.async_write_ha_state()

    async def async_turn_on(self) -> None:
        """Turn the device on."""
        if self.device.type is DEVICE_TYPE_FHL_THERMOSTAT:
            return await self.coordinator.api_wrapper(
                self.coordinator.scinan_api.set_home_away(self._id, False),
                True,
            )

        await self.coordinator.api_wrapper(
            self.coordinator.scinan_api.set_on_off(self._id, True),
            True,
        )

    async def async_turn_off(self) -> None:
        """Turn the device off."""

        if self.device.type is DEVICE_TYPE_FHL_THERMOSTAT:
            return await self.coordinator.api_wrapper(
                self.coordinator.scinan_api.set_home_away(self._id, True),
                True,
            )

        await self.coordinator.api_wrapper(
            self.coordinator.scinan_api.set_on_off(self._id, False),
            True,
        )

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        """Set new target hvac mode."""
        if self.device.type is DEVICE_TYPE_FHL_THERMOSTAT:
            return await self.coordinator.api_wrapper(
                self.coordinator.scinan_api.set_home_away(self._id, hvac_mode == HVACMode.OFF),
                True,
            )

        await self.coordinator.api_wrapper(
            self.coordinator.scinan_api.set_on_off(self._id, hvac_mode == HVACMode.HEAT),
            True,
        )

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        """Set new preset mode."""
        if preset_mode == PRESET_AWAY:
            await self.coordinator.api_wrapper(
                self.coordinator.scinan_api.set_home_away(
                    self._id,
                    True,
                ),
                True,
            )
        else:
            if self.is_away:
                await self.coordinator.api_wrapper(
                    self.coordinator.scinan_api.set_home_away(
                        self._id,
                        False,
                    ),
                    False,
                )
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
