"""Switch platform for NyxR Companion."""
from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import NyxrCompanionConfigEntry, NyxrCompanionCoordinator
from .entity import NyxrCompanionBluetoothEntity, NyxrCompanionEntity, NyxrCompanionSettingEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: NyxrCompanionConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up NyxR Companion switch entities."""
    coordinator = entry.runtime_data
    async_add_entities(
        [
            NyxrCompanionStreamSwitch(coordinator),
            NyxrCompanionBluetoothPairingSwitch(coordinator),
            NyxrCompanionBluetoothAutoSwitch(coordinator),
            NyxrCompanionAirPlayAutoSwitch(coordinator),
            NyxrCompanionCdAutoPlaySwitch(coordinator),
        ]
    )


class NyxrCompanionStreamSwitch(NyxrCompanionEntity, SwitchEntity):
    """Switch to control NyxR Companion streaming."""

    _attr_translation_key = "stream"
    _attr_icon = "mdi:cast-audio"

    def __init__(self, coordinator: NyxrCompanionCoordinator) -> None:
        """Initialize the switch."""
        super().__init__(coordinator, "stream")

    @property
    def is_on(self) -> bool:
        """Return True if streaming is active."""
        if self.coordinator.data is None:
            return False
        return bool(self.coordinator.data.get("streaming", False))

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn on streaming."""
        await self.coordinator.async_send_command("/api/stream", {"on": True})

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn off streaming."""
        await self.coordinator.async_send_command("/api/stream", {"on": False})


class NyxrCompanionBluetoothPairingSwitch(NyxrCompanionBluetoothEntity, SwitchEntity):
    """Pairing window of the Bluetooth receiver: on for 3 minutes, or until a device pairs."""

    _attr_translation_key = "bluetooth_pairing"
    _attr_icon = "mdi:bluetooth-connect"

    def __init__(self, coordinator: NyxrCompanionCoordinator) -> None:
        """Initialize the switch."""
        super().__init__(coordinator, "bluetooth_pairing")

    @property
    def is_on(self) -> bool:
        """Return True while new devices can pair."""
        return bool(self.bluetooth.get("pairing"))

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Open the pairing window."""
        await self.coordinator.async_send_command("/api/bluetooth/pairing", {"on": True})

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Close the pairing window."""
        await self.coordinator.async_send_command("/api/bluetooth/pairing", {"on": False})


class NyxrCompanionBluetoothAutoSwitch(NyxrCompanionSettingEntity, SwitchEntity):
    """Switch to Bluetooth when a connected device starts playing."""

    _setting = "bluetooth_auto_switch"
    _attr_translation_key = "bluetooth_auto_switch"
    _attr_icon = "mdi:bluetooth-audio"

    @property
    def is_on(self) -> bool:
        """Return True if switching automatically."""
        return bool(self.setting_value)

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Switch automatically."""
        await self.async_set_setting(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Do not switch automatically."""
        await self.async_set_setting(False)


class NyxrCompanionAirPlayAutoSwitch(NyxrCompanionBluetoothAutoSwitch):
    """Switch to AirPlay when a sender starts playing."""

    _setting = "airplay_auto_switch"
    _attr_translation_key = "airplay_auto_switch"
    _attr_icon = "mdi:cast-variant"


class NyxrCompanionCdAutoPlaySwitch(NyxrCompanionBluetoothAutoSwitch):
    """Switch to the CD and play a disc when it is put in."""

    _setting = "cd_auto_play"
    _attr_translation_key = "cd_auto_play"
    _attr_icon = "mdi:disc-player"
