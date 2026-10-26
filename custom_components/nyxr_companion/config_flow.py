"""Config flow for NyxR Companion integration."""
from __future__ import annotations

import logging
from typing import Any

import aiohttp
import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import CONF_TOKEN, DEFAULT_HOST, DEFAULT_PORT, DOMAIN

_LOGGER = logging.getLogger(__name__)

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_HOST, default=DEFAULT_HOST): str,
        vol.Required(CONF_PORT, default=DEFAULT_PORT): int,
        vol.Optional(CONF_TOKEN, default=""): str,
    }
)


class NyxrCompanionConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for NyxR Companion."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize the flow."""
        super().__init__()
        self._discovered: dict[str, Any] | None = None

    async def _async_check(self, host: str, port: int, token: str) -> str | None:
        """Return an error key if the daemon cannot be reached with these details."""
        session = async_get_clientsession(self.hass)
        url = f"http://{host}:{port}/api/state"
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        try:
            async with session.get(
                url, headers=headers, timeout=aiohttp.ClientTimeout(total=10)
            ) as resp:
                if resp.status == 200:
                    return None
                if resp.status == 401:
                    return "invalid_auth"
                _LOGGER.warning(
                    "Connection test returned unexpected status: %s", resp.status
                )
        except (aiohttp.ClientError, TimeoutError) as err:
            _LOGGER.warning(
                "Failed to connect to NyxR Companion at %s:%s: %s", host, port, err
            )
        return "cannot_connect"

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the user step to set up NyxR Companion."""
        errors: dict[str, str] = {}

        if user_input is not None:
            host = str(user_input[CONF_HOST]).strip()
            port = int(user_input[CONF_PORT])
            token = str(user_input.get(CONF_TOKEN) or "").strip()
            unique_id = f"{host}:{port}"

            await self.async_set_unique_id(unique_id)
            self._abort_if_unique_id_configured()

            error = await self._async_check(host, port, token)
            if error is None:
                return self.async_create_entry(
                    title=f"NyxR Companion ({unique_id})",
                    data={CONF_HOST: host, CONF_PORT: port, CONF_TOKEN: token},
                )
            errors["base"] = error

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_DATA_SCHEMA,
            errors=errors,
        )

    async def async_step_hassio(self, discovery_info: Any) -> ConfigFlowResult:
        """Handle the add-on announcing itself through Supervisor discovery.

        An entry for the same address only gets the new details (the token is made when
        the add-on first starts)."""
        config = discovery_info.config
        data = {
            CONF_HOST: str(config[CONF_HOST]),
            CONF_PORT: int(config[CONF_PORT]),
            CONF_TOKEN: str(config.get(CONF_TOKEN) or ""),
        }
        await self.async_set_unique_id(f"{data[CONF_HOST]}:{data[CONF_PORT]}")
        self._abort_if_unique_id_configured(updates=data)
        self._discovered = data
        return await self.async_step_hassio_confirm()

    async def async_step_hassio_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm setting up the discovered add-on."""
        assert self._discovered is not None
        if user_input is None:
            return self.async_show_form(step_id="hassio_confirm")
        error = await self._async_check(
            self._discovered[CONF_HOST],
            self._discovered[CONF_PORT],
            self._discovered[CONF_TOKEN],
        )
        if error is not None:
            return self.async_abort(reason=error)
        return self.async_create_entry(title="NyxR Companion", data=self._discovered)
