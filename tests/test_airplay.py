"""The daemon's AirPlay source, fed by a fake shairport-sync (PCM and metadata pipes) and
streaming into a fake nyxr (NYXR_COMMAND) instead of a receiver."""
import asyncio
import base64
import importlib.util
import os
import tempfile
import threading
import time
import unittest

STATE = tempfile.mkdtemp()
OUT = os.path.join(STATE, "nyxr-stdin")
os.environ["NYXR_COMPANION_STATE"] = STATE
os.environ["NYXR_COMPANION_RUN"] = STATE
os.environ["NYXR_COMMAND"] = f"sh -c 'cat > {OUT}'"

try:
    spec = importlib.util.spec_from_file_location(
        "nyxr_companion",
        os.path.join(os.path.dirname(__file__), "..", "nyxr_companion", "nyxr_companion.py"),
    )
    daemon = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(daemon)
except ImportError as e:  # the daemon's own dependencies (dbus-next) are not installed
    daemon = None
    SKIP = str(e)


def item(kind, code, data=b""):
    text = f"<item><type>{kind.encode().hex()}</type><code>{code.encode().hex()}</code><length>{len(data)}</length>"
    if data:
        text += f'\n<data encoding="base64">\n{base64.b64encode(data).decode()}</data>'
    return (text + "</item>\n").encode()


class FakeSources:
    def __init__(self, player):
        self.player = player
        self.switched = []

    def set_source(self, source):
        self.switched.append(source)
        self.player.source = source


class FakePlayer:
    def __init__(self, loop):
        self.loop = loop
        self.source = "cd"
        self.sources = FakeSources(self)

    def changed(self):
        pass


def wait_for(check, timeout=5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if check():
            return True
        time.sleep(0.02)
    return False


@unittest.skipIf(daemon is None, "daemon dependencies missing")
class AirPlayTest(unittest.TestCase):
    def setUp(self):
        self.loop = asyncio.new_event_loop()
        threading.Thread(target=self.loop.run_forever, daemon=True).start()
        self.player = FakePlayer(self.loop)
        self.airplay = daemon.AirPlay(self.loop, self.player)
        if os.path.exists(OUT):
            os.unlink(OUT)

    def tearDown(self):
        self.airplay.stop()
        self.loop.call_soon_threadsafe(self.loop.stop)

    def test_metadata(self):
        pipe = os.path.join(STATE, "metadata")
        if os.path.exists(pipe):
            os.unlink(pipe)
        os.mkfifo(pipe)
        threading.Thread(target=self.airplay._read_metadata, args=(pipe,), daemon=True).start()
        cover = b"\xff\xd8\xff\xe0" + bytes(100)
        with open(pipe, "wb") as f:
            for chunk in (
                item("ssnc", "abeg"),
                item("ssnc", "snam", "iPhone".encode()),
                item("ssnc", "mdst"),
                item("core", "minm", "Ref:rain".encode()),
                item("core", "asar", "Limonène".encode()),
                item("core", "asal", "maimai".encode()),
                item("core", "astm", (215000).to_bytes(4, "big")),
                item("ssnc", "mden"),
                item("ssnc", "PICT", cover),
                item("ssnc", "prgr", f"{2**32 - 44100}/{44100}/{44100 * 9}".encode()),
            ):
                f.write(chunk[:20])  # items arrive in pieces
                f.flush()
                f.write(chunk[20:])
                f.flush()
        self.assertTrue(wait_for(lambda: self.airplay.progress is not None))
        info = self.airplay.track_info()
        self.assertEqual(info["title"], "Ref:rain")
        self.assertEqual(info["artist"], "Limonène")
        self.assertEqual(info["album"], "maimai")
        self.assertEqual(info["length"], 215)
        self.assertTrue(info["cover_url"].startswith("file://"))
        with open(info["cover_url"][len("file://"):], "rb") as f:
            self.assertEqual(f.read(), cover)
        self.assertAlmostEqual(self.airplay.position(), 2.0, places=1)  # RTP time wraps
        self.assertEqual(self.airplay.snapshot()["connected"], "iPhone")

        self.airplay._on_metadata(b"ssnc", b"aend", b"")
        self.assertIsNone(self.airplay.track_info())
        self.assertEqual(self.airplay.playback, "stopped")

    def test_stream_and_auto_switch(self):
        daemon.SETTINGS.values["airplay_auto_switch"] = True
        self.airplay.active = True
        r, w = os.pipe()
        threading.Thread(target=self.airplay._read_pcm, args=(os.fdopen(r, "rb"),), daemon=True).start()
        threading.Thread(target=self.airplay._pump_silence, daemon=True).start()

        pcm = bytes(range(256)) * 64  # 16 KiB, frame aligned
        os.write(w, pcm)
        # The first audio switches the source and starts the stream.
        self.assertTrue(wait_for(lambda: self.airplay.streaming))
        self.assertEqual(self.player.sources.switched, ["airplay"])
        self.assertEqual(self.airplay.playback, "playing")
        os.write(w, pcm)
        # Then the sender pauses: silence keeps the stream going.
        self.assertTrue(wait_for(lambda: self.airplay.playback == "paused", timeout=3))
        time.sleep(0.3)
        self.airplay.stop()
        os.close(w)
        with open(OUT, "rb") as f:
            out = f.read()
        self.assertEqual(len(out) % daemon.AIRPLAY_FRAME_BYTES, 0)
        self.assertIn(pcm, out)
        self.assertTrue(out.endswith(bytes(4 * 1000)))  # silence after the pause
        # Audio written while nyxr was starting is dropped, never more than was sent.
        self.assertGreater(len(out), len(pcm))

    def test_no_sender(self):
        with self.assertRaises(daemon.Conflict):
            self.airplay.start_stream()


if __name__ == "__main__":
    unittest.main()
