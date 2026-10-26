"""Button platform for NyxR Companion."""
from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import NyxrCompanionConfigEntry, NyxrCompanionCoordinator
from .entity import NyxrCompanionBluetoothEntity, NyxrCompanionEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: NyxrCompanionConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up NyxR Companion button entities."""
    coordinator = entry.runtime_data
    async_add_entities(
        [
            NyxrCompanionEjectButton(coordinator),
            NyxrCompanionBluetoothButton(coordinator, "bluetooth_disconnect", "mdi:bluetooth-off"),
        ]
    )


class NyxrCompanionEjectButton(NyxrCompanionEntity, ButtonEntity):
    """Button entity to eject CD."""

    _attr_translation_key = "eject"
    _attr_icon = "mdi:eject"

    def __init__(self, coordinator: NyxrCompanionCoordinator) -> None:
        """Initialize the button."""
        super().__init__(coordinator, "eject")

    async def async_press(self) -> None:
        """Handle button press to eject CD."""
        await self.coordinator.async_send_command("/api/eject", {})


class NyxrCompanionBluetoothButton(NyxrCompanionBluetoothEntity, ButtonEntity):
    """Disconnects the connected device, or forgets all paired ones."""

    def __init__(self, coordinator: NyxrCompanionCoordinator, key: str, icon: str) -> None:
        """Initialize the button."""
        super().__init__(coordinator, key)
        self._attr_translation_key = key
        self._attr_icon = icon
        self._path = "/api/bluetooth/" + key.removeprefix("bluetooth_")

    async def async_press(self) -> None:
        """Send the command."""
        await self.coordinator.async_send_command(self._path, {})
