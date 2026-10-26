"""Fake nyxr-companion daemon server for smoke testing."""
from __future__ import annotations

import argparse
import copy
import logging
import os
import sys
from typing import Any

from aiohttp import web

_LOGGER = logging.getLogger("fake_nyxr_companion")

DEFAULT_DISC = {
    "id": "mock-disc-id-12345",
    "album": "Ref:rain / Nýx",
    "artist": "7mai & Limonène",
    "cover_url": "https://example.invalid/cover.jpg",
    "tracks": [
        {
            "number": 1,
            "title": "Ref:rain (for 7th Heaven)",
            "artist": "Limonène",
            "length": 142.0,
        },
        {
            "number": 2,
            "title": "Nýx",
            "artist": "7mai",
            "length": 138.5,
        },
        {
            "number": 3,
            "title": "Bonus Track",
            "artist": "Various",
            "length": 180.0,
        },
    ],
}

DEFAULT_SETTINGS: dict[str, Any] = {
    "read_mode": "raw",
    "read_speed": 4,
    "buffer_seconds": 10.0,
    "latency_profile": "balanced",
    "target": "192.0.2.10:7000",
    "bluetooth_name": "NyxR Companion",
    "bluetooth_auto_switch": True,
    "bluetooth_buffer_ms": 150,
}

DEFAULT_BLUETOOTH: dict[str, Any] = {
    "available": True,
    "name": "NyxR Companion",
    "pairing": False,
    "scanning": False,
    "devices": [{"address": "00:11:22:33:44:55", "name": "iPod", "connected": True}],
    "nearby": [],
    "connected": "iPod",
    "playing": False,
    "codec": "SBC",
    "rate": 44100,
    "streaming": False,
    "playback": "stopped",
    "track": None,
    "error": None,
}

FAKE_TOKEN = "test-token"

DEFAULT_RECEIVERS: list[dict[str, Any]] = [
    {"name": "Kitchen", "address": "192.0.2.11:7000", "model": None},
    {"name": "Living Room", "address": "192.0.2.10:7000", "model": "AudioAccessory5,1"},
]

DEFAULT_STATE: dict[str, Any] = {
    "source": "cd",
    "drive": "ok",
    "disc": DEFAULT_DISC,
    "streaming": False,
    "playback": "stopped",
    "track": None,
    "position": 0.0,
    "mode": "repeat_all",
    "error": None,
    "bluetooth": copy.deepcopy(DEFAULT_BLUETOOTH),
    "receivers": copy.deepcopy(DEFAULT_RECEIVERS),
    "settings": copy.deepcopy(DEFAULT_SETTINGS),
}

FAKE_SERVER_KEY = web.AppKey("fake_server", Any)


class FakeServerState:
    """In-memory state and WebSocket broadcaster."""

    def __init__(self, initial_state: dict[str, Any] | None = None) -> None:
        """Initialize state."""
        self.state: dict[str, Any] = copy.deepcopy(
            initial_state if initial_state is not None else DEFAULT_STATE
        )
        self.ws_clients: set[web.WebSocketResponse] = set()

    async def broadcast(self) -> None:
        """Broadcast current state to all connected WebSocket clients."""
        for ws in list(self.ws_clients):
            if not ws.closed:
                try:
                    await ws.send_json(self.state)
                except Exception as err:
                    _LOGGER.debug("Failed to send WebSocket message: %s", err)


async def handle_get_state(request: web.Request) -> web.Response:
    """Return the current state as JSON."""
    server: FakeServerState = request.app[FAKE_SERVER_KEY]
    return web.json_response(server.state)


async def handle_ws(request: web.Request) -> web.WebSocketResponse:
    """Handle WebSocket connection and push state updates."""
    server: FakeServerState = request.app[FAKE_SERVER_KEY]
    ws = web.WebSocketResponse()
    await ws.prepare(request)

    server.ws_clients.add(ws)
    _LOGGER.info("WebSocket client connected (%d total)", len(server.ws_clients))
    try:
        # Push current state immediately upon connection
        await ws.send_json(server.state)
        async for msg in ws:
            # Client doesn't need to send messages, but keep connection open
            pass
    finally:
        server.ws_clients.discard(ws)
        _LOGGER.info("WebSocket client disconnected (%d remaining)", len(server.ws_clients))

    return ws


