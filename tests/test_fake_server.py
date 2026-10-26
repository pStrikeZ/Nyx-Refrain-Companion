"""Smoke test for fake_server.py using aiohttp.test_utils."""
import asyncio
import unittest

from aiohttp import web
from aiohttp.test_utils import AioHTTPTestCase

from fake_server import create_app


class FakeServerTestCase(AioHTTPTestCase):
    """Test suite for fake_server.py endpoints and WebSocket behavior."""

    async def get_application(self) -> web.Application:
        """Create the application under test."""
        return create_app(token=None)

    async def test_get_state(self) -> None:
        """Test GET /api/state returns 200 and default state."""
        resp = await self.client.get("/api/state")
        self.assertEqual(resp.status, 200)
        data = await resp.json()
        self.assertEqual(data["drive"], "ok")
        self.assertEqual(data["mode"], "repeat_all")
        self.assertEqual(data["playback"], "stopped")
        self.assertFalse(data["streaming"])
        self.assertIn("settings", data)
        self.assertEqual(data["settings"]["read_mode"], "raw")
        self.assertEqual(data["settings"]["read_speed"], 4)
        self.assertEqual(data["settings"]["buffer_seconds"], 10.0)
        self.assertEqual(data["settings"]["latency_profile"], "balanced")
        self.assertEqual(data["settings"]["target"], "192.0.2.10:7000")

    async def test_ws_push_and_commands(self) -> None:
        """Test WebSocket initial push and broadcast on POST commands."""
        ws = await self.client.ws_connect("/api/ws")
        # 1. First message on connect is initial state
        initial_msg = await ws.receive_json()
        self.assertEqual(initial_msg["drive"], "ok")
        self.assertEqual(initial_msg["playback"], "stopped")

        # 2. Test POST /api/mode
        resp = await self.client.post("/api/mode", json={"mode": "shuffle"})
        self.assertEqual(resp.status, 200)
        post_data = await resp.json()
        self.assertEqual(post_data["mode"], "shuffle")

        # Verify WebSocket received the update
        ws_msg = await ws.receive_json()
        self.assertEqual(ws_msg["mode"], "shuffle")

        # 3. Test POST /api/stream {"on": true}
        resp = await self.client.post("/api/stream", json={"on": True})
        self.assertEqual(resp.status, 200)
        post_data = await resp.json()
        self.assertTrue(post_data["streaming"])
        self.assertEqual(post_data["playback"], "playing")
        self.assertEqual(post_data["track"], 1)

        ws_msg = await ws.receive_json()
        self.assertTrue(ws_msg["streaming"])
        self.assertEqual(ws_msg["playback"], "playing")

        # 4. Test POST /api/pause
        resp = await self.client.post("/api/pause", json={})
        self.assertEqual(resp.status, 200)
        post_data = await resp.json()
        self.assertEqual(post_data["playback"], "paused")

        ws_msg = await ws.receive_json()
        self.assertEqual(ws_msg["playback"], "paused")

        # 5. Test POST /api/next
        resp = await self.client.post("/api/next", json={})
        self.assertEqual(resp.status, 200)
        post_data = await resp.json()
        self.assertEqual(post_data["track"], 2)

        ws_msg = await ws.receive_json()
        self.assertEqual(ws_msg["track"], 2)

        # 6. Test POST /api/previous
        resp = await self.client.post("/api/previous", json={})
        self.assertEqual(resp.status, 200)
        post_data = await resp.json()
        self.assertEqual(post_data["track"], 1)

        ws_msg = await ws.receive_json()
        self.assertEqual(ws_msg["track"], 1)

        # 7. Test POST /api/eject
        resp = await self.client.post("/api/eject", json={})
        self.assertEqual(resp.status, 200)
        post_data = await resp.json()
        self.assertEqual(post_data["drive"], "tray_open")
        self.assertIsNone(post_data["disc"])
        self.assertFalse(post_data["streaming"])
        self.assertEqual(post_data["playback"], "stopped")

        ws_msg = await ws.receive_json()
        self.assertEqual(ws_msg["drive"], "tray_open")

        # 8. Test POST /api/stream {"on": true} fails with 409 when tray_open
        resp = await self.client.post("/api/stream", json={"on": True})
        self.assertEqual(resp.status, 409)
        err_data = await resp.json()
        self.assertIn("error", err_data)

        # 9. Test POST /api/mode with invalid mode fails with 400
        resp = await self.client.post("/api/mode", json={"mode": "invalid_mode"})
        self.assertEqual(resp.status, 400)
        err_data = await resp.json()
        self.assertIn("error", err_data)

        await ws.close()

    async def test_post_settings(self) -> None:
        """Test POST /api/settings validation and WebSocket broadcast."""
        ws = await self.client.ws_connect("/api/ws")
        initial_msg = await ws.receive_json()
        self.assertIn("settings", initial_msg)

        # 1. Valid update with a subset of settings
        resp = await self.client.post(
            "/api/settings", json={"read_mode": "paranoia_full", "read_speed": 8}
        )
        self.assertEqual(resp.status, 200)
        data = await resp.json()
        self.assertEqual(data["settings"]["read_mode"], "paranoia_full")
        self.assertEqual(data["settings"]["read_speed"], 8)

        ws_msg = await ws.receive_json()
        self.assertEqual(ws_msg["settings"]["read_mode"], "paranoia_full")
        self.assertEqual(ws_msg["settings"]["read_speed"], 8)

        # 2. Valid update with remaining settings
        resp = await self.client.post(
            "/api/settings",
            json={
                "buffer_seconds": 15.5,
                "latency_profile": "low",
                "target": "192.168.1.100:7000",
            },
        )
        self.assertEqual(resp.status, 200)
        data = await resp.json()
        self.assertEqual(data["settings"]["buffer_seconds"], 15.5)
        self.assertEqual(data["settings"]["latency_profile"], "low")
        self.assertEqual(data["settings"]["target"], "192.168.1.100:7000")

        ws_msg = await ws.receive_json()
        self.assertEqual(ws_msg["settings"]["buffer_seconds"], 15.5)
        self.assertEqual(ws_msg["settings"]["latency_profile"], "low")
        self.assertEqual(ws_msg["settings"]["target"], "192.168.1.100:7000")

        # 3. Invalid requests return 400
        invalid_payloads = [
            {},
            {"unknown_setting": "val"},
            {"read_mode": "invalid_mode"},
            {"latency_profile": "ultra"},
            {"read_speed": 0},
            {"read_speed": 25},
            {"read_speed": True},
            {"read_speed": "8"},
            {"buffer_seconds": 1.9},
            {"buffer_seconds": 60.1},
            {"buffer_seconds": True},
            {"volume": 50},  # removed: NyxR Companion never sets the receiver's volume
            {"target": ""},
            {"target": "   "},
            {"target": "192.0.2.1 7000"},
            {"target": 123},
        ]
        for payload in invalid_payloads:
            with self.subTest(payload=payload):
                resp = await self.client.post("/api/settings", json=payload)
                self.assertEqual(resp.status, 400)
                err_data = await resp.json()
                self.assertIn("error", err_data)

        await ws.close()


if __name__ == "__main__":
    unittest.main()
