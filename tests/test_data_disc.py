"""The daemon playing a data disc: a directory stands in for the disc
(NYXR_COMPANION_FAKE_DATA_DISC), ffmpeg decodes for real, and a fake nyxr keeps what it
is sent. Needs ffmpeg and the daemon's dependencies; skipped without them."""
import asyncio
import importlib.util
import os
import shutil
import subprocess
import tempfile
import threading
import time
import unittest
from unittest import mock

STATE = tempfile.mkdtemp()
DISC = os.path.join(STATE, "MY DISC")
OUT = os.path.join(STATE, "data-nyxr-stdin")
os.environ["NYXR_COMPANION_STATE"] = STATE
os.environ["NYXR_COMPANION_RUN"] = STATE
os.environ["NYXR_COMPANION_FAKE_DATA_DISC"] = DISC

try:
    spec = importlib.util.spec_from_file_location(
        "nyxr_companion",
        os.path.join(os.path.dirname(__file__), "..", "nyxr_companion", "nyxr_companion.py"),
    )
    daemon = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(daemon)
except ImportError:  # the daemon's own dependencies (dbus-next) are not installed
    daemon = None
finally:
    del os.environ["NYXR_COMPANION_FAKE_DATA_DISC"]  # the other tests' daemons have no disc

FFMPEG = shutil.which("ffmpeg") and shutil.which("ffprobe")
SECOND = 44100 * 4  # bytes of PCM


def ffmpeg(*args):
    subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-y", *args], check=True)


def tone(path, seconds=1.0, **tags):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    meta = [a for k, v in tags.items() for a in ("-metadata", f"{k}={v}")]
    ffmpeg("-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}", *meta, path)


def make_disc():
    tone(f"{DISC}/Album B/2 second.flac", title="Second", artist="Someone", album="The Album")
    tone(f"{DISC}/Album B/10 tenth.flac")
    tone(f"{DISC}/Album B/1 first.wav")
    tone(f"{DISC}/Album A/song.ogg", title="Ogg Song")
    tone(f"{DISC}/root.flac", 2.0, title="Root Song")
    ffmpeg("-f", "lavfi", "-i", "color=red:s=32x32", "-frames:v", "1", f"{DISC}/Album B/cover.jpg")
    # A picture inside the file wins over the folder's.
    ffmpeg("-f", "lavfi", "-i", "color=blue:s=32x32", "-frames:v", "1", f"{STATE}/picture.png")
    ffmpeg("-i", f"{DISC}/Album B/2 second.flac", "-i", f"{STATE}/picture.png",
           "-map", "0:a", "-map", "1:v", "-c", "copy", "-disposition:v", "attached_pic",
           f"{DISC}/Album B/tmp.flac")
    os.replace(f"{DISC}/Album B/tmp.flac", f"{DISC}/Album B/2 second.flac")
    with open(f"{DISC}/Album A/broken.mp3", "wb") as f:
        f.write(b"this is not music " * 500)
    with open(f"{DISC}/notes.txt", "w") as f:
        f.write("not music")


