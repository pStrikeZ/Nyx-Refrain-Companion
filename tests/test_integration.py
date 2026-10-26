"""Unit and integration tests for NyxR Companion custom component."""
from __future__ import annotations

import asyncio
import os
import sys
import types
import unittest
from typing import Any, Generic, TypeVar
from unittest.mock import AsyncMock, MagicMock

import aiohttp
from aiohttp import web
from aiohttp.test_utils import AioHTTPTestCase

_T = TypeVar("_T")

# --- 1. Mock homeassistant environment for testing outside of HA ---
ha = types.ModuleType("homeassistant")
ha_const = types.ModuleType("homeassistant.const")
ha_const.CONF_HOST = "host"
ha_const.CONF_PORT = "port"


class Platform:
    SWITCH = "switch"
    SELECT = "select"
    BUTTON = "button"
    SENSOR = "sensor"
    NUMBER = "number"
    TEXT = "text"


ha_const.Platform = Platform


class EntityCategory:
    CONFIG = "config"
    DIAGNOSTIC = "diagnostic"


ha_const.EntityCategory = EntityCategory


class UnitOfTime:
    SECONDS = "s"
    MILLISECONDS = "ms"


ha_const.UnitOfTime = UnitOfTime
ha_const.PERCENTAGE = "%"

ha_core = types.ModuleType("homeassistant.core")


class HomeAssistant:
    def __init__(self) -> None:
        self.data: dict[str, Any] = {}
        self.config_entries = MagicMock()
        self.config_entries.async_forward_entry_setups = AsyncMock(return_value=True)
        self.config_entries.async_unload_platforms = AsyncMock(return_value=True)


ha_core.HomeAssistant = HomeAssistant

ha_exceptions = types.ModuleType("homeassistant.exceptions")


class HomeAssistantError(Exception):
    pass


class ConfigEntryNotReady(Exception):
    pass


ha_exceptions.HomeAssistantError = HomeAssistantError
ha_exceptions.ConfigEntryNotReady = ConfigEntryNotReady

ha_config_entries = types.ModuleType("homeassistant.config_entries")