async def handle_post_stream(request: web.Request) -> web.Response:
    """Handle /api/stream command."""
    server: FakeServerState = request.app[FAKE_SERVER_KEY]
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON"}, status=400)

    on = data.get("on")
    if not isinstance(on, bool):
        return web.json_response(
            {"error": "Missing or invalid 'on' parameter"}, status=400
        )

    if on:
        if server.state.get("drive") != "ok":
            return web.json_response({"error": "No disc in drive"}, status=409)
        server.state["streaming"] = True
        if server.state.get("playback") != "playing":
            server.state["playback"] = "playing"
            if server.state.get("track") is None:
                server.state["track"] = 1
                server.state["position"] = 0.0
    else:
        server.state["streaming"] = False
        server.state["playback"] = "stopped"

    await server.broadcast()
    return web.json_response(server.state)


async def handle_post_mode(request: web.Request) -> web.Response:
    """Handle /api/mode command."""
    server: FakeServerState = request.app[FAKE_SERVER_KEY]
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON"}, status=400)

    mode = data.get("mode")
    valid_modes = {"repeat_one", "repeat_all", "shuffle"}
    if mode not in valid_modes:
        return web.json_response({"error": f"Invalid mode: {mode}"}, status=400)

    server.state["mode"] = mode
    await server.broadcast()
    return web.json_response(server.state)


async def handle_post_eject(request: web.Request) -> web.Response:
    """Handle /api/eject command."""
    server: FakeServerState = request.app[FAKE_SERVER_KEY]
    server.state["streaming"] = False
    server.state["playback"] = "stopped"
    server.state["drive"] = "tray_open"
    server.state["disc"] = None
    server.state["track"] = None
    server.state["position"] = 0.0

    await server.broadcast()
    return web.json_response(server.state)


async def handle_post_play(request: web.Request) -> web.Response:
    """Handle /api/play command."""
    server: FakeServerState = request.app[FAKE_SERVER_KEY]
    if server.state.get("drive") != "ok":
        return web.json_response({"error": "No disc in drive"}, status=409)

    data: dict[str, Any] = {}
    if request.can_read_body:
        try:
            data = await request.json()
        except Exception:
            pass

    track = data.get("track") if isinstance(data, dict) else None
    disc = server.state.get("disc")
    tracks = disc.get("tracks", []) if isinstance(disc, dict) else []

    if track is not None:
        if not isinstance(track, int) or track < 1 or (tracks and track > len(tracks)):
            return web.json_response({"error": f"Invalid track: {track}"}, status=400)
        server.state["track"] = track
        server.state["position"] = 0.0
    elif server.state.get("track") is None:
        server.state["track"] = 1
        server.state["position"] = 0.0

    server.state["streaming"] = True
    server.state["playback"] = "playing"
    await server.broadcast()
    return web.json_response(server.state)


async def handle_post_pause(request: web.Request) -> web.Response:
    """Handle /api/pause command."""
    server: FakeServerState = request.app[FAKE_SERVER_KEY]
    if server.state.get("playback") == "playing":
        server.state["playback"] = "paused"
    await server.broadcast()
    return web.json_response(server.state)


async def handle_post_next(request: web.Request) -> web.Response:
    """Handle /api/next command."""
    server: FakeServerState = request.app[FAKE_SERVER_KEY]
    disc = server.state.get("disc")
    if server.state.get("drive") != "ok" or not isinstance(disc, dict):
        return web.json_response({"error": "No disc in drive"}, status=409)

    tracks = disc.get("tracks", [])
    if tracks:
        cur = server.state.get("track") or 1
        server.state["track"] = (cur % len(tracks)) + 1
        server.state["position"] = 0.0

    await server.broadcast()
    return web.json_response(server.state)


