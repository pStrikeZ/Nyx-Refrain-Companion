"""Base entity for the NyxR Companion integration."""
from __future__ import annotations

from typing import Any

from homeassistant.const import EntityCategory
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .coordinator import NyxrCompanionCoordinator


class NyxrCompanionEntity(CoordinatorEntity[NyxrCompanionCoordinator]):
    """Base entity for NyxR Companion entities."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: NyxrCompanionCoordinator, key: str) -> None:
        """Initialize the base entity."""
        super().__init__(coordinator)
        unique_id_base = (
            coordinator.config_entry.unique_id
            or f"{coordinator.host}:{coordinator.port}"
        )
        self._attr_unique_id = f"{unique_id_base}_{key}"
        self._attr_device_info = coordinator.device_info

    @property
    def available(self) -> bool:
        """Return True if entity is available."""
        return self.coordinator.connected and super().available


class NyxrCompanionBluetoothEntity(NyxrCompanionEntity):
    """Base for entities about the Bluetooth receiver."""

    @property
    def bluetooth(self) -> dict[str, Any]:
        """Return the daemon's Bluetooth state."""
        bt = (self.coordinator.data or {}).get("bluetooth")
        return bt if isinstance(bt, dict) else {}

    @property
    def available(self) -> bool:
        """Return True if the daemon can reach the host's Bluetooth."""
        return super().available and bool(self.bluetooth.get("available"))


class NyxrCompanionAirPlayEntity(NyxrCompanionEntity):
    """Base for entities about the AirPlay receiver."""

    @property
    def airplay(self) -> dict[str, Any]:
        """Return the daemon's AirPlay state."""
        airplay = (self.coordinator.data or {}).get("airplay")
        return airplay if isinstance(airplay, dict) else {}

    @property
    def available(self) -> bool:
        """Return True while the AirPlay receiver runs."""
        return super().available and bool(self.airplay.get("available"))


class NyxrCompanionSettingEntity(NyxrCompanionEntity):
    """Base for entities that show and change one of the daemon's settings."""

    _attr_entity_category = EntityCategory.CONFIG
    _setting: str

    def __init__(self, coordinator: NyxrCompanionCoordinator) -> None:
        """Initialize the setting entity."""
        super().__init__(coordinator, self._setting)

    @property
    def setting_value(self) -> Any:
        """Return the setting's current value, or None if unknown."""
        settings = (self.coordinator.data or {}).get("settings")
        if not isinstance(settings, dict):
            return None
        return settings.get(self._setting)

    @property
    def available(self) -> bool:
        """Return True if the daemon reports this setting."""
        return super().available and self.setting_value is not None

    async def async_set_setting(self, value: Any) -> None:
        """Change the setting on the daemon."""
        await self.coordinator.async_send_command("/api/settings", {self._setting: value})
