"""Climate entity, shared by cloud and local entries."""

from __future__ import annotations

from typing import Any

from homeassistant.components.climate import (
    PRESET_AWAY,
    PRESET_COMFORT,
    ClimateEntity,
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
)
from homeassistant.const import (
    ATTR_TEMPERATURE,
    PRECISION_HALVES,
    PRECISION_TENTHS,
    UnitOfTemperature,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from pyscinan import DeviceMode, DeviceType, ScinanDevice

from .const import DEVICE_MODEL, DEVICE_MODEL_IDS, DEVICE_MODEL_UNKNOWN, DOMAIN, MANUFACTURER
from .coordinator import ScinanConfigEntry, ScinanCoordinator

# The coordinators fetch the data; entity actions go straight to the client.
PARALLEL_UPDATES = 0

PRESET_AUTO = "Auto"
PRESET_DAY_OR_NIGHT = "Day or night"

_PRESET_TO_MODE = {
    PRESET_AUTO: DeviceMode.AUTO,
    PRESET_COMFORT: DeviceMode.COMFORT,
    PRESET_DAY_OR_NIGHT: DeviceMode.DAY_OR_NIGHT,
}
_MODE_TO_PRESET = {mode: preset for preset, mode in _PRESET_TO_MODE.items()}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ScinanConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add a climate entity per thermostat of the entry."""
    coordinator = entry.runtime_data
    async_add_entities(
        ScinanClimate(coordinator, device_id) for device_id in coordinator.data
    )


class ScinanClimate(CoordinatorEntity[ScinanCoordinator], ClimateEntity):
    """A Saswell thermostat."""

    _attr_has_entity_name = True
    _attr_name = None
    # HA 2026.11 renamed the temperature members to native_*; provide both so
    # older HA versions keep working and newer ones log no deprecation warning.
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_native_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_target_temperature_step = PRECISION_HALVES
    # Shown temperatures: type 6 reports 0.1 steps (e.g. 21.4); targets are set in 0.5 steps.
    _attr_precision = PRECISION_TENTHS
    _attr_hvac_modes = [HVACMode.HEAT, HVACMode.OFF]
    _attr_preset_modes = [PRESET_AUTO, PRESET_AWAY, PRESET_COMFORT, PRESET_DAY_OR_NIGHT]
    _attr_supported_features = (
        ClimateEntityFeature.TARGET_TEMPERATURE
        | ClimateEntityFeature.PRESET_MODE
        | ClimateEntityFeature.TURN_ON
        | ClimateEntityFeature.TURN_OFF
    )

    def __init__(self, coordinator: ScinanCoordinator, device_id: str) -> None:
        """Set up the entity for one thermostat."""
        super().__init__(coordinator)
        self._device_id = device_id
        self._attr_unique_id = device_id  # same in cloud and local mode
        device = self.device
        name = device.name or coordinator.config_entry.title
        model_id = DEVICE_MODEL_IDS.get(device.type) if device.type is not None else None
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, device_id)},
            manufacturer=MANUFACTURER,
            model=DEVICE_MODEL if model_id else DEVICE_MODEL_UNKNOWN,
            model_id=model_id,
            serial_number=device_id,
            name=name,
        )

    @property
    def device(self) -> ScinanDevice:
        """The thermostat's current data."""
        return self.coordinator.data[self._device_id]

    @property
    def _off_means_away(self) -> bool:
        """Off is away (frost protection), except on type 6 (WHL), which has a power command.

        Type 9 (FHL) ignores the power command. Type 3 (T13FHL) is also electric floor
        heating; until power off is confirmed there, it uses away too.
        """
        return self.device.type != DeviceType.WHL

    @property
    def available(self) -> bool:
        """Available when the thermostat is online and has reported a status."""
        return (
            super().available
            and self._device_id in self.coordinator.data
            and self.device.online
            and self.device.status is not None
        )

    @property
    def native_current_temperature(self) -> float | None:
        """Measured temperature."""
        return self.device.current_temperature

    @property
    def native_target_temperature(self) -> float | None:
        """Target temperature (05.0 while away)."""
        return self.device.target_temperature

    @property
    def current_temperature(self) -> float | None:  # HA < 2026.11
        """Measured temperature, for HA versions before 2026.11."""
        return self.native_current_temperature

    @property
    def target_temperature(self) -> float | None:  # HA < 2026.11
        """Target temperature, for HA versions before 2026.11."""
        return self.native_target_temperature

    @property
    def min_temp(self) -> float:
        """Lowest target the thermostat accepts."""
        return self.device.min_temp

    @property
    def max_temp(self) -> float:
        """Highest target the thermostat accepts."""
        return self.device.max_temp

    @property
    def hvac_mode(self) -> HVACMode:
        """Heat or Off (Off is away on type 9, power off on type 6)."""
        on = not self.device.away if self._off_means_away else self.device.is_on
        return HVACMode.HEAT if on else HVACMode.OFF

    @property
    def hvac_action(self) -> HVACAction:
        """Estimated heating state; the thermostat doesn't report its relay."""
        # No status field reports the relay; this is an estimate (current < target).
        if self.hvac_mode == HVACMode.OFF:
            return HVACAction.OFF
        return HVACAction.HEATING if self.device.is_heating else HVACAction.IDLE

    @property
    def preset_mode(self) -> str | None:
        """Away, or the thermostat's system mode."""
        if self.device.away:
            return PRESET_AWAY
        mode = self.device.mode
        return _MODE_TO_PRESET.get(mode) if mode is not None else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Connection type and device type."""
        # Only values that rarely change: an attribute that changes on every status
        # push (like a timestamp) would make HA record a new state every 15 seconds.
        return {
            "connection": self.coordinator.connection_type,
            "device_type": self.device.type,
        }

    async def async_set_temperature(self, **kwargs: Any) -> None:
        """Set the target temperature (this also ends away on the thermostat)."""
        if (temperature := kwargs.get(ATTR_TEMPERATURE)) is None:
            return
        # Note: the thermostat also leaves away mode when a temperature is set.
        await self.coordinator.async_set_temperature(self._device_id, float(temperature))

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        """Turn heating on or off."""
        on = hvac_mode == HVACMode.HEAT
        if self._off_means_away:
            await self.coordinator.async_set_away(self._device_id, not on)
        else:
            await self.coordinator.async_set_power(self._device_id, on)

    async def async_turn_on(self) -> None:
        """Turn heating on."""
        await self.async_set_hvac_mode(HVACMode.HEAT)

    async def async_turn_off(self) -> None:
        """Turn heating off."""
        await self.async_set_hvac_mode(HVACMode.OFF)

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        """Set away, or leave away and set a system mode."""
        if preset_mode == PRESET_AWAY:
            await self.coordinator.async_set_away(self._device_id, True)
            return
        if self.device.away:
            await self.coordinator.async_set_away(self._device_id, False)
        await self.coordinator.async_set_mode(self._device_id, _PRESET_TO_MODE[preset_mode])
