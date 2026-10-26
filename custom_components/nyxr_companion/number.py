"""Number platform for NyxR Companion."""
from __future__ import annotations

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.const import UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import NyxrCompanionConfigEntry
from .entity import NyxrCompanionSettingEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: NyxrCompanionConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up NyxR Companion number entities."""
    coordinator = entry.runtime_data
    async_add_entities(
        [
            NyxrCompanionReadSpeedNumber(coordinator),
            NyxrCompanionBufferSecondsNumber(coordinator),
            NyxrCompanionBluetoothBufferNumber(coordinator),
        ]
    )


class NyxrCompanionSettingNumber(NyxrCompanionSettingEntity, NumberEntity):
    """Number entity for one of the daemon's numeric settings."""

    _attr_native_step = 1.0
    _integer = True  # the daemon wants an int, not a float

    @property
    def native_value(self) -> float | None:
        """Return the current value."""
        value = self.setting_value
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        return value

    async def async_set_native_value(self, value: float) -> None:
        """Change the setting."""
        await self.async_set_setting(int(value) if self._integer else float(value))


class NyxrCompanionReadSpeedNumber(NyxrCompanionSettingNumber):
    """Cap on the drive's spin speed."""

    _setting = "read_speed"
    _attr_translation_key = "read_speed"
    _attr_icon = "mdi:speedometer"
    _attr_native_min_value = 1
    _attr_native_max_value = 24
    _attr_native_unit_of_measurement = "x"
    _attr_mode = NumberMode.BOX


class NyxrCompanionBufferSecondsNumber(NyxrCompanionSettingNumber):
    """Audio read ahead of playback."""

    _setting = "buffer_seconds"
    _integer = False
    _attr_translation_key = "buffer_seconds"
    _attr_icon = "mdi:buffer"
    _attr_native_min_value = 2
    _attr_native_max_value = 60
    _attr_native_unit_of_measurement = UnitOfTime.SECONDS
    _attr_mode = NumberMode.BOX


class NyxrCompanionBluetoothBufferNumber(NyxrCompanionSettingNumber):
    """Audio kept buffered from Bluetooth, which arrives in bursts."""

    _setting = "bluetooth_buffer_ms"
    _attr_translation_key = "bluetooth_buffer_ms"
    _attr_icon = "mdi:buffer"
    _attr_native_min_value = 20
    _attr_native_max_value = 2000
    _attr_native_step = 10.0
    _attr_native_unit_of_measurement = UnitOfTime.MILLISECONDS
    _attr_mode = NumberMode.BOX