def wait_for(check, timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if check():
            return True
        time.sleep(0.02)
    return False


@unittest.skipIf(daemon is None, "daemon dependencies missing")
@unittest.skipIf(not FFMPEG, "ffmpeg missing")
class DataDiscTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        make_disc()

    def setUp(self):
        self.loop = asyncio.new_event_loop()
        threading.Thread(target=self.loop.run_forever, daemon=True).start()
        patch = mock.patch.object(
            daemon, "nyxr_command", lambda **kw: ["sh", "-c", f"cat > '{OUT}'"]
        )
        patch.start()
        self.addCleanup(patch.stop)
        daemon.SETTINGS.values["target"] = "192.0.2.1:7000"
        self.player = daemon.Player(self.loop)
        self.disc = daemon.scan_data_disc()
        self.player.set_drive("ok")
        self.player.data_loaded(self.disc)

    def tearDown(self):
        self.player.stop()
        self.loop.call_soon_threadsafe(self.loop.stop)

    def titles(self):
        return [t["title"] for t in self.player.disc["tracks"]]

    def test_listing(self):
        # Files of the root first, then folder by folder; "2" before "10"; only music.
        self.assertEqual(
            [f.path for f in self.disc.tracks],
            ["root.flac", "Album A/broken.mp3", "Album A/song.ogg",
             "Album B/1 first.wav", "Album B/2 second.flac", "Album B/10 tenth.flac"],
        )
        disc = self.player.snapshot()["disc"]
        self.assertEqual((disc["kind"], disc["label"], disc["track_count"]), ("data", "MY DISC", 6))
        self.assertEqual(disc["folder"], "")
        self.assertEqual(
            disc["folders"],
            [{"name": "", "first": 1, "tracks": 1}, {"name": "Album A", "first": 2, "tracks": 2},
             {"name": "Album B", "first": 4, "tracks": 3}],
        )
        # Only the folder being played is listed; names stand in for titles until played.
        self.assertEqual(disc["tracks"], [{"number": 1, "title": "root", "artist": None,
                                           "album": "MY DISC", "length": None, "cover_url": None}])
        self.assertTrue(disc["id"].startswith("data-"))

    def test_play_reads_tags_and_goes_on(self):
        self.player.play()
        self.assertTrue(wait_for(lambda: self.player.track == 1 and self.titles()[0] == "Root Song"))
        info = self.player.track_info()
        self.assertEqual((info["title"], info["length"], info["album"]), ("Root Song", 2.0, "MY DISC"))
        # The broken file is skipped; the next one plays.
        self.assertTrue(wait_for(lambda: self.player.track == 3))
        self.assertEqual(self.player.track_info()["title"], "Ogg Song")
        self.assertIn("broken.mp3", self.player.error)
        self.assertEqual(self.player.snapshot()["disc"]["folder"], "Album A")
        self.assertTrue(wait_for(lambda: self.player.track == 5))
        self.player.stop()
        with open(OUT, "rb") as f:
            out = f.read()
        # root (2 s), the ogg and the wav (1 s each), sample for sample, plus silence.
        self.assertGreaterEqual(len(out.replace(bytes(4096), b"")), int(3.9 * SECOND))

    def test_folder_and_pictures(self):
        self.player.play_folder("Album B")
        self.assertTrue(wait_for(lambda: self.player.track == 4 and self.player.lengths[3]))
        first = self.player.track_info()
        self.assertEqual((first["title"], first["album"]), ("1 first", "Album B"))
        with open(first["cover_url"][len("file://"):], "rb") as f:
            self.assertEqual(f.read(3), b"\xff\xd8\xff")  # the folder's cover.jpg
        self.player.next()
        self.assertTrue(wait_for(lambda: self.player.track == 5 and self.titles()[4] == "Second"))
        second = self.player.track_info()
        self.assertEqual((second["artist"], second["album"]), ("Someone", "The Album"))
        with open(second["cover_url"][len("file://"):], "rb") as f:
            self.assertEqual(f.read(4), b"\x89PNG")  # its own picture
        tracks = self.player.snapshot()["disc"]["tracks"]
        self.assertEqual([t["number"] for t in tracks], [4, 5, 6])
        with self.assertRaises(daemon.BadRequest):
            self.player.play_folder("No Such Folder")

    def test_seek(self):
        self.player.play(1)
        self.assertTrue(wait_for(lambda: self.player.track == 1 and self.player.lengths[0]))
        self.player.pause()
        self.player.seek(1.5)
        self.assertAlmostEqual(self.player.position(), 1.5, places=1)
        self.player.seek(99)  # past the end: held to the last moment of the file
        self.assertLess(self.player.position(), 2.0)

    def test_file_in_pieces(self):
        # A fragmented file cannot be opened by ffmpeg itself: it is handed over a pipe.
        with mock.patch.object(self.disc, "where", lambda f: None):
            self.player.play(5)
            self.assertTrue(wait_for(lambda: self.player.track == 5 and self.titles()[4] == "Second"))
            self.assertTrue(wait_for(lambda: self.player.track == 6))


if __name__ == "__main__":
    unittest.main()