class ConfigFlow:
    def __init_subclass__(cls, domain: str | None = None, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        cls.domain = domain

    def __init__(self) -> None:
        self.hass = None
        self.unique_id: str | None = None
        self._configured_ids: set[str] = set()

    async def async_set_unique_id(
        self, unique_id: str, *, raise_on_progress: bool = True
    ) -> None:
        self.unique_id = unique_id

    def _abort_if_unique_id_configured(
        self, updates: dict[str, Any] | None = None
    ) -> None:
        if self.unique_id in self._configured_ids:
            self.updated = updates
            raise HomeAssistantError("already_configured")

    def async_abort(self, *, reason: str) -> dict[str, Any]:
        return {"type": "abort", "reason": reason}

    def async_show_form(
        self,
        *,
        step_id: str,
        data_schema: Any = None,
        errors: dict[str, str] | None = None,
        description_placeholders: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        return {
            "type": "form",
            "step_id": step_id,
            "data_schema": data_schema,
            "errors": errors or {},
        }

    def async_create_entry(
        self, *, title: str, data: dict[str, Any], description: str | None = None
    ) -> dict[str, Any]:
        return {"type": "create_entry", "title": title, "data": data}


class ConfigEntry(Generic[_T]):
    def __init__(
        self,
        unique_id: str,
        data: dict[str, Any],
        entry_id: str = "test_entry_id",
        title: str = "NyxR Companion",
    ) -> None:
        self.unique_id = unique_id
        self.data = data
        self.entry_id = entry_id
        self.title = title
        self.runtime_data: Any = None
        self.background_tasks: list[asyncio.Task] = []

    def async_create_background_task(
        self, hass: Any, target: Any, name: str
    ) -> asyncio.Task:
        task = asyncio.create_task(target, name=name)
        self.background_tasks.append(task)
        return task


ha_config_entries.ConfigFlow = ConfigFlow
ha_config_entries.ConfigEntry = ConfigEntry
ha_config_entries.ConfigFlowResult = dict[str, Any]

ha_helpers = types.ModuleType("homeassistant.helpers")
ha_helpers_aiohttp_client = types.ModuleType("homeassistant.helpers.aiohttp_client")


def async_get_clientsession(hass: Any) -> aiohttp.ClientSession:
    return hass.data["client_session"]


ha_helpers_aiohttp_client.async_get_clientsession = async_get_clientsession

ha_helpers_device_registry = types.ModuleType("homeassistant.helpers.device_registry")


class DeviceInfo:
    def __init__(
        self,
        identifiers: set[tuple[str, str]],
        name: str,
        manufacturer: str,
        model: str,
    ) -> None:
        self.identifiers = identifiers
        self.name = name
        self.manufacturer = manufacturer
        self.model = model


ha_helpers_device_registry.DeviceInfo = DeviceInfo

ha_helpers_update_coordinator = types.ModuleType(
    "homeassistant.helpers.update_coordinator"
)


class UpdateFailed(Exception):
    pass


class DataUpdateCoordinator(Generic[_T]):
    def __init__(
        self,
        hass: Any,
        logger: Any,
        *,
        config_entry: Any = None,
        name: str | None = None,
        update_interval: Any = None,
    ) -> None:
        self.hass = hass
        self.logger = logger
        self.config_entry = config_entry
        self.name = name
        self.data: Any = None
        self.last_update_success = True
        self._listeners: list[Any] = []

    def async_add_listener(self, update_callback: Any) -> Any:
        self._listeners.append(update_callback)
        return lambda: self._listeners.remove(update_callback)

    def async_update_listeners(self) -> None:
        for listener in list(self._listeners):
            listener()

    def async_set_updated_data(self, data: Any) -> None:
        self.data = data
        self.last_update_success = True
        self.async_update_listeners()


class CoordinatorEntity(Generic[_T]):
    _attr_has_entity_name = True

    def __init__(self, coordinator: Any) -> None:
        self.coordinator = coordinator

    @property
    def available(self) -> bool:
        return self.coordinator.last_update_success

    @property
    def unique_id(self) -> str | None:
        return getattr(self, "_attr_unique_id", None)

    @property
    def entity_category(self) -> Any:
        return getattr(self, "_attr_entity_category", None)

    @property
    def translation_key(self) -> str | None:
        return getattr(self, "_attr_translation_key", None)

    @property
    def icon(self) -> str | None:
        return getattr(self, "_attr_icon", None)


ha_helpers_update_coordinator.DataUpdateCoordinator = DataUpdateCoordinator
ha_helpers_update_coordinator.CoordinatorEntity = CoordinatorEntity
ha_helpers_update_coordinator.UpdateFailed = UpdateFailed

ha_helpers_entity_platform = types.ModuleType("homeassistant.helpers.entity_platform")
ha_helpers_entity_platform.AddConfigEntryEntitiesCallback = Any
ha_helpers_entity_platform.AddEntitiesCallback = Any

ha_components = types.ModuleType("homeassistant.components")
ha_components_switch = types.ModuleType("homeassistant.components.switch")


class SwitchEntity:
    pass


ha_components_switch.SwitchEntity = SwitchEntity

ha_components_select = types.ModuleType("homeassistant.components.select")


class SelectEntity:
    @property
    def options(self) -> list[str]:
        return getattr(self, "_attr_options", [])


ha_components_select.SelectEntity = SelectEntity

ha_components_button = types.ModuleType("homeassistant.components.button")


class ButtonEntity:
    pass


ha_components_button.ButtonEntity = ButtonEntity

ha_components_sensor = types.ModuleType("homeassistant.components.sensor")


class SensorDeviceClass:
    ENUM = "enum"


class SensorEntity:
    pass


ha_components_sensor.SensorDeviceClass = SensorDeviceClass
ha_components_sensor.SensorEntity = SensorEntity

ha_components_number = types.ModuleType("homeassistant.components.number")


class NumberMode:
    AUTO = "auto"
    BOX = "box"
    SLIDER = "slider"


class NumberEntity:
    @property
    def native_min_value(self) -> float:
        return getattr(self, "_attr_native_min_value", 0.0)

    @property
    def native_max_value(self) -> float:
        return getattr(self, "_attr_native_max_value", 100.0)

    @property
    def native_step(self) -> float:
        return getattr(self, "_attr_native_step", 1.0)

    @property
    def native_unit_of_measurement(self) -> str | None:
        return getattr(self, "_attr_native_unit_of_measurement", None)

    @property
    def mode(self) -> Any:
        return getattr(self, "_attr_mode", NumberMode.AUTO)


ha_components_number.NumberMode = NumberMode
ha_components_number.NumberEntity = NumberEntity

ha_components_text = types.ModuleType("homeassistant.components.text")


class TextEntity:
    @property
    def native_max(self) -> int:
        return getattr(self, "_attr_native_max", 255)

    @property
    def native_min(self) -> int:
        return getattr(self, "_attr_native_min", 0)


ha_components_text.TextEntity = TextEntity

vol = types.ModuleType("voluptuous")


class Schema:
    def __init__(self, schema: Any) -> None:
        self.schema = schema

    def __call__(self, val: Any) -> Any:
        return val


def required_field(key: Any, default: Any = None) -> Any:
    return key


vol.Schema = Schema
vol.Required = required_field
vol.Optional = required_field
sys.modules["voluptuous"] = vol

# Inject mocks into sys.modules
sys.modules["homeassistant"] = ha
sys.modules["homeassistant.const"] = ha_const
sys.modules["homeassistant.core"] = ha_core
sys.modules["homeassistant.exceptions"] = ha_exceptions
sys.modules["homeassistant.config_entries"] = ha_config_entries
sys.modules["homeassistant.helpers"] = ha_helpers
sys.modules["homeassistant.helpers.aiohttp_client"] = ha_helpers_aiohttp_client
sys.modules["homeassistant.helpers.device_registry"] = ha_helpers_device_registry
sys.modules["homeassistant.helpers.update_coordinator"] = ha_helpers_update_coordinator
sys.modules["homeassistant.helpers.entity_platform"] = ha_helpers_entity_platform
sys.modules["homeassistant.components"] = ha_components
sys.modules["homeassistant.components.switch"] = ha_components_switch
sys.modules["homeassistant.components.select"] = ha_components_select
sys.modules["homeassistant.components.button"] = ha_components_button
sys.modules["homeassistant.components.sensor"] = ha_components_sensor
sys.modules["homeassistant.components.number"] = ha_components_number
sys.modules["homeassistant.components.text"] = ha_components_text

# Add custom_components directory to path
sys.path.insert(
    0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "custom_components")
)

# Import components under test
from fake_server import FAKE_TOKEN, create_app
from nyxr_companion import async_setup_entry, async_unload_entry
from nyxr_companion.button import NyxrCompanionBluetoothButton, NyxrCompanionEjectButton
from nyxr_companion.config_flow import NyxrCompanionConfigFlow
from nyxr_companion.coordinator import NyxrCompanionCoordinator
from nyxr_companion.number import (
    NyxrCompanionBluetoothBufferNumber,
    NyxrCompanionBufferSecondsNumber,
    NyxrCompanionReadSpeedNumber,
    async_setup_entry as async_setup_number_entry,
)
from nyxr_companion.select import (
    NyxrCompanionLatencyProfileSelect,
    NyxrCompanionPlayModeSelect,
    NyxrCompanionReadModeSelect,
    NyxrCompanionBluetoothForgetSelect,
    NyxrCompanionBluetoothPairSelect,
    NyxrCompanionReceiverSelect,
    NyxrCompanionSourceSelect,
    async_setup_entry as async_setup_select_entry,
)
from nyxr_companion.sensor import NyxrCompanionBluetoothSensor, NyxrCompanionStatusSensor
from nyxr_companion.switch import (
    NyxrCompanionBluetoothAutoSwitch,
    NyxrCompanionBluetoothPairingSwitch,
    NyxrCompanionStreamSwitch,
)
from nyxr_companion.text import (
    NyxrCompanionBluetoothNameText,
    NyxrCompanionTargetText,
    async_setup_entry as async_setup_text_entry,
)


class IntegrationTestCase(AioHTTPTestCase):
    """Integration test suite connecting custom component classes to fake_server."""

    async def get_application(self) -> web.Application:
        return create_app()

    async def setUpAsync(self) -> None:
        await super().setUpAsync()
        self.hass = HomeAssistant()
        self.hass.data["client_session"] = self.client.session

        self.entry = ConfigEntry(
            unique_id=f"{self.client.host}:{self.client.port}",
            data={"host": self.client.host, "port": self.client.port, "token": FAKE_TOKEN},
        )
        self.coordinator = NyxrCompanionCoordinator(self.hass, self.entry)

    async def test_coordinator_setup_and_entities(self) -> None:
        """Test coordinator initial fetch, entity state mappings, and commands."""
        # 1. Initial setup
        await self.coordinator.async_setup()
        self.assertIsNotNone(self.coordinator.data)
        self.assertEqual(self.coordinator.data["drive"], "ok")

        # Create entities
        stream_switch = NyxrCompanionStreamSwitch(self.coordinator)
        mode_select = NyxrCompanionPlayModeSelect(self.coordinator)
        eject_button = NyxrCompanionEjectButton(self.coordinator)
        status_sensor = NyxrCompanionStatusSensor(self.coordinator)

        # Before WS is connected, available is False
        self.assertFalse(stream_switch.available)
        self.assertFalse(status_sensor.available)

        # Device info check
        dev_info = self.coordinator.device_info
        self.assertEqual(dev_info.name, "NyxR Companion")
        self.assertEqual(dev_info.manufacturer, "Nyx Refrain")
        self.assertEqual(dev_info.model, "CD Player")
        self.assertIn(("nyxr_companion", self.entry.unique_id), dev_info.identifiers)

        # 2. Start WebSocket loop in background task
        ws_task = asyncio.create_task(self.coordinator.async_run_ws())
        # Wait a moment for WS connection to establish
        for _ in range(20):
            if self.coordinator.connected:
                break
            await asyncio.sleep(0.05)

        self.assertTrue(self.coordinator.connected)
        self.assertTrue(stream_switch.available)
        self.assertTrue(status_sensor.available)

        # 3. Test initial entity states
        self.assertFalse(stream_switch.is_on)
        self.assertEqual(mode_select.current_option, "repeat_all")
        # Drive is "ok" and playback is "stopped" -> status should be "stopped"
        self.assertEqual(status_sensor.native_value, "stopped")

        # Test sensor attributes
        attrs = status_sensor.extra_state_attributes
        self.assertEqual(attrs["album"], "Ref:rain / Nýx")
        self.assertEqual(attrs["artist"], "7mai & Limonène")
        self.assertEqual(attrs["track_count"], 3)
        self.assertEqual(len(attrs["tracks"]), 3)
        self.assertEqual(attrs["tracks"][0]["number"], 1)
        self.assertEqual(attrs["tracks"][0]["title"], "Ref:rain (for 7th Heaven)")
        self.assertEqual(attrs["tracks"][0]["length"], 142.0)
        self.assertEqual(status_sensor.entity_picture, "https://example.invalid/cover.jpg")

        # 4. Test switch turn_on
        await stream_switch.async_turn_on()
        # Verify coordinator and entities updated
        self.assertTrue(stream_switch.is_on)
        self.assertEqual(status_sensor.native_value, "playing")
        self.assertEqual(status_sensor.extra_state_attributes["track"], 1)
        self.assertEqual(
            status_sensor.extra_state_attributes["track_title"],
            "Ref:rain (for 7th Heaven)",
        )

        # 5. Test select mode change
        await mode_select.async_select_option("shuffle")
        self.assertEqual(mode_select.current_option, "shuffle")

        # 6. Test eject button press
        await eject_button.async_press()
        self.assertEqual(status_sensor.native_value, "tray_open")
        self.assertIsNone(status_sensor.entity_picture)
        self.assertIsNone(status_sensor.extra_state_attributes["album"])

        # 6b. Test sending command that errors (e.g. streaming with tray_open returns 409)
        with self.assertRaises(HomeAssistantError) as cm:
            await stream_switch.async_turn_on()
        self.assertIn("No disc in drive", str(cm.exception))

        # 7. Close coordinator and verify available becomes False
        await self.coordinator.async_close()
        try:
            await asyncio.wait_for(ws_task, timeout=1.0)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            ws_task.cancel()

        self.assertFalse(self.coordinator.connected)
        self.assertFalse(stream_switch.available)
        self.assertFalse(status_sensor.available)

    async def test_sensor_enum_mappings(self) -> None:
        """Verify all enum mappings for status sensor."""
        status_sensor = NyxrCompanionStatusSensor(self.coordinator)

        # Drive not ok takes drive value
        for drive_val in ("missing", "tray_open", "no_disc", "not_audio", "not_ready"):
            self.coordinator.async_set_updated_data({"drive": drive_val, "playback": "playing"})
            self.assertEqual(status_sensor.native_value, drive_val)

        # Drive ok takes playback value
        for playback_val in ("stopped", "playing", "paused"):
            self.coordinator.async_set_updated_data({"drive": "ok", "playback": playback_val})
            self.assertEqual(status_sensor.native_value, playback_val)

    async def test_config_flow(self) -> None:
        """Test config flow validation and error reporting."""
        flow = NyxrCompanionConfigFlow()
        flow.hass = self.hass

        # 1. Test connection failure
        result = await flow.async_step_user(
            {"host": "127.0.0.1", "port": 59999}
        )
        self.assertEqual(result["type"], "form")
        self.assertEqual(result["errors"]["base"], "cannot_connect")

        # 2. A wrong token
        result = await flow.async_step_user(
            {"host": self.client.host, "port": self.client.port, "token": "wrong"}
        )
        self.assertEqual(result["errors"]["base"], "invalid_auth")

        # 3. Test successful connection
        result = await flow.async_step_user(
            {"host": self.client.host, "port": self.client.port, "token": FAKE_TOKEN}
        )
        self.assertEqual(result["type"], "create_entry")
        self.assertEqual(
            result["title"], f"NyxR Companion ({self.client.host}:{self.client.port})"
        )
        self.assertEqual(result["data"]["host"], self.client.host)
        self.assertEqual(result["data"]["port"], self.client.port)

        self.assertEqual(result["data"]["token"], FAKE_TOKEN)

        # 4. Test already configured abort
        flow._configured_ids.add(f"{self.client.host}:{self.client.port}")
        with self.assertRaises(HomeAssistantError):
            await flow.async_step_user(
                {"host": self.client.host, "port": self.client.port}
            )

    async def test_config_flow_hassio(self) -> None:
        """The add-on announces itself through Supervisor discovery."""
        info = types.SimpleNamespace(
            config={"host": self.client.host, "port": self.client.port, "token": FAKE_TOKEN}
        )
        flow = NyxrCompanionConfigFlow()
        flow.hass = self.hass
        result = await flow.async_step_hassio(info)
        self.assertEqual(result["type"], "form")
        self.assertEqual(result["step_id"], "hassio_confirm")
        result = await flow.async_step_hassio_confirm({})
        self.assertEqual(result["type"], "create_entry")
        self.assertEqual(result["data"]["token"], FAKE_TOKEN)

        # An existing entry for the address gets the new token.
        flow = NyxrCompanionConfigFlow()
        flow.hass = self.hass
        flow._configured_ids.add(f"{self.client.host}:{self.client.port}")
        with self.assertRaises(HomeAssistantError):
            await flow.async_step_hassio(info)
        self.assertEqual(flow.updated["token"], FAKE_TOKEN)

        # A token that does not work ends the flow.
        flow = NyxrCompanionConfigFlow()
        flow.hass = self.hass
        info.config["token"] = "stale"
        await flow.async_step_hassio(info)
        result = await flow.async_step_hassio_confirm({})
        self.assertEqual(result, {"type": "abort", "reason": "invalid_auth"})


    async def test_setup_and_unload_entry(self) -> None:
        """Test full async_setup_entry and async_unload_entry lifecycle."""
        entry = ConfigEntry(
            unique_id=f"{self.client.host}:{self.client.port}",
            data={"host": self.client.host, "port": self.client.port, "token": FAKE_TOKEN},
        )
        # 1. Setup entry
        success = await async_setup_entry(self.hass, entry)
        self.assertTrue(success)
        self.assertIsNotNone(entry.runtime_data)
        self.hass.config_entries.async_forward_entry_setups.assert_awaited_once()

        # 2. Wait for WS connection
        for _ in range(20):
            if entry.runtime_data.connected:
                break
            await asyncio.sleep(0.05)
        self.assertTrue(entry.runtime_data.connected)

        # 3. Unload entry
        unload_success = await async_unload_entry(self.hass, entry)
        self.assertTrue(unload_success)
        self.assertFalse(entry.runtime_data.connected)

        # Cancel tasks
        for task in entry.background_tasks:
            task.cancel()

    async def test_settings_entities(self) -> None:
        """Test configuration entities for daemon settings."""
        self.entry.runtime_data = self.coordinator
        await self.coordinator.async_setup()
        self.assertIsNotNone(self.coordinator.data)

        # 1. Test platform async_setup_entry
        select_entities: list[Any] = []
        await async_setup_select_entry(self.hass, self.entry, select_entities.extend)
        self.assertEqual(len(select_entities), 7)

        number_entities: list[Any] = []
        await async_setup_number_entry(self.hass, self.entry, number_entities.extend)
        self.assertEqual(len(number_entities), 3)

        text_entities: list[Any] = []
        await async_setup_text_entry(self.hass, self.entry, text_entities.extend)
        self.assertEqual(len(text_entities), 2)

        # Connect WebSocket
        ws_task = asyncio.create_task(self.coordinator.async_run_ws())
        for _ in range(20):
            if self.coordinator.connected:
                break
            await asyncio.sleep(0.05)
        self.assertTrue(self.coordinator.connected)

        read_mode_select = NyxrCompanionReadModeSelect(self.coordinator)
        latency_select = NyxrCompanionLatencyProfileSelect(self.coordinator)
        read_speed_num = NyxrCompanionReadSpeedNumber(self.coordinator)
        buffer_num = NyxrCompanionBufferSecondsNumber(self.coordinator)
        target_text = NyxrCompanionTargetText(self.coordinator)

        all_settings_entities = [
            read_mode_select,
            latency_select,
            read_speed_num,
            buffer_num,
            target_text,
        ]

        # 2. Check metadata: entity category, unique_id, translation_key, icon
        for entity in all_settings_entities:
            self.assertEqual(entity.entity_category, EntityCategory.CONFIG)
            self.assertTrue(entity.available)

        uid_base = self.entry.unique_id
        self.assertEqual(read_mode_select.unique_id, f"{uid_base}_read_mode")
        self.assertEqual(read_mode_select.translation_key, "read_mode")
        self.assertEqual(read_mode_select.icon, "mdi:disc")

        self.assertEqual(latency_select.unique_id, f"{uid_base}_latency_profile")
        self.assertEqual(latency_select.translation_key, "latency_profile")
        self.assertEqual(latency_select.icon, "mdi:timer-outline")

        self.assertEqual(read_speed_num.unique_id, f"{uid_base}_read_speed")
        self.assertEqual(read_speed_num.translation_key, "read_speed")
        self.assertEqual(read_speed_num.icon, "mdi:speedometer")
        self.assertEqual(read_speed_num.native_min_value, 1.0)
        self.assertEqual(read_speed_num.native_max_value, 24.0)
        self.assertEqual(read_speed_num.native_step, 1.0)
        self.assertEqual(read_speed_num.mode, NumberMode.BOX)
        self.assertEqual(read_speed_num.native_unit_of_measurement, "x")

        self.assertEqual(buffer_num.unique_id, f"{uid_base}_buffer_seconds")
        self.assertEqual(buffer_num.translation_key, "buffer_seconds")
        self.assertEqual(buffer_num.icon, "mdi:buffer")
        self.assertEqual(buffer_num.native_min_value, 2.0)
        self.assertEqual(buffer_num.native_max_value, 60.0)
        self.assertEqual(buffer_num.native_step, 1.0)
        self.assertEqual(buffer_num.mode, NumberMode.BOX)
        self.assertEqual(buffer_num.native_unit_of_measurement, UnitOfTime.SECONDS)

        self.assertEqual(target_text.unique_id, f"{uid_base}_target")
        self.assertEqual(target_text.translation_key, "target")
        self.assertEqual(target_text.icon, "mdi:speaker-wireless")
        self.assertEqual(target_text.native_max, 64)

        # 3. Check initial values from state
        self.assertEqual(read_mode_select.current_option, "raw")
        self.assertEqual(latency_select.current_option, "balanced")
        self.assertEqual(read_speed_num.native_value, 4)
        self.assertEqual(buffer_num.native_value, 10.0)
        self.assertEqual(target_text.native_value, "192.0.2.10:7000")
        receiver = NyxrCompanionReceiverSelect(self.coordinator)
        self.assertEqual(
            receiver.options,
            ["Kitchen (192.0.2.11:7000)", "Living Room (192.0.2.10:7000)"],
        )
        self.assertEqual(receiver.current_option, "Living Room (192.0.2.10:7000)")
        await receiver.async_select_option("Kitchen (192.0.2.11:7000)")
        self.assertEqual(target_text.native_value, "192.0.2.11:7000")
        await target_text.async_set_value("192.0.2.99:7000")  # not found on the network
        self.assertEqual(receiver.current_option, "192.0.2.99:7000")
        await target_text.async_set_value("192.0.2.10:7000")
        unset = dict(self.coordinator.data, settings={**self.coordinator.data["settings"], "target": None})
        self.coordinator.async_set_updated_data(unset)
        self.assertTrue(target_text.available)  # so the receiver can be set the first time
        self.assertIsNone(target_text.native_value)
        await target_text.async_set_value("192.0.2.10:7000")

        # 4. Setting values sends right request and updates entities
        await read_mode_select.async_select_option("paranoia_full")
        self.assertEqual(read_mode_select.current_option, "paranoia_full")

        await latency_select.async_select_option("low")
        self.assertEqual(latency_select.current_option, "low")

        await read_speed_num.async_set_native_value(8)
        self.assertEqual(read_speed_num.native_value, 8)

        await buffer_num.async_set_native_value(25.5)
        self.assertEqual(buffer_num.native_value, 25.5)

        await target_text.async_set_value("192.168.1.88:7000")
        self.assertEqual(target_text.native_value, "192.168.1.88:7000")

        # 5. Invalid values raise HomeAssistantError
        with self.assertRaises(HomeAssistantError):
            await read_mode_select.async_select_option("invalid_read_mode")

        with self.assertRaises(HomeAssistantError):
            await latency_select.async_select_option("ultra_fast")

        with self.assertRaises(HomeAssistantError):
            await read_speed_num.async_set_native_value(50)

        with self.assertRaises(HomeAssistantError):
            await buffer_num.async_set_native_value(1.0)

        with self.assertRaises(HomeAssistantError):
            await target_text.async_set_value("space in target")

        # 6. Entity unavailable / returns None when settings dict or key is missing
        self.coordinator.async_set_updated_data({"drive": "ok", "playback": "stopped"})
        for entity in all_settings_entities:
            self.assertFalse(entity.available)

        self.assertIsNone(read_mode_select.current_option)
        self.assertIsNone(latency_select.current_option)
        self.assertIsNone(read_speed_num.native_value)
        self.assertIsNone(buffer_num.native_value)
        self.assertIsNone(target_text.native_value)

        await self.coordinator.async_close()
        try:
            await asyncio.wait_for(ws_task, timeout=1.0)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            ws_task.cancel()

    async def test_bluetooth_entities(self) -> None:
        self.entry.runtime_data = self.coordinator
        await self.coordinator.async_setup()
        ws_task = asyncio.create_task(self.coordinator.async_run_ws())
        for _ in range(20):
            if self.coordinator.connected:
                break
            await asyncio.sleep(0.05)
        self.assertTrue(self.coordinator.connected)

        source = NyxrCompanionSourceSelect(self.coordinator)
        pairing = NyxrCompanionBluetoothPairingSwitch(self.coordinator)
        auto = NyxrCompanionBluetoothAutoSwitch(self.coordinator)
        device = NyxrCompanionBluetoothSensor(self.coordinator)
        disconnect = NyxrCompanionBluetoothButton(
            self.coordinator, "bluetooth_disconnect", "mdi:bluetooth-off"
        )
        forget = NyxrCompanionBluetoothForgetSelect(self.coordinator)
        name = NyxrCompanionBluetoothNameText(self.coordinator)
        buffer_ms = NyxrCompanionBluetoothBufferNumber(self.coordinator)

        uid_base = self.entry.unique_id
        self.assertEqual(source.unique_id, f"{uid_base}_source")
        self.assertEqual(pairing.unique_id, f"{uid_base}_bluetooth_pairing")
        self.assertEqual(forget.unique_id, f"{uid_base}_bluetooth_forget_device")
        self.assertEqual(forget.translation_key, "bluetooth_forget")
        self.assertEqual(forget.entity_category, EntityCategory.CONFIG)
        for entity in (auto, name, buffer_ms):
            self.assertEqual(entity.entity_category, EntityCategory.CONFIG)
        self.assertEqual(buffer_ms.native_unit_of_measurement, UnitOfTime.MILLISECONDS)

        # Initial state
        self.assertEqual(source.current_option, "cd")
        self.assertFalse(pairing.is_on)
        self.assertTrue(auto.is_on)
        self.assertEqual(device.native_value, "iPod")
        self.assertEqual(device.extra_state_attributes["paired_devices"], ["iPod"])
        self.assertEqual(device.extra_state_attributes["codec"], "SBC")
        self.assertEqual(name.native_value, "NyxR Companion")
        self.assertEqual(buffer_ms.native_value, 150)

        # Commands
        await source.async_select_option("bluetooth")
        self.assertEqual(source.current_option, "bluetooth")
        with self.assertRaises(HomeAssistantError):
            await source.async_select_option("radio")

        pair = NyxrCompanionBluetoothPairSelect(self.coordinator)
        self.assertFalse(pair.available)  # nothing nearby until pairing opens
        await pairing.async_turn_on()
        self.assertTrue(pairing.is_on)
        self.assertTrue(pair.available)
        self.assertEqual(pair.options, ["Phone (66:77:88:99:AA:BB)"])
        self.assertIsNone(pair.current_option)
        await pair.async_select_option("Phone (66:77:88:99:AA:BB)")
        self.assertFalse(pairing.is_on)
        self.assertFalse(pair.available)
        self.assertIn("Phone", device.extra_state_attributes["paired_devices"])
        await pairing.async_turn_on()
        await pairing.async_turn_off()
        self.assertFalse(pairing.is_on)

        await auto.async_turn_off()
        self.assertFalse(auto.is_on)
        await name.async_set_value("Living Room CD")
        self.assertEqual(name.native_value, "Living Room CD")
        await buffer_ms.async_set_native_value(300)
        self.assertEqual(buffer_ms.native_value, 300)
        with self.assertRaises(HomeAssistantError):
            await buffer_ms.async_set_native_value(5)

        await disconnect.async_press()
        self.assertIsNone(device.native_value)
        self.assertEqual(
            forget.options, ["iPod (00:11:22:33:44:55)", "Phone (66:77:88:99:AA:BB)"]
        )
        self.assertIsNone(forget.current_option)
        await forget.async_select_option("Phone (66:77:88:99:AA:BB)")
        self.assertEqual(device.extra_state_attributes["paired_devices"], ["iPod"])
        await forget.async_select_option("iPod (00:11:22:33:44:55)")
        self.assertEqual(device.extra_state_attributes["paired_devices"], [])
        self.assertFalse(forget.available)  # nothing left to forget

        # Without Bluetooth on the host, its entities are unavailable.
        data = dict(self.coordinator.data)
        data["bluetooth"] = {"available": False}
        self.coordinator.async_set_updated_data(data)
        for entity in (pairing, device, disconnect, forget):
            self.assertFalse(entity.available)
        self.assertTrue(source.available)

        await self.coordinator.async_close()
        try:
            await asyncio.wait_for(ws_task, timeout=1.0)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            ws_task.cancel()


if __name__ == "__main__":
    unittest.main()