async def handle_post_previous(request: web.Request) -> web.Response:
    """Handle /api/previous command."""
    server: FakeServerState = request.app[FAKE_SERVER_KEY]
    disc = server.state.get("disc")
    if server.state.get("drive") != "ok" or not isinstance(disc, dict):
        return web.json_response({"error": "No disc in drive"}, status=409)

    tracks = disc.get("tracks", [])
    if tracks:
        cur = server.state.get("track") or 1
        server.state["track"] = len(tracks) if cur <= 1 else cur - 1
        server.state["position"] = 0.0

    await server.broadcast()
    return web.json_response(server.state)


async def handle_post_settings(request: web.Request) -> web.Response:
    """Handle /api/settings command."""
    server: FakeServerState = request.app[FAKE_SERVER_KEY]
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON"}, status=400)

    if not isinstance(data, dict) or not data:
        return web.json_response(
            {"error": "send an object of settings to change"}, status=400
        )

    valid_read_modes = {"raw", "raw_aligned", "paranoia_overlap", "paranoia_full"}
    valid_latency_profiles = {"low", "balanced", "stable"}

    updates: dict[str, Any] = {}
    for name, value in data.items():
        if name == "read_mode":
            if value not in valid_read_modes:
                return web.json_response(
                    {"error": f"Invalid read_mode: {value}"}, status=400
                )
            updates[name] = value
        elif name == "latency_profile":
            if value not in valid_latency_profiles:
                return web.json_response(
                    {"error": f"Invalid latency_profile: {value}"}, status=400
                )
            updates[name] = value
        elif name == "read_speed":
            if (
                isinstance(value, bool)
                or not isinstance(value, int)
                or not (1 <= value <= 24)
            ):
                return web.json_response(
                    {"error": f"Invalid read_speed: {value}"}, status=400
                )
            updates[name] = int(value)
        elif name == "buffer_seconds":
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not (2 <= value <= 60)
            ):
                return web.json_response(
                    {"error": f"Invalid buffer_seconds: {value}"}, status=400
                )
            updates[name] = float(value)
        elif name == "target":
            if (
                not isinstance(value, str)
                or not value.strip()
                or any(c.isspace() for c in value.strip())
            ):
                return web.json_response(
                    {"error": f"Invalid target: {value}"}, status=400
                )
            updates[name] = value.strip()
        elif name == "bluetooth_name":
            if not isinstance(value, str) or not value.strip() or len(value.strip()) > 64:
                return web.json_response(
                    {"error": f"Invalid bluetooth_name: {value}"}, status=400
                )
            updates[name] = value.strip()
        elif name == "bluetooth_auto_switch":
            if not isinstance(value, bool):
                return web.json_response(
                    {"error": f"Invalid bluetooth_auto_switch: {value}"}, status=400
                )
            updates[name] = value
        elif name == "bluetooth_buffer_ms":
            if (
                isinstance(value, bool)
                or not isinstance(value, int)
                or not (20 <= value <= 2000)
            ):
                return web.json_response(
                    {"error": f"Invalid bluetooth_buffer_ms: {value}"}, status=400
                )
            updates[name] = int(value)
        else:
            return web.json_response(
                {"error": f"unknown setting {name!r}"}, status=400
            )

    server.state.setdefault("settings", {}).update(updates)
    await server.broadcast()
    return web.json_response(server.state)


async def handle_post_source(request: web.Request) -> web.Response:
    """Handle /api/source command."""
    server: FakeServerState = request.app[FAKE_SERVER_KEY]
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON"}, status=400)
    source = data.get("source") if isinstance(data, dict) else None
    if source not in ("cd", "bluetooth"):
        return web.json_response(
            {"error": "source must be one of cd, bluetooth"}, status=400
        )
    if source != server.state.get("source"):
        server.state["source"] = source
        server.state["streaming"] = False
    await server.broadcast()
    return web.json_response(server.state)


