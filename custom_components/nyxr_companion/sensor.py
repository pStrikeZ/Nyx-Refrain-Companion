"""Sensor platform for NyxR Companion."""
from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import STATUS_OPTIONS
from .coordinator import NyxrCompanionConfigEntry, NyxrCompanionCoordinator
from .entity import (
    NyxrCompanionAirPlayEntity,
    NyxrCompanionBluetoothEntity,
    NyxrCompanionEntity,
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: NyxrCompanionConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up NyxR Companion sensor entities."""
    coordinator = entry.runtime_data
    async_add_entities(
        [
            NyxrCompanionStatusSensor(coordinator),
            NyxrCompanionBluetoothSensor(coordinator),
            NyxrCompanionAirPlaySensor(coordinator),
        ]
    )


class NyxrCompanionStatusSensor(NyxrCompanionEntity, SensorEntity):
    """Sensor entity for NyxR Companion status and metadata."""

    _attr_translation_key = "status"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_icon = "mdi:disc-player"
    _attr_options = STATUS_OPTIONS

    def __init__(self, coordinator: NyxrCompanionCoordinator) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, "status")

    @property
    def native_value(self) -> str | None:
        """Return the CD player status enum value."""
        if self.coordinator.data is None:
            return None
        drive = self.coordinator.data.get("drive")
        if drive != "ok":
            return str(drive) if drive in STATUS_OPTIONS else None
        playback = self.coordinator.data.get("playback")
        return str(playback) if playback in STATUS_OPTIONS else None

    @property
    def entity_picture(self) -> str | None:
        """Return the cover art URL as entity picture."""
        if self.coordinator.data is None:
            return None
        disc = self.coordinator.data.get("disc")
        if isinstance(disc, dict):
            cover_url = disc.get("cover_url")
            if cover_url:
                return str(cover_url)
        return None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return attributes representing disc and playback information."""
        if self.coordinator.data is None:
            return {}
        data = self.coordinator.data
        disc = data.get("disc")
        if not isinstance(disc, dict):
            disc = None

        track_num = data.get("track")
        album = disc.get("album") if disc else None
        artist = disc.get("artist") if disc else None

        track_title: str | None = None
        track_artist: str | None = None
        track_count: int | None = None
        tracks_list: list[dict[str, Any]] = []

        if disc is not None:
            raw_tracks = disc.get("tracks")
            if isinstance(raw_tracks, list):
                track_count = len(raw_tracks)
                for item in raw_tracks:
                    if isinstance(item, dict):
                        t_num = item.get("number")
                        t_title = item.get("title")
                        t_len = item.get("length")
                        tracks_list.append(
                            {
                                "number": t_num,
                                "title": t_title,
                                "length": t_len,
                            }
                        )
                        if track_num is not None and t_num == track_num:
                            track_title = t_title
                            track_artist = item.get("artist") or artist

        return {
            "album": album,
            "artist": artist,
            "track": track_num,
            "track_title": track_title,
            "track_artist": track_artist,
            "track_count": track_count,
            "position": data.get("position"),
            "source": data.get("source"),
            "mode": data.get("mode"),
            "streaming": data.get("streaming"),
            "error": data.get("error"),
            "tracks": tracks_list,
        }


class NyxrCompanionBluetoothSensor(NyxrCompanionBluetoothEntity, SensorEntity):
    """Device connected to the Bluetooth receiver, with the paired ones as attributes."""

    _attr_translation_key = "bluetooth_device"
    _attr_icon = "mdi:bluetooth"

    def __init__(self, coordinator: NyxrCompanionCoordinator) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, "bluetooth_device")

    @property
    def native_value(self) -> str | None:
        """Return the connected device's name, or None."""
        connected = self.bluetooth.get("connected")
        return str(connected) if connected else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the receiver's state and the paired devices."""
        bt = self.bluetooth
        track = bt.get("track") if isinstance(bt.get("track"), dict) else {}
        devices = bt.get("devices") if isinstance(bt.get("devices"), list) else []
        return {
            "name": bt.get("name"),
            "pairing": bt.get("pairing"),
            "playing": bt.get("playing"),
            "streaming": bt.get("streaming"),
            "codec": bt.get("codec"),
            "rate": bt.get("rate"),
            "track_title": track.get("title"),
            "track_artist": track.get("artist"),
            "paired_devices": [d.get("name") for d in devices if isinstance(d, dict)],
            "error": bt.get("error"),
        }


class NyxrCompanionAirPlaySensor(NyxrCompanionAirPlayEntity, SensorEntity):
    """Sender connected to the AirPlay receiver, with what it plays as attributes."""

    _attr_translation_key = "airplay_sender"
    _attr_icon = "mdi:cast-variant"

    def __init__(self, coordinator: NyxrCompanionCoordinator) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, "airplay_sender")

    @property
    def native_value(self) -> str | None:
        """Return the connected sender's name, or None."""
        connected = self.airplay.get("connected")
        return str(connected) if connected else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the receiver's state and the sender's track."""
        airplay = self.airplay
        track = airplay.get("track") if isinstance(airplay.get("track"), dict) else {}
        return {
            "name": airplay.get("name"),
            "playing": airplay.get("playing"),
            "streaming": airplay.get("streaming"),
            "track_title": track.get("title"),
            "track_artist": track.get("artist"),
            "track_album": track.get("album"),
            "error": airplay.get("error"),
        }
