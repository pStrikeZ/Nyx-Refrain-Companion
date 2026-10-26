"""Coordinator for NyxR Companion integration."""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import aiohttp

from homeassistant.config_entries import ConfigEntry
from homeassistant.exceptions import ConfigEntryNotReady, HomeAssistantError
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import CONF_HOST, CONF_PORT, CONF_TOKEN, DOMAIN

_LOGGER = logging.getLogger(__name__)

type NyxrCompanionConfigEntry = ConfigEntry[NyxrCompanionCoordinator]


class NyxrCompanionCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Coordinator to manage connection and state with NyxR Companion daemon."""

    config_entry: NyxrCompanionConfigEntry

    def __init__(self, hass: Any, entry: NyxrCompanionConfigEntry) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN} ({entry.unique_id})",
            update_interval=None,
        )
        self.host: str = entry.data[CONF_HOST]
        self.port: int = entry.data[CONF_PORT]
        token = entry.data.get(CONF_TOKEN) or ""
        self._headers = {"Authorization": f"Bearer {token}"} if token else {}
        self._session = async_get_clientsession(hass)
        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._connected: bool = False
        self._stopped: bool = False

    @property
    def connected(self) -> bool:
        """Return True if WebSocket is actively connected."""
        return self._connected

    @property
    def device_info(self) -> DeviceInfo:
        """Return DeviceInfo for all entities."""
        return DeviceInfo(
            identifiers={(DOMAIN, self.config_entry.unique_id or f"{self.host}:{self.port}")},
            name="NyxR Companion",
            manufacturer="Nyx Refrain",
            model="CD Player",
        )

    def _set_connected(self, connected: bool) -> None:
        """Set connection status and notify entities if changed."""
        if self._connected != connected:
            self._connected = connected
            self.async_update_listeners()

    async def async_setup(self) -> None:
        """Initial connection check via GET /api/state."""
        try:
            data = await self.async_get_state()
        except Exception as err:
            raise ConfigEntryNotReady(
                f"Failed to connect to NyxR Companion at {self.host}:{self.port}: {err}"
            ) from err
        self.async_set_updated_data(data)

    async def async_get_state(self) -> dict[str, Any]:
        """Fetch current state from /api/state."""
        url = f"http://{self.host}:{self.port}/api/state"
        try:
            async with self._session.get(
                url, headers=self._headers, timeout=aiohttp.ClientTimeout(total=10)
            ) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    raise HomeAssistantError(
                        f"GET /api/state failed with status {resp.status}: {text}"
                    )
                data = await resp.json()
                if not isinstance(data, dict):
                    raise HomeAssistantError(
                        f"Unexpected state format from NyxR Companion: {data}"
                    )
                return data
        except (aiohttp.ClientError, TimeoutError) as err:
            raise HomeAssistantError(
                f"Communication error with NyxR Companion at {self.host}:{self.port}: {err}"
            ) from err

    async def _async_update_data(self) -> dict[str, Any]:
        """Coordinator update method for manual refreshes."""
        try:
            return await self.async_get_state()
        except Exception as err:
            raise UpdateFailed(f"Error fetching state: {err}") from err

    async def async_send_command(
        self,
        endpoint: str,
        payload: dict[str, Any] | None = None,
        timeout: float = 10,
    ) -> dict[str, Any]:
        """Send a POST command to NyxR Companion and update coordinator state."""
        url = f"http://{self.host}:{self.port}{endpoint}"
        if payload is None:
            payload = {}
        try:
            async with self._session.post(
                url,
                json=payload,
                headers=self._headers,
                timeout=aiohttp.ClientTimeout(total=timeout),
            ) as resp:
                data: dict[str, Any] = {}
                try:
                    data = await resp.json()
                except Exception:
                    pass

                if resp.status != 200:
                    error_msg = data.get("error") if isinstance(data, dict) else None
                    if not error_msg:
                        error_msg = f"HTTP {resp.status}"
                    raise HomeAssistantError(f"NyxR Companion command failed: {error_msg}")

                if isinstance(data, dict) and data:
                    self.async_set_updated_data(data)
                return data
        except (aiohttp.ClientError, TimeoutError) as err:
            raise HomeAssistantError(
                f"Communication error with NyxR Companion at {self.host}:{self.port}: {err}"
            ) from err

    async def async_run_ws(self) -> None:
        """Run WebSocket connection loop with exponential backoff."""
        backoff = 1.0
        url = f"ws://{self.host}:{self.port}/api/ws"

        while not self._stopped:
            self._set_connected(False)
            try:
                _LOGGER.debug("Connecting to NyxR Companion WebSocket at %s", url)
                async with self._session.ws_connect(
                    url, heartbeat=30.0, headers=self._headers
                ) as ws:
                    self._ws = ws
                    self._set_connected(True)
                    backoff = 1.0
                    _LOGGER.info(
                        "Connected to NyxR Companion WebSocket at %s:%s", self.host, self.port
                    )

                    async for msg in ws:
                        if self._stopped:
                            break
                        if msg.type == aiohttp.WSMsgType.TEXT:
                            try:
                                state = json.loads(msg.data)
                                if isinstance(state, dict):
                                    self.async_set_updated_data(state)
                            except json.JSONDecodeError as err:
                                _LOGGER.warning(
                                    "Invalid JSON received from WebSocket: %s", err
                                )
                        elif msg.type in (
                            aiohttp.WSMsgType.CLOSED,
                            aiohttp.WSMsgType.CLOSING,
                            aiohttp.WSMsgType.ERROR,
                        ):
                            _LOGGER.debug("WebSocket connection closed")
                            break
            except asyncio.CancelledError:
                self._set_connected(False)
                raise
            except (aiohttp.ClientError, TimeoutError, Exception) as err:
                _LOGGER.debug("NyxR Companion WebSocket connection error: %s", err)
            finally:
                self._ws = None
                self._set_connected(False)

            if self._stopped:
                break

            _LOGGER.debug(
                "WebSocket disconnected. Reconnecting in %.1f seconds...", backoff
            )
            try:
                await asyncio.sleep(backoff)
            except asyncio.CancelledError:
                break

            backoff = min(backoff * 2, 30.0)

    async def async_close(self) -> None:
        """Close WebSocket connection cleanly."""
        self._stopped = True
        self._set_connected(False)
        if self._ws is not None and not self._ws.closed:
            await self._ws.close()
