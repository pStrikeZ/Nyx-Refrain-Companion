"""Select platform for NyxR Companion."""
from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import LATENCY_PROFILES, PLAY_MODES, READ_MODES, SOURCES
from .coordinator import NyxrCompanionConfigEntry, NyxrCompanionCoordinator
from .entity import (
    NyxrCompanionBluetoothEntity,
    NyxrCompanionEntity,
    NyxrCompanionSettingEntity,
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: NyxrCompanionConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up NyxR Companion select entities."""
    coordinator = entry.runtime_data
    async_add_entities(
        [
            NyxrCompanionSourceSelect(coordinator),
            NyxrCompanionBluetoothPairSelect(coordinator),
            NyxrCompanionBluetoothForgetSelect(coordinator),
            NyxrCompanionPlayModeSelect(coordinator),
            NyxrCompanionReadModeSelect(coordinator),
            NyxrCompanionLatencyProfileSelect(coordinator),
            NyxrCompanionReceiverSelect(coordinator),
        ]
    )


class NyxrCompanionSourceSelect(NyxrCompanionEntity, SelectEntity):
    """Select entity for what is streamed: the CD, Bluetooth or AirPlay."""

    _attr_translation_key = "source"
    _attr_icon = "mdi:import"
    _attr_options = SOURCES

    def __init__(self, coordinator: NyxrCompanionCoordinator) -> None:
        """Initialize the select entity."""
        super().__init__(coordinator, "source")

    @property
    def current_option(self) -> str | None:
        """Return the selected source."""
        if self.coordinator.data is None:
            return None
        source = self.coordinator.data.get("source")
        return str(source) if source in self._attr_options else None

    async def async_select_option(self, option: str) -> None:
        """Change the source."""
        await self.coordinator.async_send_command("/api/source", {"source": option})


class NyxrCompanionBluetoothPairSelect(NyxrCompanionBluetoothEntity, SelectEntity):
    """Pairs with a nearby device found while the pairing window is open.

    For devices whose own pairing request does not reach the receiver: picking one makes
    the receiver call it instead."""

    _attr_translation_key = "bluetooth_pair"
    _attr_icon = "mdi:bluetooth-connect"

    def __init__(self, coordinator: NyxrCompanionCoordinator) -> None:
        """Initialize the select entity."""
        super().__init__(coordinator, "bluetooth_pair")

    def _nearby(self) -> dict[str, str]:
        """Return option label -> address for the nearby devices."""
        nearby = self.bluetooth.get("nearby")
        if not isinstance(nearby, list):
            return {}
        return {
            f"{d.get('name')} ({d.get('address')})": str(d.get("address"))
            for d in nearby
            if isinstance(d, dict) and d.get("address")
        }

    @property
    def options(self) -> list[str]:
        """Return the nearby devices."""
        return list(self._nearby())

    @property
    def current_option(self) -> str | None:
        """Nothing stays selected: picking a device starts pairing."""
        return None

    @property
    def available(self) -> bool:
        """Return True while there are devices to pair with."""
        return super().available and bool(self._nearby())

    async def async_select_option(self, option: str) -> None:
        """Pair with the picked device."""
        address = self._nearby().get(option)
        if address is not None:
            await self.coordinator.async_send_command(
                "/api/bluetooth/pair", {"address": address}, timeout=60
            )


class NyxrCompanionBluetoothForgetSelect(NyxrCompanionBluetoothEntity, SelectEntity):
    """Removes the pairing with one of the paired devices.

    A device that removed the pairing on its side is forgotten by the add-on itself when it
    tries to pair again; this is for devices you no longer want."""

    _attr_translation_key = "bluetooth_forget"
    _attr_icon = "mdi:bluetooth-off"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: NyxrCompanionCoordinator) -> None:
        """Initialize the select entity."""
        super().__init__(coordinator, "bluetooth_forget_device")

    def _paired(self) -> dict[str, str]:
        """Return option label -> address for the paired devices."""
        devices = self.bluetooth.get("devices")
        if not isinstance(devices, list):
            return {}
        return {
            f"{d.get('name')} ({d.get('address')})": str(d.get("address"))
            for d in devices
            if isinstance(d, dict) and d.get("address")
        }

    @property
    def options(self) -> list[str]:
        """Return the paired devices."""
        return list(self._paired())

    @property
    def current_option(self) -> str | None:
        """Nothing stays selected: picking a device forgets it."""
        return None

    @property
    def available(self) -> bool:
        """Return True while there are paired devices."""
        return super().available and bool(self._paired())

    async def async_select_option(self, option: str) -> None:
        """Forget the picked device."""
        address = self._paired().get(option)
        if address is not None:
            await self.coordinator.async_send_command(
                "/api/bluetooth/forget", {"address": address}
            )


class NyxrCompanionPlayModeSelect(NyxrCompanionEntity, SelectEntity):
    """Select entity for NyxR Companion play mode."""

    _attr_translation_key = "play_mode"
    _attr_icon = "mdi:repeat"
    _attr_options = PLAY_MODES

    def __init__(self, coordinator: NyxrCompanionCoordinator) -> None:
        """Initialize the select entity."""
        super().__init__(coordinator, "play_mode")

    @property
    def current_option(self) -> str | None:
        """Return the current selected play mode."""
        if self.coordinator.data is None:
            return None
        mode = self.coordinator.data.get("mode")
        if mode in self._attr_options:
            return str(mode)
        return None

    async def async_select_option(self, option: str) -> None:
        """Change the play mode."""
        await self.coordinator.async_send_command("/api/mode", {"mode": option})


class NyxrCompanionSettingSelect(NyxrCompanionSettingEntity, SelectEntity):
    """Select entity for one of the daemon's settings."""

    @property
    def current_option(self) -> str | None:
        """Return the current option."""
        value = self.setting_value
        return value if value in self._attr_options else None

    async def async_select_option(self, option: str) -> None:
        """Change the setting."""
        await self.async_set_setting(option)


class NyxrCompanionReadModeSelect(NyxrCompanionSettingSelect):
    """Select entity for how the drive is read."""

    _setting = "read_mode"
    _attr_translation_key = "read_mode"
    _attr_icon = "mdi:disc"
    _attr_options = READ_MODES


class NyxrCompanionLatencyProfileSelect(NyxrCompanionSettingSelect):
    """Select entity for nyxr's latency profile."""

    _setting = "latency_profile"
    _attr_translation_key = "latency_profile"
    _attr_icon = "mdi:timer-outline"
    _attr_options = LATENCY_PROFILES


class NyxrCompanionReceiverSelect(NyxrCompanionSettingSelect):
    """AirPlay receiver to stream to, picked from the ones found on the network.

    The target text entity takes any address; this lists what the add-on found."""

    _setting = "target"
    _attr_translation_key = "receiver"
    _attr_icon = "mdi:speaker-wireless"

    def _choices(self) -> dict[str, str]:
        """Return option label -> address, including the current target."""
        receivers = (self.coordinator.data or {}).get("receivers")
        choices = {
            f"{r.get('name')} ({r.get('address')})": str(r.get("address"))
            for r in receivers or []
            if isinstance(r, dict) and r.get("address")
        }
        target = self.setting_value
        if isinstance(target, str) and target not in choices.values():
            choices[target] = target
        return choices

    @property
    def options(self) -> list[str]:
        """Return the receivers found."""
        return list(self._choices())

    @property
    def current_option(self) -> str | None:
        """Return the receiver streamed to."""
        target = self.setting_value
        return next(
            (label for label, address in self._choices().items() if address == target),
            None,
        )

    @property
    def available(self) -> bool:
        """Return True while there is something to pick."""
        return NyxrCompanionEntity.available.fget(self) and bool(self._choices())

    async def async_select_option(self, option: str) -> None:
        """Stream to the picked receiver."""
        address = self._choices().get(option)
        if address is not None:
            await self.async_set_setting(address)
