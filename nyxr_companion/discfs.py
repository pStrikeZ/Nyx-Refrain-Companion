"""Reads the file system of a data disc (CD, DVD, BD) straight from the drive, without
mounting it: ISO 9660 (with Joliet and Rock Ridge names, multi-extent files, multisession
discs) and UDF 1.02 to 2.60 (plain, sparable, virtual (VAT) and metadata partitions).

Nothing is mounted because Home Assistant OS's kernel has no UDF, and mounting from an
add-on needs privileges the add-on otherwise does without. Read-only, pure Python.

    volume = open_volume(Device("/dev/sr0"), session_start=..., last_block=...)
    for f in volume.files: ...           # DiscFile: path, size, extents
    volume.read(f, offset, size)

Every file comes with where its data lies on the disc (`extents`), so a decoder can be
pointed at the drive itself.
"""

import ctypes
import fcntl
import os
import struct
import time

BLOCK = 2048  # every optical format uses 2048-byte sectors
MAX_DIR_BYTES = 32 << 20  # a directory larger than this is taken for damage
MAX_FILES = 100_000
MAX_DEPTH = 64


class NotRecognized(Exception):
    """No file system this module reads."""


class _SgHeader(ctypes.Structure):  # struct sg_io_hdr
    _fields_ = [("interface_id", ctypes.c_int), ("dxfer_direction", ctypes.c_int),
                ("cmd_len", ctypes.c_ubyte), ("mx_sb_len", ctypes.c_ubyte),
                ("iovec_count", ctypes.c_ushort), ("dxfer_len", ctypes.c_uint),
                ("dxferp", ctypes.c_void_p), ("cmdp", ctypes.c_void_p),
                ("sbp", ctypes.c_void_p), ("timeout", ctypes.c_uint),
                ("flags", ctypes.c_uint), ("pack_id", ctypes.c_int),
                ("usr_ptr", ctypes.c_void_p), ("status", ctypes.c_ubyte),
                ("masked_status", ctypes.c_ubyte), ("msg_status", ctypes.c_ubyte),
                ("sb_len_wr", ctypes.c_ubyte), ("host_status", ctypes.c_ushort),
                ("driver_status", ctypes.c_ushort), ("resid", ctypes.c_int),
                ("duration", ctypes.c_uint), ("info", ctypes.c_uint)]


