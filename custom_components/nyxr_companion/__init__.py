"""The NyxR Companion integration."""
from __future__ import annotations

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from .const import DOMAIN
from .coordinator import NyxrCompanionConfigEntry, NyxrCompanionCoordinator

PLATFORMS: list[Platform] = [
    Platform.SWITCH,
    Platform.SELECT,
    Platform.BUTTON,
    Platform.SENSOR,
    Platform.NUMBER,
    Platform.TEXT,
]


async def async_setup_entry(hass: HomeAssistant, entry: NyxrCompanionConfigEntry) -> bool:
    """Set up NyxR Companion from a config entry."""
    coordinator = NyxrCompanionCoordinator(hass, entry)
    await coordinator.async_setup()

    entry.runtime_data = coordinator

    entry.async_create_background_task(
        hass, coordinator.async_run_ws(), name=f"{DOMAIN}_ws_{entry.unique_id}"
    )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: NyxrCompanionConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        await entry.runtime_data.async_close()
    return unload_ok
