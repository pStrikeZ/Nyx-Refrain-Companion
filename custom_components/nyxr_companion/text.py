"""Text platform for NyxR Companion."""
from __future__ import annotations

from homeassistant.components.text import TextEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import NyxrCompanionConfigEntry
from .entity import NyxrCompanionEntity, NyxrCompanionSettingEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: NyxrCompanionConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up NyxR Companion text entities."""
    coordinator = entry.runtime_data
    async_add_entities(
        [
            NyxrCompanionTargetText(coordinator),
            NyxrCompanionBluetoothNameText(coordinator),
            NyxrCompanionAirPlayNameText(coordinator),
        ]
    )


class NyxrCompanionTargetText(NyxrCompanionSettingEntity, TextEntity):
    """AirPlay 2 receiver that nyxr streams to."""

    _setting = "target"
    _attr_translation_key = "target"
    _attr_icon = "mdi:speaker-wireless"
    _attr_native_max = 64

    @property
    def available(self) -> bool:
        """Return True while connected, also before a target is set (it starts unset)."""
        settings = (self.coordinator.data or {}).get("settings")
        return (
            NyxrCompanionEntity.available.fget(self)
            and isinstance(settings, dict)
            and "target" in settings
        )

    @property
    def native_value(self) -> str | None:
        """Return the current target."""
        value = self.setting_value
        return value if isinstance(value, str) else None

    async def async_set_value(self, value: str) -> None:
        """Change the target."""
        await self.async_set_setting(value)


class NyxrCompanionBluetoothNameText(NyxrCompanionSettingEntity, TextEntity):
    """Name the Bluetooth receiver shows to phones."""

    _setting = "bluetooth_name"
    _attr_translation_key = "bluetooth_name"
    _attr_icon = "mdi:bluetooth"
    _attr_native_min = 1
    _attr_native_max = 64

    @property
    def native_value(self) -> str | None:
        """Return the current name."""
        value = self.setting_value
        return value if isinstance(value, str) else None

    async def async_set_value(self, value: str) -> None:
        """Change the name."""
        await self.async_set_setting(value)


class NyxrCompanionAirPlayNameText(NyxrCompanionBluetoothNameText):
    """Name the AirPlay receiver shows in the AirPlay list."""

    _setting = "airplay_name"
    _attr_translation_key = "airplay_name"
    _attr_icon = "mdi:cast-variant"
