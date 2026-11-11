"""The disc file system reader against real images (tests/fixtures/discfs)."""

import gzip
import os
import shutil
import struct
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
FIXTURES = os.path.join(HERE, "fixtures", "discfs")
sys.path.insert(0, os.path.join(HERE, "..", "nyxr_companion"))
sys.path.insert(0, FIXTURES)

import discfs  # noqa: E402
from mktree import FILES, content  # noqa: E402

FRAGMENTED = {"frag.wav": 600000}
SECOND_SESSION = {"session2/added.mp3": 7777, "readme.txt": 124}


def last_session_start(path):
    """First block of the last session of an image: 16 before its last primary volume
    descriptor (a drive tells this itself)."""
    start = 0
    with open(path, "rb") as f:
        block = 16
        while True:
            f.seek(block * 2048)
            d = f.read(2048)
            if len(d) < 2048:
                return start
            if d[:6] == b"\x01CD001":
                start = block - 16
            block += 1


def last_written(path):
    """The last block of an image that holds a descriptor: where a disc written in
    packets ends (a drive tells this itself; the image file is longer)."""
    last = 0
    with open(path, "rb") as f:
        block = 0
        while True:
            d = f.read(2048)
            if len(d) < 2048:
                return last
            if discfs._tag(d):
                last = block
            block += 1