class Device:
    """A drive or an image file, read in 2048-byte blocks."""

    def __init__(self, path):
        self.fd = os.open(path, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0))
        try:
            self.blocks = os.lseek(self.fd, 0, os.SEEK_END) // BLOCK
        except OSError:
            self.blocks = 0
        # What the kernel lets through the block device. A drive may report less than the
        # disc holds (a DVD-R seen to report 371 MB of 4.3 GB), and what lies beyond is read
        # with READ (12) by hand once `extend` is told where the disc really ends.
        self.kernel_blocks = self.blocks
        self.last_error = None

    def extend(self, last_block):
        """The disc holds blocks up to `last_block`, whatever the block device reports."""
        if last_block + 1 > self.blocks:
            self.blocks = last_block + 1

    def read(self, block, count=1):
        """`count` blocks from `block`; short (or empty) past the end or on a read error."""
        return self.pread(count * BLOCK, block * BLOCK)

    def pread(self, size, offset):
        out = bytearray()
        try:
            while len(out) < size:
                pos = offset + len(out)
                if self.blocks > self.kernel_blocks and pos // BLOCK >= self.kernel_blocks:
                    data = self._sg_read(pos, size - len(out))
                else:
                    want = size - len(out)
                    if self.blocks > self.kernel_blocks:  # do not run over the kernel's end
                        want = min(want, self.kernel_blocks * BLOCK - pos)
                    data = os.pread(self.fd, want, pos)
                if not data:
                    break
                out += data
        except OSError:
            pass
        return bytes(out)

    def _sg_read(self, offset, size):
        """READ (12) through SG_IO: up to 64 blocks from the one holding `offset`."""
        block, skip = divmod(offset, BLOCK)
        count = min(64, -(-(skip + size) // BLOCK), self.blocks - block)
        if count <= 0:
            return b""
        buf = ctypes.create_string_buffer(count * BLOCK)
        cdb = ctypes.create_string_buffer(struct.pack(">BBIIBB", 0xA8, 0, block, count, 0, 0), 12)
        sense = ctypes.create_string_buffer(32)
        for attempt in range(20):
            hdr = _SgHeader(ord("S"), -3, 12, 32, 0, count * BLOCK, ctypes.addressof(buf),
                            ctypes.addressof(cdb), ctypes.addressof(sense), 30000)
            fcntl.ioctl(self.fd, 0x2285, hdr)  # SG_IO
            if not (hdr.status or hdr.host_status or hdr.driver_status & 0xF):
                break
            # A drive that has spun down answers NOT READY / UNIT ATTENTION while it comes
            # back (the block device waits for it, SG_IO does not); a bad sector does not
            # get better, so only a retry or two.
            key = sense.raw[2] & 0xF if hdr.sb_len_wr > 2 else 0
            asc = sense.raw[12:14].hex() if hdr.sb_len_wr > 13 else ""
            self.last_error = (f"block {block}: SCSI status {hdr.status:#x}, host {hdr.host_status}, "
                               f"driver {hdr.driver_status:#x}, sense key {key} {asc}")
            if key not in (2, 6) and attempt >= 2:
                return b""
            time.sleep(0.5)
        else:
            return b""
        return buf.raw[skip : skip + size]

    def close(self):
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None


class DiscFile:
    """A file on the disc. `extents` is [(byte offset on the device, or None for a hole
    that reads as zeros, length in bytes)]; a small UDF file may instead be `inline`, its
    data held in the file's own descriptor."""

    __slots__ = ("path", "size", "extents", "inline")

    def __init__(self, path, size, extents=(), inline=None):
        self.path = path
        self.size = size
        self.extents = list(extents)
        self.inline = inline

    @property
    def name(self):
        return self.path.rsplit("/", 1)[-1]

    def span(self):
        """(offset, size) on the device if the whole file is one run of bytes there."""
        if self.inline is None and len(self.extents) == 1 and self.extents[0][0] is not None:
            return self.extents[0][0], self.size
        return None

    def __repr__(self):
        return f"DiscFile({self.path!r}, {self.size})"


class Volume:
    def __init__(self, device, kind, label, files):
        self.device = device
        self.kind = kind  # "udf" or "iso9660"
        self.label = label
        self.files = files

    def read(self, f, offset=0, size=None):
        """Up to `size` bytes of `f` from `offset` (all of it by default). Short only if
        the disc could not be read."""
        if size is None or offset + size > f.size:
            size = max(0, f.size - offset)
        if f.inline is not None:
            return f.inline[offset : offset + size]
        out = bytearray()
        start = 0  # where the extent begins in the file
        for where, length in f.extents:
            if len(out) >= size:
                break
            if offset + len(out) < start + length:
                skip = offset + len(out) - start
                want = min(length - skip, size - len(out))
                if where is None:
                    out += bytes(want)
                else:
                    data = self.device.pread(want, where + skip)
                    out += data
                    if len(data) < want:
                        break
            start += length
        return bytes(out)


def open_volume(device, session_start=0, last_block=None):
    """The disc's file system. `session_start` is the first block of the last session
    (multisession discs; 0 otherwise) and `last_block` the last block written, where a
    disc written in packets keeps its newest table (the device's size by default).
    On a disc carrying both, ISO 9660 is read if it has Rock Ridge names (the tools that
    write those cut the names in their UDF short), and UDF otherwise."""
    if last_block is None:
        last_block = device.blocks - 1
    errors = []
    iso = _Iso9660(device, session_start, last_block)
    try:
        rock_ridge = iso.probe()
    except (NotRecognized, struct.error, IndexError):
        rock_ridge = False
    for reader in (iso, _Udf) if rock_ridge else (_Udf, iso):
        try:
            if reader is _Udf:
                reader = _Udf(device, session_start, last_block)
            return reader.volume()
        except NotRecognized as e:
            errors.append(str(e))
        except (struct.error, IndexError, ValueError, RecursionError) as e:
            errors.append(f"{type(reader).__name__[1:]}: damaged ({e!r})")
    raise NotRecognized("; ".join(errors))


# -- ISO 9660 ---------------------------------------------------------------------------

JOLIET_ESCAPES = (b"%/@", b"%/C", b"%/E")


class _Iso9660:
    def __init__(self, device, session_start, last_block):
        self.dev = device
        self.session_start = session_start
        self.files = []
        self.seen = set()

    def probe(self):
        """Reads the volume descriptors; returns whether the disc has Rock Ridge names."""
        self._descriptors()
        root = self.primary[156:190]
        for rec, use in self._records(_le32(root, 2) + root[1], min(_le32(root, 10), BLOCK)):
            # "SP" in the root's own entry starts Rock Ridge, and says how far into every
            # system use field its entries begin (CD-ROM XA discs).
            for skip in (0, 14):
                if use[skip : skip + 2] == b"SP" and len(use) >= skip + 7:
                    self.susp_skip = use[skip + 6]
                    self.rock_ridge = True
            break
        return self.rock_ridge

    def _descriptors(self):
        primary = joliet = None
        for i in range(16, 16 + 64):
            d = self.dev.read(self.session_start + i)
            if len(d) < BLOCK or d[1:6] != b"CD001":
                break
            if d[0] == 255:
                break
            if d[0] == 1 and primary is None:
                primary = d
            elif d[0] == 2 and d[88:91] in JOLIET_ESCAPES:
                joliet = d
        if primary is None:
            raise NotRecognized("no ISO 9660 volume descriptor")
        self.primary, self.supplementary = primary, joliet
        self.susp_skip = 0
        self.rock_ridge = False

    def volume(self):
        if getattr(self, "primary", None) is None:
            self.probe()
        primary = self.primary
        # Rock Ridge names are whole; Joliet's stop at 64 characters.
        self.joliet = self.supplementary is not None and not self.rock_ridge
        vd = self.supplementary if self.joliet else primary
        root = vd[156:190]
        self._walk(_le32(root, 2) + root[1], _le32(root, 10), "", 0)
        if self.joliet:
            label = vd[40:72].decode("utf-16-be", "replace")
        else:
            label = primary[40:72].decode("ascii", "replace")
        return Volume(self.dev, "iso9660", label.strip(" \0"), self.files)

    def _records(self, block, size):
        """The directory's records: (record bytes, system use bytes)."""
        if size > MAX_DIR_BYTES:
            return
        data = self.dev.read(block, -(-size // BLOCK))[:size]
        pos = 0
        while pos < len(data):
            length = data[pos]
            if length == 0:  # records do not cross blocks; the rest of this one is padding
                pos = (pos // BLOCK + 1) * BLOCK
                continue
            rec = data[pos : pos + length]
            pos += length
            if len(rec) < 34:
                break
            name_end = 33 + rec[32]
            yield rec, rec[name_end + (name_end & 1) :]

    def _system_use(self, use):
        """Rock Ridge fields of a record: (name, child directory block, relocated)."""
        name, child, relocated, more = b"", None, False, True
        hops = 0
        while use is not None and hops < 8:
            cont = None
            pos = 0
            while pos + 4 <= len(use):
                sig, length = use[pos : pos + 2], use[pos + 2]
                if length < 4 or pos + length > len(use):
                    break
                body = use[pos + 4 : pos + length]
                if sig == b"NM" and body and more:
                    flags = body[0]
                    if not flags & 0x06:  # not "." or ".."
                        name += body[1:]
                    more = bool(flags & 0x01)
                elif sig == b"CL" and len(body) >= 4:
                    child = _le32(body, 0)
                elif sig == b"RE":
                    relocated = True
                elif sig == b"CE" and len(body) >= 24:
                    cont = (_le32(body, 0), _le32(body, 8), _le32(body, 16))
                elif sig == b"ST":
                    break
                pos += length
            use = None
            if cont:
                block, offset, length = cont
                use = self.dev.pread(length, block * BLOCK + offset)
                hops += 1
        return name, child, relocated

    def _walk(self, block, size, path, depth):
        if block in self.seen or depth > MAX_DEPTH:
            return
        self.seen.add(block)
        pending = None  # a file in several extents: (name, [extents], size so far)
        for rec, use in self._records(block, size):
            raw = rec[33 : 33 + rec[32]]
            flags = rec[25]
            if raw in (b"\x00", b"\x01"):
                continue
            rr_name, child, relocated = (
                self._system_use(use[self.susp_skip :]) if self.rock_ridge else (b"", None, False)
            )
            if relocated:
                continue  # a deep directory moved here; listed where it belongs (CL)
            if self.joliet:
                name = raw.decode("utf-16-be", "replace")
            elif rr_name:
                name = rr_name.decode("utf-8", "replace")
            else:
                name = raw.decode("ascii", "replace")
            if not rr_name:
                name = name.split(";")[0]
                if not self.joliet:
                    name = name.rstrip(".")
            full = f"{path}/{name}" if path else name
            start, length = _le32(rec, 2) + rec[1], _le32(rec, 10)
            if child is not None:
                first = self.dev.read(child)
                if len(first) >= 34:
                    self._walk(child, _le32(first, 10), full, depth + 1)
            elif flags & 0x02:
                self._walk(start, length, full, depth + 1)
            else:
                if pending and pending[0] != full:
                    pending = None
                extents = pending[1] if pending else []
                total = (pending[2] if pending else 0) + length
                extents.append((start * BLOCK, length))
                if flags & 0x80:  # more extents follow
                    pending = (full, extents, total)
                    continue
                pending = None
                if len(self.files) >= MAX_FILES:
                    return
                self.files.append(DiscFile(full, total, extents if total else []))


# -- UDF --------------------------------------------------------------------------------

TAG_PRIMARY, TAG_ANCHOR, TAG_POINTER, TAG_PARTITION, TAG_LOGICAL, TAG_TERMINATOR = 1, 2, 3, 5, 6, 8
TAG_FSD, TAG_FID, TAG_ALLOC_EXTENT, TAG_INDIRECT, TAG_FE, TAG_EFE = 256, 257, 258, 259, 261, 266
FILE_TYPE_DIRECTORY, FILE_TYPE_VAT = 4, 248
UNMAPPED = 0xFFFFFFFF


def _le16(b, o):
    return struct.unpack_from("<H", b, o)[0]


def _le32(b, o):
    return struct.unpack_from("<I", b, o)[0]


def _le64(b, o):
    return struct.unpack_from("<Q", b, o)[0]


def _tag(data, ident=None):
    """The descriptor tag's identifier, or None if the block is not a descriptor."""
    if len(data) < 16:
        return None
    if (sum(data[0:4]) + sum(data[5:16])) & 0xFF != data[4]:
        return None
    found = _le16(data, 0)
    if found == 0 or (ident is not None and found != ident):
        return None
    return found


def _dstring(field):
    """An OSTA CS0 d-string: a fixed field whose last byte is the length used."""
    used = field[-1]
    return _cs0(field[: min(used, len(field) - 1)])


def _cs0(data):
    if not data:
        return ""
    if data[0] == 16:
        return data[1:].decode("utf-16-be", "replace")
    return data[1:].decode("latin-1")  # 8: one byte per character, code points 0-255


class _Udf:
    def __init__(self, device, session_start, last_block):
        self.dev = device
        self.session_start = session_start
        self.last_block = last_block
        self.files = []
        self.seen = set()

    # -- volume structure ---------------------------------------------------------------

    def volume(self):
        anchor = self._anchor()
        self.partitions = {}  # partition number: (first block, blocks)
        primary = logical = None
        best = {}
        for which in (16, 24):  # the main sequence, then the reserve
            length, start = _le32(anchor, which), _le32(anchor, which + 4)
            hops = 0
            i = 0
            while i < length // BLOCK and hops < 16:
                d = self.dev.read(start + i)
                i += 1
                ident = _tag(d)
                if ident is None or ident == TAG_TERMINATOR:
                    break
                if ident == TAG_POINTER:
                    length, start, i = _le32(d, 20), _le32(d, 24), 0
                    hops += 1
                    continue
                seq = _le32(d, 16)
                if ident == TAG_PARTITION:
                    number = _le16(d, 22)
                    if seq >= best.get(("p", number), -1):
                        best["p", number] = seq
                        self.partitions[number] = (_le32(d, 188), _le32(d, 192))
                elif ident == TAG_PRIMARY and seq >= best.get("v", -1):
                    best["v"], primary = seq, d
                elif ident == TAG_LOGICAL and seq >= best.get("l", -1):
                    best["l"], logical = seq, d
            if logical is not None and self.partitions:
                break
        if logical is None or not self.partitions:
            raise NotRecognized("UDF: no logical volume")
        if _le32(logical, 212) != BLOCK:
            raise NotRecognized("UDF: block size is not 2048")
        self._partition_maps(logical)
        fsd_len, fsd_block, fsd_part = _le32(logical, 248), _le32(logical, 252), _le16(logical, 256)
        fsd = None
        for i in range(max(1, (fsd_len & 0x3FFFFFFF) // BLOCK)):
            d = self._block(fsd_part, fsd_block + i)
            if _tag(d, TAG_FSD):
                fsd = d
            elif fsd is not None:
                break
        if fsd is None:
            raise NotRecognized("UDF: no file set")
        self._walk((_le16(fsd, 408), _le32(fsd, 404)), "", 0)
        label = _dstring(logical[84:212]) or (_dstring(primary[24:56]) if primary else "")
        return Volume(self.dev, "udf", label.strip(), self.files)

    def _anchor(self):
        last = self.last_block
        # 256 into the (last) session, then counted from the end; drives report the end
        # with the run-out blocks of a track (2, 7) or the lead-out gap (150) included.
        places = [self.session_start + 256, 256, 512]
        for back in (0, 2, 7, 150, 152):
            places += [last - back, last - back - 256]
        tried = set()
        for block in places:
            if block < 0 or block in tried:
                continue
            tried.add(block)
            d = self.dev.read(block)
            if _tag(d, TAG_ANCHOR) and _le32(d, 12) == block:
                return d
        raise NotRecognized("UDF: no anchor")

    def _partition_maps(self, logical):
        table_len, count = _le32(logical, 264), _le32(logical, 268)
        table = logical[440 : 440 + table_len]
        self.maps = []
        pos = 0
        for _ in range(count):
            if pos + 2 > len(table):
                break
            kind, length = table[pos], table[pos + 1]
            m = table[pos : pos + length]
            pos += length
            if kind == 1 and length >= 6:
                self.maps.append({"kind": "physical", "number": _le16(m, 4)})
            elif kind == 2 and length >= 40:
                ident = m[5:28]
                entry = {"number": _le16(m, 38)}
                if ident.startswith(b"*UDF Virtual Partition"):
                    entry["kind"] = "virtual"
                elif ident.startswith(b"*UDF Sparable Partition"):
                    entry["kind"] = "sparable"
                    entry["packet"] = _le16(m, 40) or 32
                    entry["tables"] = [_le32(m, 48 + 4 * i) for i in range(min(m[42], 4))]
                elif ident.startswith(b"*UDF Metadata Partition"):
                    entry["kind"] = "metadata"
                    entry["file"], entry["mirror"] = _le32(m, 40), _le32(m, 44)
                else:
                    entry["kind"] = "unknown"
                self.maps.append(entry)
            else:
                self.maps.append({"kind": "unknown", "number": None})
            if length == 0:
                break
        if not self.maps:
            raise NotRecognized("UDF: no partition maps")
        for m in self.maps:
            if m["kind"] in ("physical", "sparable") and m["number"] not in self.partitions:
                raise NotRecognized("UDF: partition map without its partition")
        for index, m in enumerate(self.maps):
            if m["kind"] == "sparable":
                m["spared"] = self._sparing_table(m)
            elif m["kind"] in ("virtual", "metadata"):
                # Laid over the partition of the same number that holds the blocks.
                under = [
                    i
                    for i, o in enumerate(self.maps)
                    if o["kind"] in ("physical", "sparable") and o["number"] == m["number"]
                ]
                if not under:
                    raise NotRecognized(f"UDF: {m['kind']} partition over nothing")
                m["under"] = under[0]
        # Tables that are themselves files on the disc, read once the maps they lie on
        # are usable: the virtual one first (a metadata file is never found through it).
        for m in self.maps:
            if m["kind"] == "virtual":
                m["table"] = self._vat(m)
        for m in self.maps:
            if m["kind"] == "metadata":
                m["extents"] = self._metadata_extents(m)

    def _sparing_table(self, m):
        """{first block of a packet (in the partition): block on the disc it was moved to}"""
        for block in m["tables"]:
            d = self.dev.read(block, 1)
            if _tag(d) is None or not d[17:40].startswith(b"*UDF Sparing Table"):
                continue
            count = _le16(d, 48)
            d = self.dev.read(block, -(-(56 + 8 * count) // BLOCK))
            spared = {}
            for i in range(count):
                if 64 + 8 * i > len(d):
                    break
                original, mapped = _le32(d, 56 + 8 * i), _le32(d, 60 + 8 * i)
                if original < 0xFFFFFFF0:
                    spared[original] = mapped
            return spared
        return {}

    def _vat(self, m):
        """The virtual allocation table: the newest one is the last thing written."""
        under = m["under"]
        start, _ = self.partitions[self.maps[under]["number"]]
        last = self.last_block
        for back in list(range(0, 8)) + [150, 151, 152, 153, 154, 155, 156, 157]:
            block = last - back
            if block < start:
                break
            d = self.dev.read(block)
            if _tag(d) not in (TAG_FE, TAG_EFE):
                continue
            file_type = d[16 + 11]
            try:
                entry = self._entry(under, block - start, d)
            except (ValueError, struct.error):
                continue
            data = self._entry_data(entry)
            if file_type == FILE_TYPE_VAT:  # UDF 2.00 and later: a header, then the table
                if len(data) < 4:
                    continue
                table = data[_le16(data, 0) :]
            elif len(data) >= 36 and data[-35:-13].startswith(b"*UDF Virtual Alloc Tbl"):
                table = data[:-36]  # UDF 1.50: the table, then an identifier and a pointer
            else:
                continue
            return struct.unpack(f"<{len(table) // 4}I", table[: len(table) // 4 * 4])
        raise NotRecognized("UDF: no virtual allocation table")

    def _metadata_extents(self, m):
        for where in (m["file"], m["mirror"]):
            if where == UNMAPPED:
                continue
            d = self._block(m["under"], where)
            if _tag(d) not in (TAG_FE, TAG_EFE):
                continue
            try:
                entry = self._entry(m["under"], where, d)
            except (ValueError, struct.error):
                continue
            if entry["extents"]:
                return entry["extents"]
        raise NotRecognized("UDF: no metadata file")

    # -- addresses ----------------------------------------------------------------------

    def _locate(self, part, block):
        """Where on the disc block `block` of partition map `part` is, or None."""
        if part >= len(self.maps):
            return None
        m = self.maps[part]
        kind = m["kind"]
        if kind == "physical":
            return self.partitions[m["number"]][0] + block
        if kind == "sparable":
            packet = block - block % m["packet"]
            moved = m["spared"].get(packet)
            if moved is not None:
                return moved + block % m["packet"]
            return self.partitions[m["number"]][0] + block
        if kind == "virtual":
            if block >= len(m["table"]) or m["table"][block] == UNMAPPED:
                return None
            return self._locate(m["under"], m["table"][block])
        if kind == "metadata":
            at = block * BLOCK
            for where, length in m["extents"]:
                if at < length:
                    return None if where is None else (where + at) // BLOCK
                at -= length
        return None

    def _block(self, part, block):
        where = self._locate(part, block)
        return b"" if where is None else self.dev.read(where)

    def _runs(self, part, block, length):
        """[(byte offset on the disc or None, bytes)] for `length` bytes from a block."""
        m = self.maps[part] if part < len(self.maps) else {"kind": "unknown"}
        if m["kind"] == "physical" or (m["kind"] == "sparable" and not m["spared"]):
            return [(self._locate(part, block) * BLOCK, length)]
        runs = []
        left = length
        while left > 0:
            where = self._locate(part, block)
            step = min(left, BLOCK)
            offset = None if where is None else where * BLOCK
            if runs and offset is not None and runs[-1][0] is not None \
                    and runs[-1][0] + runs[-1][1] == offset and runs[-1][1] % BLOCK == 0:
                runs[-1] = (runs[-1][0], runs[-1][1] + step)
            elif runs and offset is None and runs[-1][0] is None:
                runs[-1] = (None, runs[-1][1] + step)
            else:
                runs.append((offset, step))
            block += 1
            left -= step
        return runs

    # -- files --------------------------------------------------------------------------

    def _entry(self, part, block, d=None, hops=0):
        """A file entry: {"type", "size", "extents" or "inline"}."""
        if d is None:
            d = self._block(part, block)
        ident = _tag(d)
        if ident == TAG_INDIRECT and hops < 16:  # write-once media: follow to the newest
            return self._entry(_le16(d, 44), _le32(d, 40), None, hops + 1)
        if ident == TAG_FE:
            ea_len, ad_len, base = _le32(d, 168), _le32(d, 172), 176
        elif ident == TAG_EFE:
            ea_len, ad_len, base = _le32(d, 208), _le32(d, 212), 216
        else:
            raise ValueError(f"no file entry at {part}:{block}")
        entry = {"type": d[16 + 11], "size": _le64(d, 56)}
        form = _le16(d, 16 + 18) & 7
        ads = d[base + ea_len : base + ea_len + ad_len]
        if form == 3:
            entry["inline"] = bytes(ads[: entry["size"]])
            return entry
        if form not in (0, 1, 2):
            raise ValueError("unknown allocation descriptors")
        width = (8, 16, 20)[form]
        extents = []
        left = entry["size"]
        pos = 0
        chained = 0
        while pos + width <= len(ads):
            raw = _le32(ads, pos)
            length, kind = raw & 0x3FFFFFFF, raw >> 30
            if form == 0:
                ad_part, ad_block = part, _le32(ads, pos + 4)
            elif form == 1:
                ad_block, ad_part = _le32(ads, pos + 4), _le16(ads, pos + 8)
            else:
                ad_block, ad_part = _le32(ads, pos + 12), _le16(ads, pos + 16)
            pos += width
            if length == 0:
                break
            if kind == 3:  # the descriptors go on in another block
                chained += 1
                more = self._block(ad_part, ad_block)
                if chained > 4096 or not _tag(more, TAG_ALLOC_EXTENT):
                    break
                ads, pos = more[24 : 24 + _le32(more, 20)], 0
                continue
            take = min(length, left)
            if take:
                if kind == 0:
                    extents += self._runs(ad_part, ad_block, take)
                else:  # allocated or not, never written: reads as zeros
                    extents.append((None, take))
            left -= take
        if left > 0:
            extents.append((None, left))  # the file ends in a hole
        entry["extents"] = extents
        return entry

    def _entry_data(self, entry, limit=MAX_DIR_BYTES):
        if "inline" in entry:
            return entry["inline"]
        if entry["size"] > limit:
            return b""
        out = bytearray()
        for where, length in entry["extents"]:
            out += bytes(length) if where is None else self.dev.pread(length, where)
        return bytes(out[: entry["size"]])

    def _walk(self, icb, path, depth):
        if icb in self.seen or depth > MAX_DEPTH:
            return
        self.seen.add(icb)
        try:
            entry = self._entry(*icb)
        except (ValueError, struct.error):
            return
        if entry["type"] != FILE_TYPE_DIRECTORY:
            return
        data = self._entry_data(entry)
        pos = 0
        while pos + 38 <= len(data):
            if _le16(data, pos) != TAG_FID:
                # Padding to the end of the block, or damage: go on at the next block.
                pos = (pos // BLOCK + 1) * BLOCK
                continue
            flags, name_len = data[pos + 18], data[pos + 19]
            child = (_le16(data, pos + 28), _le32(data, pos + 24))
            use_len = _le16(data, pos + 36)
            name = _cs0(data[pos + 38 + use_len : pos + 38 + use_len + name_len])
            pos += (38 + use_len + name_len + 3) & ~3
            if flags & 0x0C or not name:  # deleted, or the parent directory
                continue
            if not path and name == "Non-Allocatable Space":  # UDF 1.50's own list of bad blocks
                continue
            full = f"{path}/{name}" if path else name
            if flags & 0x02:
                self._walk(child, full, depth + 1)
                continue
            try:
                f = self._entry(*child)
            except (ValueError, struct.error):
                continue
            if f["type"] == FILE_TYPE_DIRECTORY:
                continue
            if len(self.files) >= MAX_FILES:
                return
            self.files.append(
                DiscFile(full, f["size"], f.get("extents", ()), f.get("inline"))
            )
