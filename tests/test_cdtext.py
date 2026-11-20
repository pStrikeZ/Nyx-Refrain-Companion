"""CD-Text out of what READ TOC format 5 returns, including from a drive that leaves 28
stale bytes between the header and the first pack."""
import importlib.util
import os
import tempfile
import unittest

STATE = tempfile.mkdtemp()
os.environ["NYXR_COMPANION_STATE"] = STATE
os.environ["NYXR_COMPANION_RUN"] = STATE

try:
    spec = importlib.util.spec_from_file_location(
        "nyxr_companion",
        os.path.join(os.path.dirname(__file__), "..", "nyxr_companion", "nyxr_companion.py"),
    )
    daemon = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(daemon)
except ImportError:  # the daemon's own dependencies (dbus-next) are not installed
    daemon = None


def pack(kind, track, seq, text, block=0):
    body = bytes([kind, track, seq, block << 4]) + text.ljust(12, b"\0")
    return body + daemon._crc16_cdtext(body).to_bytes(2, "big")


def packs_for(kind, strings, seq, encoding="latin-1"):
    """The packs holding strings for tracks 0, 1, ... (what the drive reads off the disc)."""
    wide = encoding == "cp932"
    stream = b"".join(s.encode(encoding) + b"\0" * (2 if wide else 1) for s in strings)
    out, track_at = [], 0
    chunks = [stream[i : i + 12] for i in range(0, len(stream), 12)]
    ends = [i for i in range(len(stream)) if stream[i : i + 1] == b"\0"]
    for n, chunk in enumerate(chunks):
        first = n * 12
        # the track of the first string that starts in this chunk (or continues into it)
        track = sum(1 for e in ends if e < first and (not wide or e % 2 == 1)) if not wide else (
            len(stream[:first].split(b"\0\0")) - 1
        )
        out.append(pack(kind, track, seq, chunk))
        seq += 1
    return out, seq


def size_info(charset, last_seq):
    first = bytes([charset, 1, 3, 0, 0, 0, 0, 0, 0, 0, 0, 0])
    return [pack(0x8F, 0, last_seq, first)]


def response(packs, junk, cut=0):
    """4-byte header, `junk` unwritten bytes, the packs; the declared length leaves the junk
    out, so the last bytes never arrive."""
    body = b"".join(packs)
    declared = 2 + len(body)
    data = bytes([declared >> 8, declared & 0xFF, 0, 0]) + junk + body
    return data[: declared + 2 - cut]


@unittest.skipIf(daemon is None, "the daemon's dependencies are not installed")
class CdText(unittest.TestCase):
    TITLES = ["Frost Era", "Intro", "Art For Rest", "There Was A Boy"]
    PERFORMERS = ["ARForest"] * 4

    def build(self, encoding="latin-1", titles=None, performers=None):
        titles = titles or self.TITLES
        performers = performers or self.PERFORMERS
        t, seq = packs_for(0x80, titles, 0, encoding)
        p, seq = packs_for(0x81, performers, seq, encoding)
        charset = 0x80 if encoding == "cp932" else 0
        return t + p + size_info(charset, seq)

    def test_compliant_drive(self):
        meta = daemon.parse_cdtext(daemon.cdtext_packs(response(self.build(), b"")), [1, 2, 3])
        self.assertEqual(meta["album"], "Frost Era")
        self.assertEqual(meta["artist"], "ARForest")
        self.assertEqual([t["title"] for t in meta["tracks"]], ["Intro", "Art For Rest", "There Was A Boy"])
        self.assertEqual({t["artist"] for t in meta["tracks"]}, {"ARForest"})

    def test_drive_with_stale_bytes_before_the_packs(self):
        junk = bytes(range(0x30, 0x30 + 28))  # what the drive's buffer held
        data = response(self.build(), junk, cut=28)  # the packs' last 28 bytes never arrive
        meta = daemon.parse_cdtext(daemon.cdtext_packs(data), [1, 2, 3])
        self.assertEqual(meta["album"], "Frost Era")
        self.assertEqual([t["title"] for t in meta["tracks"]], ["Intro", "Art For Rest", "There Was A Boy"])

    def test_junk_that_looks_like_packs_is_ignored(self):
        junk = bytes([0x80, 0, 0, 0]) + b"x" * 24  # right type, wrong CRC
        meta = daemon.parse_cdtext(daemon.cdtext_packs(response(self.build(), junk)), [1])
        self.assertEqual(meta["tracks"][0]["title"], "Intro")

    def test_block_repeated_on_the_disc(self):
        packs = self.build()
        meta = daemon.parse_cdtext(daemon.cdtext_packs(response(packs + packs, b"")), [1])
        self.assertEqual(meta["tracks"][0]["title"], "Intro")

    def test_same_as_before(self):
        packs, seq = packs_for(0x80, ["Album", "One", "\t", "Three"], 0)
        meta = daemon.parse_cdtext(daemon.cdtext_packs(response(packs, b"")), [1, 2, 3])
        self.assertEqual([t["title"] for t in meta["tracks"]], ["One", "One", "Three"])

    def test_ms_jis(self):
        titles = ["アルバム", "曲一", "曲二"]
        meta = daemon.parse_cdtext(
            daemon.cdtext_packs(response(self.build("cp932", titles, ["歌手"] * 3), b"")), [1, 2]
        )
        self.assertEqual(meta["album"], "アルバム")
        self.assertEqual([t["title"] for t in meta["tracks"]], ["曲一", "曲二"])
        self.assertEqual(meta["tracks"][0]["artist"], "歌手")

    def test_nothing(self):
        self.assertIsNone(daemon.parse_cdtext(daemon.cdtext_packs(b"\0" * 100), [1]))
        self.assertIsNone(daemon.parse_cdtext(daemon.cdtext_packs(b""), [1]))


if __name__ == "__main__":
    unittest.main()