async def handle_post_bluetooth(request: web.Request) -> web.Response:
    """Handle /api/bluetooth/{pairing,pair,disconnect,forget} commands."""
    server: FakeServerState = request.app[FAKE_SERVER_KEY]
    action = request.match_info["action"]
    try:
        data = await request.json() if request.can_read_body else {}
    except Exception:
        return web.json_response({"error": "Invalid JSON"}, status=400)
    bt = server.state.setdefault("bluetooth", copy.deepcopy(DEFAULT_BLUETOOTH))
    if not bt.get("available"):
        return web.json_response({"error": "Bluetooth is not available"}, status=409)
    if action == "pairing":
        if not isinstance(data, dict) or not isinstance(data.get("on"), bool):
            return web.json_response({"error": '"on" must be true or false'}, status=400)
        bt["pairing"] = data["on"]
        bt["scanning"] = data["on"]
        # While scanning, a device turns up that can only be paired from this side.
        bt["nearby"] = [{"address": "66:77:88:99:AA:BB", "name": "Phone"}] if data["on"] else []
    elif action == "pair":
        address = data.get("address") if isinstance(data, dict) else None
        found = [d for d in bt["nearby"] if d["address"] == address]
        if not found:
            return web.json_response({"error": f"no nearby device {address!r}"}, status=400)
        bt["nearby"] = []
        bt["pairing"] = bt["scanning"] = False
        bt["devices"].append({**found[0], "connected": True})
    elif action == "disconnect":
        for device in bt["devices"]:
            device["connected"] = False
        bt["connected"] = None
    elif action == "forget":
        address = data.get("address") if isinstance(data, dict) else None
        if address is not None and not any(d["address"] == address for d in bt["devices"]):
            return web.json_response({"error": f"no paired device {address!r}"}, status=400)
        bt["devices"] = [
            d for d in bt["devices"] if address is not None and d["address"] != address
        ]
        if not any(d["connected"] for d in bt["devices"]):
            bt["connected"] = None
    else:
        raise web.HTTPNotFound()
    await server.broadcast()
    return web.json_response(server.state)


async def on_cleanup(app: web.Application) -> None:
    """Close all open WebSockets on app cleanup."""
    server: FakeServerState = app[FAKE_SERVER_KEY]
    for ws in list(server.ws_clients):
        await ws.close()


def create_app(
    initial_state: dict[str, Any] | None = None, token: str | None = FAKE_TOKEN
) -> web.Application:
    """Create and configure the aiohttp application. With a token, requests without it are
    rejected, as the add-on does."""

    @web.middleware
    async def require_token(request: web.Request, handler: Any) -> web.StreamResponse:
        if token and request.headers.get("Authorization") != f"Bearer {token}":
            return web.json_response({"error": "missing or wrong API token"}, status=401)
        return await handler(request)

    app = web.Application(middlewares=[require_token])
    app[FAKE_SERVER_KEY] = FakeServerState(initial_state)
    app.on_cleanup.append(on_cleanup)

    app.router.add_get("/api/state", handle_get_state)
    app.router.add_get("/api/ws", handle_ws)
    app.router.add_post("/api/stream", handle_post_stream)
    app.router.add_post("/api/mode", handle_post_mode)
    app.router.add_post("/api/eject", handle_post_eject)
    app.router.add_post("/api/play", handle_post_play)
    app.router.add_post("/api/pause", handle_post_pause)
    app.router.add_post("/api/next", handle_post_next)
    app.router.add_post("/api/previous", handle_post_previous)
    app.router.add_post("/api/settings", handle_post_settings)
    app.router.add_post("/api/source", handle_post_source)
    app.router.add_post("/api/bluetooth/{action}", handle_post_bluetooth)

    return app


def main() -> None:
    """Run the fake daemon server."""
    parser = argparse.ArgumentParser(description="Fake nyxr-companion daemon")
    parser.add_argument(
        "--host",
        default=os.environ.get("FAKE_SERVER_HOST", "0.0.0.0"),
        help="Host to listen on (default: 0.0.0.0)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("FAKE_SERVER_PORT", "8765")),
        help="Port to listen on (default: 8765)",
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true", help="Enable verbose logging"
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    app = create_app()
    _LOGGER.info("Starting fake nyxr-companion server on %s:%d", args.host, args.port)
    web.run_app(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