class Images(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dir = tempfile.mkdtemp(prefix="discfs-test-")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.dir)

    def open(self, name, **kwargs):
        path = os.path.join(self.dir, name)
        if not os.path.exists(path):
            with gzip.open(os.path.join(FIXTURES, name + ".gz"), "rb") as src:
                with open(path, "wb") as dst:
                    shutil.copyfileobj(src, dst)
        device = discfs.Device(path)
        self.addCleanup(device.close)
        if kwargs.pop("multisession", False):
            kwargs["session_start"] = last_session_start(path)
        if kwargs.pop("packets", False):
            kwargs["last_block"] = last_written(path)
        return discfs.open_volume(device, **kwargs)

    def check(self, volume, expected, exact_names=True):
        """Every file is there with its content; names too unless the format mangles them."""
        self.assertEqual(len(volume.files), len(expected))
        by_size = {f.size: f for f in volume.files}
        self.assertEqual(len(by_size), len(volume.files), "sizes are unique in the fixture")
        for path, size in expected.items():
            self.assertIn(size, by_size, path)
            f = by_size[size]
            if exact_names:
                self.assertEqual(f.path, path)
            self.assertEqual(volume.read(f), content(size), path)
            # A read from the middle, across whatever extents there are.
            if size > 5000:
                self.assertEqual(volume.read(f, 3000, 1500), content(size)[3000:4500], path)
            self.assertEqual(volume.read(f, size, 10), b"")

    def test_iso_joliet_rock_ridge(self):
        v = self.open("iso_joliet_rr.iso")
        self.assertEqual((v.kind, v.label), ("iso9660", "FIXTURE"))
        self.check(v, FILES)

    def test_iso_rock_ridge(self):
        self.check(self.open("iso_rr.iso"), FILES)

    def test_iso_joliet(self):
        v = self.open("iso_joliet.iso")
        self.check(v, FILES, exact_names=False)  # Joliet stops at 64 characters
        names = {f.path for f in v.files}
        self.assertIn("アルバム A/01 曲目.mp3", names)
        self.assertIn("deep/a/b/c/track.opus", names)

    def test_iso_level_1(self):
        v = self.open("iso_level1.iso")
        self.check(v, FILES, exact_names=False)
        names = {f.path for f in v.files}
        self.assertIn("TINY.FLA", names)  # 8.3, no version suffix

    def test_iso_multisession(self):
        v = self.open("iso_multisession.iso", multisession=True)
        expected = dict(FILES, **SECOND_SESSION)
        self.check(v, expected)
        # xorriso also rewrites the first session's descriptors on a rewritable medium
        # (which an image file counts as), so the new tree is found from block 16 too.
        self.check(self.open("iso_multisession.iso"), expected)

    def test_single_extent_span(self):
        v = self.open("iso_joliet_rr.iso")
        f = next(f for f in v.files if f.path == "アルバム A/01 曲目.mp3")
        offset, size = f.span()
        self.assertEqual(v.device.pread(size, offset), content(70000))

    def test_bridge_with_rock_ridge(self):
        # genisoimage cuts the names in its UDF short; its Rock Ridge names are whole.
        v = self.open("bridge_udf102_joliet_rr.iso")
        self.assertEqual(v.kind, "iso9660")
        self.check(v, FILES)

    def test_bridge_udf_side(self):
        path = os.path.join(self.dir, "bridge_udf102_joliet_rr.iso")
        self.open("bridge_udf102_joliet_rr.iso")
        device = discfs.Device(path)
        self.addCleanup(device.close)
        v = discfs._Udf(device, 0, device.blocks - 1).volume()
        self.check(v, FILES, exact_names=False)

    def test_udf_102(self):
        v = self.open("udf102_hd.img")
        self.assertEqual((v.kind, v.label), ("udf", "udf102_hd"))
        self.check(v, dict(FILES, **FRAGMENTED))

    def test_udf_fragmented_file(self):
        v = self.open("udf201_dvdrw.img")
        f = next(f for f in v.files if f.path == "frag.wav")
        self.assertGreater(len(f.extents), 5)
        self.assertIsNone(f.span())

    def test_udf_inline_file(self):
        v = self.open("udf201_dvdram.img")
        f = next(f for f in v.files if f.path == "tiny.flac")
        self.assertEqual(v.read(f), content(10))

    def test_udf_150_sparable(self):
        self.check(self.open("udf150_dvdrw.img"), dict(FILES, **FRAGMENTED))

    def test_udf_201_sparable(self):
        self.check(self.open("udf201_dvdrw.img"), dict(FILES, **FRAGMENTED))

    def test_udf_201(self):
        self.check(self.open("udf201_dvdram.img"), FILES)

    # Linux cannot write these, so the volumes are empty: what is checked is that the
    # root directory is reached through the virtual allocation table.
    def test_udf_250_virtual_partition(self):
        v = self.open("udf250_bdr.img", packets=True)
        self.assertEqual((v.kind, v.label, v.files), ("udf", "udf250_bdr", []))

    def test_udf_150_virtual_partition(self):
        v = self.open("udf150_cdr_vat.img", packets=True)
        self.assertEqual((v.kind, v.label, v.files), ("udf", "udf150_cdr_vat", []))

    def test_udf_201_virtual_partition(self):
        v = self.open("udf201_dvdr_vat.img", packets=True)
        self.assertEqual((v.kind, v.label, v.files), ("udf", "udf201_dvdr_vat", []))

    def test_udf_250_metadata_partition(self):
        """BD-RE and BD-R: the directories lie in a metadata partition, a file inside the
        physical one. No Linux tool writes such a volume with files in it, so this one is
        put together here, descriptor by descriptor (ECMA-167, UDF 2.50 2.2.10)."""
        blocks = {}

        def tag(ident, location, body):
            head = bytearray(struct.pack("<HHBBHHHI", ident, 3, 0, 0, 0, 0, 0, location))
            head[4] = sum(head) & 0xFF
            return bytes(head) + body

        def put(block, data):
            blocks[block] = data.ljust(2048, b"\0")

        def short_ad(length, block):
            return struct.pack("<II", length, block)

        def long_ad(length, block, part):
            return struct.pack("<IIH6x", length, block, part)

        def file_entry(location, file_type, size, form, ads):
            icb = struct.pack("<IHHHBB6xH", 0, 4, 0, 1, 0, file_type, form)  # 20 bytes
            body = icb + bytes(20) + struct.pack("<Q", size) + bytes(104)
            body += struct.pack("<II", 0, len(ads)) + ads
            return tag(261, location, body)

        def fid(location, flags, name, icb):
            ident = b"\x08" + name.encode("latin-1") if name else b""
            body = struct.pack("<HBB", 1, flags, len(ident)) + icb + struct.pack("<H", 0) + ident
            return tag(257, location, body).ljust((38 + len(ident) + 3) & ~3, b"\0")

        start = 300  # the physical partition
        put(256, tag(2, 256, struct.pack("<IIII", 4 * 2048, 32, 0, 0)))
        put(32, tag(1, 32, struct.pack("<I", 0)))
        put(33, tag(5, 33, struct.pack("<IHH", 1, 1, 0).ljust(172, b"\0")
                    + struct.pack("<II", start, 700)))
        physical = struct.pack("<BBHH", 1, 6, 1, 0)
        metadata = (struct.pack("<BB2x", 2, 64) + b"\0" + b"*UDF Metadata Partition"
                    + bytes(8) + struct.pack("<HHIII", 1, 0, 10, 0xFFFFFFFF, 0xFFFFFFFF))
        metadata = metadata.ljust(64, b"\0")
        label = b"\x08METADATA"
        logical = bytearray(tag(6, 34, struct.pack("<I", 2)).ljust(440, b"\0"))
        logical[84:212] = label.ljust(127, b"\0") + bytes([len(label)])
        struct.pack_into("<I", logical, 212, 2048)
        logical[248:264] = long_ad(2048, 0, 1)  # the file set, in the metadata partition
        struct.pack_into("<II", logical, 264, len(physical + metadata), 2)
        put(34, bytes(logical) + physical + metadata)
        put(35, tag(8, 35, b""))
        # The metadata file: metadata blocks 0-1 at partition blocks 20-21, 2-4 at 40-42.
        put(start + 10, file_entry(10, 250, 5 * 2048, 0,
                                   short_ad(2 * 2048, 20) + short_ad(3 * 2048, 40)))
        put(start + 20, tag(256, 0, bytes(384) + long_ad(2048, 2, 1)))  # the file set
        directory = fid(3, 0x0A, "", long_ad(2048, 2, 1)) + fid(3, 0, "song.mp3", long_ad(2048, 4, 1))
        put(start + 40, file_entry(2, 4, len(directory), 0, short_ad(len(directory), 3)))
        put(start + 41, directory)
        put(start + 42, file_entry(4, 5, 5000, 1, long_ad(5000, 100, 0)))
        data = content(5000)
        for i in range(3):
            put(start + 100 + i, data[i * 2048 : (i + 1) * 2048])

        path = os.path.join(self.dir, "metadata.img")
        with open(path, "wb") as f:
            for block, d in blocks.items():
                f.seek(block * 2048)
                f.write(d)
            f.truncate(1100 * 2048)
        device = discfs.Device(path)
        self.addCleanup(device.close)
        v = discfs.open_volume(device)
        self.assertEqual((v.kind, v.label), ("udf", "METADATA"))
        self.assertEqual([f.path for f in v.files], ["song.mp3"])
        self.assertEqual(v.files[0].span(), ((start + 100) * 2048, 5000))
        self.assertEqual(v.read(v.files[0]), data)

    def test_udf_read_only_volume(self):
        self.assertEqual(self.open("udf102_dvd.img").files, [])

    def test_not_a_file_system(self):
        path = os.path.join(self.dir, "zeros.img")
        with open(path, "wb") as f:
            f.truncate(4 << 20)
        device = discfs.Device(path)
        self.addCleanup(device.close)
        with self.assertRaises(discfs.NotRecognized):
            discfs.open_volume(device)


if __name__ == "__main__":
    unittest.main()
