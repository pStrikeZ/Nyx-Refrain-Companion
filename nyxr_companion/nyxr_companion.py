#!/usr/bin/env python3
"""NyxR Companion: plays an audio CD from a local drive through nyxr to an AirPlay 2
receiver, and works as a Bluetooth receiver (A2DP sink) through the host's BlueZ.

    CD drive ──libcdio──▶ ring buffer ──PCM──▶ nyxr --source stdin ──AirPlay 2──▶ speaker
    phone ──A2DP──▶ BlueZ + PulseAudio ──parec──▶ nyxr --source stdin --input-live ──▶ speaker

Control surfaces:
- MPRIS on a private D-Bus session bus. nyxr --now-playing reads track info from it and
  forwards the receiver's play / pause / next / previous back to it.
- HTTP + WebSocket API (see API.md) for the Home Assistant integration: stream switch, play
  mode, eject and status.

Threads: the asyncio loop runs the HTTP server, MPRIS and the drive monitor; one reader
thread runs cd-paranoia for a track at a time and fills the buffer; one writer thread feeds
nyxr (which pulls at the receiver's pace), writing silence while paused or starved.
"""

import asyncio
import collections
import ctypes
import fcntl
import json
import logging
import os
import random
import re
import secrets
import shlex
import signal
import subprocess
import threading
import time

import aiohttp
from aiohttp import WSCloseCode, WSMsgType, web
from dbus_next import Message, MessageType, PropertyAccess, Variant
from dbus_next.aio import MessageBus
from dbus_next.auth import AuthExternal
from dbus_next.errors import DBusError
from dbus_next.service import ServiceInterface, dbus_property, method, signal as dbus_signal

log = logging.getLogger("nyxr-companion")

SECTOR_BYTES = 2352  # one CD-DA sector: 588 stereo s16le frames
SECTORS_PER_SECOND = 75
BYTES_PER_SECOND = SECTOR_BYTES * SECTORS_PER_SECOND
FRAME_BYTES = 4
SECTOR_FRAMES = SECTOR_BYTES // FRAME_BYTES
WRITE_CHUNK = 4096  # ~23 ms per write to nyxr
PIPE_SIZE = 16384  # small pipe so a pause takes effect quickly
MODES = ("repeat_all", "repeat_one", "shuffle")

# linux/cdrom.h
CDROMEJECT = 0x5309
CDROM_SELECT_SPEED = 0x5322
SG_IO = 0x2285  # scsi/sg.h
CDROM_DRIVE_STATUS = 0x5326
CDROM_LOCKDOOR = 0x5329
CDSL_CURRENT = 0x7FFFFFFF
DRIVE_STATUS = {1: "no_disc", 2: "tray_open", 3: "not_ready", 4: "ok"}


def _load_options():
    """Home Assistant add-on options (/data/options.json), as environment defaults."""
    try:
        with open(os.environ.get("NYXR_COMPANION_OPTIONS", "/data/options.json"), encoding="utf-8") as f:
            options = json.load(f)
    except (OSError, ValueError):
        return {}
    return options if isinstance(options, dict) else {}


OPTIONS = _load_options()
OPTION_VARS = {  # add-on option: environment variable it stands for
    "cd_device": "NYXR_COMPANION_DEVICE",
    "bluetooth_adapter": "NYXR_COMPANION_BT_ADAPTER",
    "api_listen": "NYXR_COMPANION_LISTEN",
    "log_level": "NYXR_COMPANION_LOG",
}


def env(name, default):
    if name in os.environ:
        return os.environ[name]
    for option, var in OPTION_VARS.items():
        if var == name and OPTIONS.get(option) not in (None, ""):
            return str(OPTIONS[option])
    return default


DEVICE = env("NYXR_COMPANION_DEVICE", "/dev/sr0")
# The HTTP API has no authentication: by default only Home Assistant on the same host reaches it.
LISTEN = env("NYXR_COMPANION_LISTEN", "127.0.0.1:8765")
STATE_DIR = env("NYXR_COMPANION_STATE", "/data")
CACHE_DIR = env("NYXR_COMPANION_CACHE", os.path.join(STATE_DIR, "cache"))
RUN_DIR = env("NYXR_COMPANION_RUN", "/run/nyxr-companion")
# Testing only: "223.4,183.2,..." fakes a disc with these track lengths and generates tones
# instead of reading the drive.
FAKE_DISC = env("NYXR_COMPANION_FAKE_DISC", "")


class BadRequest(Exception):
    pass


class Conflict(Exception):
    pass


# ---------------------------------------------------------------------------------------
# Settings: adjustable from Home Assistant, kept in STATE_DIR/settings.json. The environment
# only gives the defaults.

READ_MODES = ("raw", "raw_aligned", "paranoia_overlap", "paranoia_full")
LATENCY_PROFILES = ("low", "balanced", "stable")


def _choice(options):
    def check(value):
        if value not in options:
            raise BadRequest(f"must be one of {', '.join(options)}")
        return value

    return check


def _number(low, high, kind):
    def check(value):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise BadRequest(f"must be a number from {low} to {high}")
        if not low <= value <= high:
            raise BadRequest(f"must be from {low} to {high}")
        return kind(value)

    return check


def _bool(value):
    if not isinstance(value, bool):
        raise BadRequest("must be true or false")
    return value


def _name(value):
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > 64:
        raise BadRequest("must be 1-64 characters")
    return value.strip()


def _target(value):
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip() or any(c.isspace() for c in value.strip()):
        raise BadRequest("must be an IP or IP:PORT")
    return value.strip()


def _env_default(check, name, default):
    try:
        return check(type(default)(env(name, default)))
    except (BadRequest, ValueError):
        return default


SETTINGS_SPEC = {
    # name: (check, default)
    "read_mode": (
        _choice(READ_MODES),
        "raw",
    ),
    "read_speed": (_number(1, 24, int), None),
    "buffer_seconds": (_number(2, 60, float), None),
    "latency_profile": (_choice(LATENCY_PROFILES), "balanced"),
    # Set from Home Assistant; nothing streams until it is.
    "target": (_target, env("NYXR_COMPANION_TARGET", "") or None),
    "bluetooth_name": (_name, "NyxR Companion"),
    "bluetooth_auto_switch": (_bool, True),
    "bluetooth_buffer_ms": (_number(20, 2000, int), 150),
}
for _name, _var, _default in (
    ("read_speed", "NYXR_COMPANION_READ_SPEED", 4),
    ("buffer_seconds", "NYXR_COMPANION_BUFFER_SECONDS", 10.0),
):
    _check = SETTINGS_SPEC[_name][0]
    SETTINGS_SPEC[_name] = (_check, _env_default(_check, _var, _default))


class Settings:
    def __init__(self):
        self.path = os.path.join(STATE_DIR, "settings.json")
        self.values = {name: default for name, (_, default) in SETTINGS_SPEC.items()}
        try:
            with open(self.path, encoding="utf-8") as f:
                saved = json.load(f)
        except (OSError, ValueError):
            saved = {}
        for name, value in saved.items() if isinstance(saved, dict) else ():
            if name in SETTINGS_SPEC:
                try:
                    self.values[name] = SETTINGS_SPEC[name][0](value)
                except BadRequest:
                    pass  # keep the default

    def __getitem__(self, name):
        return self.values[name]

    def update(self, changes):
        """Validates and saves `changes`; returns the names whose value changed."""
        if not changes:
            raise BadRequest("send an object of settings to change")
        new = dict(self.values)
        for name, value in changes.items():
            if name not in SETTINGS_SPEC:
                raise BadRequest(f"unknown setting {name!r}")
            try:
                new[name] = SETTINGS_SPEC[name][0](value)
            except BadRequest as e:
                raise BadRequest(f"{name} {e}") from None
        changed = {name for name in new if new[name] != self.values[name]}
        self.values = new
        try:
            os.makedirs(STATE_DIR, exist_ok=True)
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(new, f, ensure_ascii=False, indent=1)
            os.replace(tmp, self.path)
        except OSError as e:
            log.warning("saving settings failed: %s", e)
        return changed


def nyxr_command(rate=44100, channels=2, live_ms=None):
    """nyxr reading s16le PCM on stdin. `live_ms`: the PCM is written live on another
    device's clock (Bluetooth); nyxr keeps that much buffered and follows the clock."""
    override = env("NYXR_COMMAND", "")  # testing: any command that consumes PCM on stdin
    if override:
        return shlex.split(override)
    if not SETTINGS["target"]:
        raise Conflict("set the AirPlay receiver (target) first")
    live = ["--input-live", "--input-buffer-ms", str(live_ms)] if live_ms else []
    return [
        "nyxr", "stream",
        "--target", SETTINGS["target"],
        "--source", "stdin",
        "--input-rate", str(rate),
        "--input-format", "s16le",
        "--input-channels", str(channels),
        *live,
        "--audible",
        "--keep-volume",  # never touch the speaker's volume
        "--latency-profile", SETTINGS["latency_profile"],
        "--now-playing",
    ] + shlex.split(env("NYXR_EXTRA_ARGS", ""))


def api_token():
    """The token API clients must send (Authorization: Bearer ...), made on first start and
    kept in STATE_DIR; Home Assistant gets it through Supervisor discovery."""
    if env("NYXR_COMPANION_TOKEN", ""):
        return env("NYXR_COMPANION_TOKEN", "")
    path = os.path.join(STATE_DIR, "api_token")
    try:
        with open(path, encoding="utf-8") as f:
            token = f.read().strip()
        if token:
            return token
    except OSError:
        pass
    token = secrets.token_urlsafe(32)
    os.makedirs(STATE_DIR, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(token + "\n")
    return token


DISCOVERED = re.compile(r"^\d+\. '(?P<name>.*)' at (?P<address>\S+) \(model=(?P<model>.*?), et=")


def discover_receivers():
    """AirPlay receivers on the network, from `nyxr discover`: [{"name", "address", "model"}].
    A receiver is listed once per service (AirPlay, RAOP); keep one per address."""
    out = subprocess.run(
        ["nyxr", "discover", "--timeout", "3s"], capture_output=True, text=True, timeout=20
    ).stdout
    found = {}
    for line in out.splitlines():
        m = DISCOVERED.match(line.strip())
        if not m:
            continue
        entry = found.setdefault(m["address"], {"name": m["name"], "address": m["address"], "model": None})
        if m["model"] not in ("", "Unknown"):
            entry["model"] = m["model"]
    return sorted(found.values(), key=lambda r: r["name"].lower())


# ---------------------------------------------------------------------------------------
# Drive access

def drive_status():
    if FAKE_DISC:
        return "ok"
    try:
        fd = os.open(DEVICE, os.O_RDONLY | os.O_NONBLOCK)
    except OSError:
        return "missing"
    try:
        return DRIVE_STATUS.get(fcntl.ioctl(fd, CDROM_DRIVE_STATUS, CDSL_CURRENT), "not_ready")
    except OSError:
        return "not_ready"
    finally:
        os.close(fd)


class SgIoHdr(ctypes.Structure):
    _fields_ = [
        ("interface_id", ctypes.c_int),
        ("dxfer_direction", ctypes.c_int),
        ("cmd_len", ctypes.c_ubyte),
        ("mx_sb_len", ctypes.c_ubyte),
        ("iovec_count", ctypes.c_ushort),
        ("dxfer_len", ctypes.c_uint),
        ("dxferp", ctypes.c_void_p),
        ("cmdp", ctypes.c_void_p),
        ("sbp", ctypes.c_void_p),
        ("timeout", ctypes.c_uint),
        ("flags", ctypes.c_uint),
        ("pack_id", ctypes.c_int),
        ("usr_ptr", ctypes.c_void_p),
        ("status", ctypes.c_ubyte),
        ("masked_status", ctypes.c_ubyte),
        ("msg_status", ctypes.c_ubyte),
        ("sb_len_wr", ctypes.c_ubyte),
        ("host_status", ctypes.c_ushort),
        ("driver_status", ctypes.c_ushort),
        ("resid", ctypes.c_int),
        ("duration", ctypes.c_uint),
        ("info", ctypes.c_uint),
    ]


def _set_cd_speed(fd, speed):
    """SET CD SPEED (MMC 0xBB) through SG_IO. Inside a VM with the drive passed through as a
    raw SCSI device the guest's sr driver refuses CDROM_SELECT_SPEED, the drive does not."""
    kbps = min(int(speed * 176.4 + 0.5), 0xFFFF)
    cdb = (ctypes.c_ubyte * 12)(0xBB, 0, kbps >> 8, kbps & 0xFF, 0xFF, 0xFF, 0, 0, 0, 0, 0, 0)
    sense = (ctypes.c_ubyte * 32)()
    hdr = SgIoHdr(
        interface_id=ord("S"), dxfer_direction=-1, cmd_len=12, mx_sb_len=32,  # SG_DXFER_NONE
        cmdp=ctypes.addressof(cdb), sbp=ctypes.addressof(sense), timeout=5000,
    )
    fcntl.ioctl(fd, SG_IO, hdr)
    if hdr.status or hdr.host_status or hdr.driver_status:
        raise OSError(f"SET CD SPEED failed (status {hdr.status:#x})")


def limit_speed():
    """Caps the drive's spin speed. Some slim drives drop off the SATA link when they spin
    up to full speed (24x), and drives forget the cap when they reset or change discs, so
    this is repeated before anything that may spin it up."""
    if FAKE_DISC:
        return
    try:
        fd = os.open(DEVICE, os.O_RDONLY | os.O_NONBLOCK)
    except OSError:
        return
    try:
        try:
            fcntl.ioctl(fd, CDROM_SELECT_SPEED, SETTINGS["read_speed"])
        except OSError:
            _set_cd_speed(fd, SETTINGS["read_speed"])
    except OSError:
        pass  # no disc, or not ready yet
    finally:
        os.close(fd)


def eject_disc():
    if FAKE_DISC:
        return
    fd = os.open(DEVICE, os.O_RDONLY | os.O_NONBLOCK)
    try:
        fcntl.ioctl(fd, CDROM_LOCKDOOR, 0)
        fcntl.ioctl(fd, CDROMEJECT)
    finally:
        os.close(fd)


class NotAudio(Exception):
    """The disc has no audio tracks: a data CD, a DVD, or one whose TOC cannot be read."""


def _libcdio():
    lib = ctypes.CDLL("libcdio.so.19")
    ctypes.c_int.in_dll(lib, "cdio_loglevel_default").value = 4  # CDIO_LOG_ERROR: no warnings
    lib.cdio_open.restype = ctypes.c_void_p
    lib.cdio_open.argtypes = [ctypes.c_char_p, ctypes.c_int]
    lib.cdio_destroy.argtypes = [ctypes.c_void_p]
    lib.cdio_get_first_track_num.restype = ctypes.c_uint8
    lib.cdio_get_first_track_num.argtypes = [ctypes.c_void_p]
    lib.cdio_get_num_tracks.restype = ctypes.c_uint8
    lib.cdio_get_num_tracks.argtypes = [ctypes.c_void_p]
    lib.cdio_get_track_format.restype = ctypes.c_int
    lib.cdio_get_track_format.argtypes = [ctypes.c_void_p, ctypes.c_uint8]
    lib.cdio_get_cdtext.restype = ctypes.c_void_p
    lib.cdio_get_cdtext.argtypes = [ctypes.c_void_p]
    lib.cdtext_get_const.restype = ctypes.c_char_p
    lib.cdtext_get_const.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_uint8]
    return lib


TRACK_FORMAT_AUDIO = 0
CDIO_INVALID_TRACK = 0xFF


def audio_tracks():
    """CD track numbers of the disc's audio tracks, going by the TOC."""
    lib = _libcdio()
    cdio = lib.cdio_open(DEVICE.encode(), 0)  # DRIVER_UNKNOWN: autodetect
    if not cdio:
        raise OSError(f"cannot open {DEVICE}")
    try:
        first = lib.cdio_get_first_track_num(cdio)
        count = lib.cdio_get_num_tracks(cdio)
        if CDIO_INVALID_TRACK in (first, count):
            return []
        return [
            n
            for n in range(first, first + count)
            if lib.cdio_get_track_format(cdio, n) == TRACK_FORMAT_AUDIO
        ]
    finally:
        lib.cdio_destroy(cdio)


def read_toc():
    """Returns (disc id, [track length in sectors], [CD track number]) for the audio tracks
    only: data tracks (mixed-mode CDs, CD-Extra) are left out, never played. Raises NotAudio
    for a disc without audio tracks."""
    if FAKE_DISC:
        lengths = [round(float(s) * SECTORS_PER_SECOND) for s in FAKE_DISC.split(",")]
        return "fake-" + "-".join(map(str, lengths)), lengths, list(range(1, len(lengths) + 1))
    audio = audio_tracks()
    if not audio:
        raise NotAudio()
    import libdiscid

    disc = libdiscid.read(DEVICE)
    numbers = range(disc.first_track, disc.last_track + 1)
    tracks = [(n, length) for n, length in zip(numbers, disc.track_lengths) if n in audio]
    if not tracks:
        raise NotAudio()
    return disc.id, [length for _, length in tracks], [n for n, _ in tracks]


CDTEXT_TITLE, CDTEXT_PERFORMER = 0, 1  # cdtext_field_t


def read_cdtext(numbers, source=None):
    """Album / track names from the disc's CD-Text, in the same shape as lookup_metadata(),
    or None. Read through libcdio directly: cd-info takes some burned CDs for DVD-ROMs and
    then skips CD-Text. libcdio converts the text (including MS-JIS) to UTF-8."""
    if FAKE_DISC and source is None:
        return None
    lib = _libcdio()
    cdio = lib.cdio_open((source or DEVICE).encode(), 0)  # DRIVER_UNKNOWN: autodetect
    if not cdio:
        return None
    try:
        text = lib.cdio_get_cdtext(cdio)  # owned by cdio
        if not text:
            return None

        def get(field, track):
            value = lib.cdtext_get_const(text, field, track)
            return value.decode("utf-8", "replace").strip() or None if value else None

        meta = {
            "album": get(CDTEXT_TITLE, 0),
            "artist": get(CDTEXT_PERFORMER, 0),
            "cover_url": None,
            "tracks": [
                {"title": get(CDTEXT_TITLE, n), "artist": get(CDTEXT_PERFORMER, n)}
                for n in numbers
            ],
        }
    finally:
        lib.cdio_destroy(cdio)
    if not meta["album"] and not any(t["title"] for t in meta["tracks"]):
        return None
    return meta


def lookup_metadata(disc_id, count):
    """MusicBrainz lookup, cached per disc id. Returns a dict or None."""
    if FAKE_DISC:
        return None
    path = os.path.join(CACHE_DIR, disc_id + ".json")
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        pass
    import musicbrainzngs as mb

    mb.set_useragent("nyxr-companion", "0.1", "https://github.com/pStrikeZ/Nyx-Refrain-Companion")
    try:
        result = mb.get_releases_by_discid(
            disc_id, includes=["artists", "recordings", "artist-credits"]
        )
    except mb.ResponseError:
        return None  # unknown disc; not cached so a later submission can be picked up
    releases = result.get("disc", {}).get("release-list", [])
    for release in releases:
        for medium in release.get("medium-list", []):
            if not any(d.get("id") == disc_id for d in medium.get("disc-list", [])):
                continue
            tracks = medium.get("track-list", [])
            if len(tracks) != count:
                continue
            meta = {
                "album": release.get("title"),
                "artist": release.get("artist-credit-phrase"),
                "cover_url": None,
                "tracks": [
                    {
                        "title": t.get("recording", {}).get("title") or t.get("title"),
                        "artist": t.get("artist-credit-phrase")
                        or t.get("recording", {}).get("artist-credit-phrase"),
                    }
                    for t in tracks
                ],
            }
            if release.get("cover-art-archive", {}).get("front") == "true":
                meta["cover_url"] = (
                    f"https://coverartarchive.org/release/{release['id']}/front-500"
                )
            try:
                os.makedirs(CACHE_DIR, exist_ok=True)
                with open(path, "w", encoding="utf-8") as f:
                    json.dump(meta, f, ensure_ascii=False)
            except OSError:
                pass
            return meta
    return None


class Cdda:
    """The drive opened through libcdio's cdda layer, for raw sector reads. Opening checks
    the drive and reads the TOC (about a second), so it stays open for the whole disc and a
    jump only costs a seek. Used by the reader thread only."""

    def __init__(self):
        self.lib = None
        self.drive = None
        self.epoch = None

    def get(self, epoch):
        if self.drive is not None and self.epoch == epoch:
            return self.drive
        self.close()
        if self.lib is None:
            lib = ctypes.CDLL("libcdio_cdda.so.2")
            lib.cdio_cddap_identify.restype = ctypes.c_void_p
            lib.cdio_cddap_identify.argtypes = [ctypes.c_char_p, ctypes.c_int, ctypes.c_void_p]
            lib.cdio_cddap_open.argtypes = [ctypes.c_void_p]
            lib.cdio_cddap_close.argtypes = [ctypes.c_void_p]
            lib.cdio_cddap_speed_set.argtypes = [ctypes.c_void_p, ctypes.c_int]
            lib.cdio_cddap_track_firstsector.restype = ctypes.c_int32
            lib.cdio_cddap_track_firstsector.argtypes = [ctypes.c_void_p, ctypes.c_uint8]
            lib.cdio_cddap_read.restype = ctypes.c_long
            lib.cdio_cddap_read.argtypes = [
                ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int32, ctypes.c_long
            ]
            self.lib = lib
        drive = self.lib.cdio_cddap_identify(DEVICE.encode(), 0, None)  # 0: no messages
        if not drive:
            raise OSError(f"cannot open {DEVICE} for audio")
        if self.lib.cdio_cddap_open(drive) != 0:
            self.lib.cdio_cddap_close(drive)
            raise OSError(f"cannot read the TOC of {DEVICE}")
        self.lib.cdio_cddap_speed_set(drive, SETTINGS["read_speed"])
        self.drive, self.epoch = drive, epoch
        return drive

    def close(self):
        if self.drive is not None:
            self.lib.cdio_cddap_close(self.drive)
            self.drive = None


CDDA = Cdda()
READ_RETRIES = 3
RAW_SECTORS = 20  # new sectors per read; with the overlap, under the drive's limit of 25
OVERLAP_SECTORS = 2  # read again before each block, to line it up with the last one
MATCH_BYTES = 1024  # end of the previous block that is looked for in the overlap
MAX_BAD_SECTORS = 75 * 3  # in a row (3 s) before giving up on the track
MAX_FAILED_TRACKS = 3


class TrackReader:
    """Reads one track (from a sector offset to its end) as raw s16le PCM.

    By default the sectors are read directly, like a CD player does: the drive's own error
    correction is enough for a healthy disc, and a jump starts in a fraction of a second.
    "raw_aligned" also re-reads a little of the previous block and lines each block up on
    it (cdda2wav-style overlap checking), keeping the position in audio samples rather than
    addresses, for drives whose reads are not sample-accurate.
    cd-paranoia ("overlap" / "full") verifies every read, but first reads its whole cache
    model (1200 sectors, 16 s of audio) after every jump, which takes 3-7 s."""

    def __init__(self, track, offset, length, epoch=None):
        self.proc = None
        self.fake = None
        self.raw = None
        self.error = None
        if FAKE_DISC:
            # A quiet tone per track: 220 Hz * track number, so tracks are easy to tell apart.
            import math

            frames = (length - offset) * SECTOR_BYTES // FRAME_BYTES
            start = offset * SECTOR_BYTES // FRAME_BYTES
            freq = 220.0 * track
            self.fake = (
                int(3000 * math.sin(2 * math.pi * freq * (start + i) / 44100))
                for i in range(frames)
            )
            return
        mode = SETTINGS["read_mode"]
        if mode in ("raw", "raw_aligned"):
            self.aligned = mode == "raw_aligned"
            try:
                drive = CDDA.get(epoch)
            except OSError as e:
                self.error = str(e)
                return
            start = CDDA.lib.cdio_cddap_track_firstsector(drive, track)
            if start < 0:
                self.error = f"no start sector for track {track}"
                return
            self.drive = drive
            self.pos = (start + offset) * SECTOR_FRAMES  # next frame to hand out
            self.end = (start + length) * SECTOR_FRAMES
            self.drift = 0  # frames the drive's data lies ahead of the address asked for
            self.raw = True
            self.bad = 0
            self.tail = b""  # last MATCH_BYTES handed out
            return
        cmd = ["cd-paranoia", "-q", "-r", "-d", DEVICE, "-S", str(SETTINGS["read_speed"])]
        cmd += ["-Y"] if mode == "paranoia_overlap" else []
        cmd += [f"{track}[.{offset}]-{track}", "-"]
        self.proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=0
        )

    def read(self, size):
        if self.fake is not None:
            out = bytearray()
            for sample in self.fake:
                v = sample.to_bytes(2, "little", signed=True)
                out += v + v
                if len(out) >= size:
                    break
            return bytes(out)
        if self.proc is None:
            return self._read_raw() if self.raw else b""
        return self.proc.stdout.read(size)

    def _read_raw(self):
        if self.pos >= self.end:
            return b""
        back = OVERLAP_SECTORS if self.aligned and self.tail else 0
        lsn = (self.pos - self.drift) // SECTOR_FRAMES - back
        last = self.end // SECTOR_FRAMES
        n = min(RAW_SECTORS + back, last - lsn)
        if n <= back:
            return b""  # within the drift of the end
        buf = ctypes.create_string_buffer(n * SECTOR_BYTES)
        for _ in range(READ_RETRIES):
            got = CDDA.lib.cdio_cddap_read(self.drive, buf, lsn, n)
            if got > back:  # the drive may return fewer sectors than asked
                break
        else:
            # Play silence over an unreadable stretch rather than stop, unless it goes on.
            self.tail = b""
            self.bad += n - back
            log.warning("unreadable sectors %d-%d", lsn + back, lsn + n - 1)
            if self.bad >= MAX_BAD_SECTORS:
                self.error = f"cannot read the disc at sector {lsn + back}"
                CDDA.close()
                return b""
            frames = min((n - back) * SECTOR_FRAMES, self.end - self.pos)
            self.pos += frames
            return bytes(frames * FRAME_BYTES)
        self.bad = 0
        data = buf.raw[: got * SECTOR_BYTES]
        skip = self._align(data, back * SECTOR_BYTES)
        # data[skip] is frame self.pos; that gives where this read really started.
        self.drift = self.pos - skip // FRAME_BYTES - lsn * SECTOR_FRAMES
        out = data[skip : skip + (self.end - self.pos) * FRAME_BYTES]
        self.pos += len(out) // FRAME_BYTES
        self.tail = (self.tail + out)[-MATCH_BYTES:]
        return out

    def _align(self, data, overlap):
        """Where in `data` the new audio starts: just after the end of the previous block,
        searched near where it should be. Falls back to the nominal position."""
        if not overlap:
            return 0
        expected = overlap - len(self.tail)
        best = None
        pos = data.find(self.tail, 0, overlap * 2)
        while pos >= 0:
            if (pos - expected) % FRAME_BYTES == 0 and (
                best is None or abs(pos - expected) < abs(best - expected)
            ):
                best = pos
            pos = data.find(self.tail, pos + 1, overlap * 2)
        if best is None:
            log.debug("no overlap match; jitter not corrected")
            return overlap
        return best + len(self.tail)

    def finish(self):
        """Returns an error string if the read failed, else None."""
        if self.proc is None:
            return self.error
        code = self.proc.wait()
        err = self.proc.stderr.read().decode(errors="replace").strip()
        if code != 0:
            return f"cd-paranoia exited with {code}: {err[-300:]}"
        return None

    def kill(self):
        if self.proc is not None and self.proc.poll() is None:
            self.proc.kill()
            self.proc.wait()


# ---------------------------------------------------------------------------------------
# Player

class Player:
    def __init__(self, loop):
        self.loop = loop
        self.lock = threading.Condition()
        self.listeners = []  # callables run on the asyncio loop after each change
        self.receivers = []  # AirPlay receivers seen on the network (discover_receivers)

        self.drive = "missing"
        self.disc = None  # {"id", "album", "artist", "cover_url", "tracks": [...]}
        self.lengths = []  # sectors per track
        self.cd_tracks = []  # CD track number of each (audio) track
        self.mode = "repeat_all"
        self.streaming = False
        self.playback = "stopped"
        self.track = None
        self.played = 0  # bytes of the current track handed to nyxr
        self.error = None

        self.order = []  # shuffle order (track numbers) and our place in it
        self.order_pos = 0

        # Each read of a track gets a sequence number, so the writer can tell a repeated
        # track (single-track repeat) from the one before it.
        self.buffer = collections.deque()  # (seq, track, bytes)
        self.buffered = 0
        self.seq = 0  # last sequence number handed out
        self.failed_tracks = 0  # read failures in a row
        self.disc_epoch = 0  # bumped when the disc goes away, so the reader reopens the drive
        self.playing_seq = 0  # sequence number of what the writer is playing
        self.gen = 0  # bumped to cancel the reader's current job
        self.job = None  # (seq, track, offset in sectors) for the reader to start
        self.reading = None  # sequence number the reader is working on

        self.nyxr = None
        self.stopping_nyxr = False

        self.source = "cd"  # or "bluetooth"; see Sources
        self.bt = None  # Bluetooth, set up by main()
        self.sources = None

        threading.Thread(target=self._reader_loop, daemon=True).start()
        threading.Thread(target=self._writer_loop, daemon=True).start()

    # -- notifications ------------------------------------------------------------------

    def changed(self):
        self.loop.call_soon_threadsafe(self._notify)

    def _notify(self):
        for fn in list(self.listeners):
            fn()

    # -- state --------------------------------------------------------------------------

    def position(self):
        return self.played / BYTES_PER_SECOND

    def snapshot(self):
        bt = self.bt.snapshot() if self.bt else {"available": False}
        with self.lock:
            on_bt = self.source == "bluetooth"
            return {
                "source": self.source,
                "drive": self.drive,
                "disc": self.disc,
                # The selected source's stream and playback.
                "streaming": bt["streaming"] if on_bt else self.streaming,
                "playback": bt["playback"] if on_bt else self.playback,
                "track": self.track,
                "position": round(self.position(), 1),
                "mode": self.mode,
                "error": bt["error"] if on_bt else self.error,
                "bluetooth": bt,
                "receivers": self.receivers,
                "settings": dict(SETTINGS.values),
            }

    def track_info(self):
        with self.lock:
            if self.disc is None or self.track is None:
                return None
            info = dict(self.disc["tracks"][self.track - 1])
            info["album"] = self.disc["album"]
            info["album_artist"] = self.disc["artist"]
            info["cover_url"] = self.disc["cover_url"]
            return info

    # -- planning (call with the lock held) ----------------------------------------------

    def _count(self):
        return len(self.lengths)

    def _reshuffle(self, first=None):
        rest = [t for t in range(1, self._count() + 1) if t != first]
        random.shuffle(rest)
        self.order = ([first] if first else []) + rest
        self.order_pos = 0

    def _order_index(self, track):
        """Where `track` sits in the shuffle order, looking forward from the audible place.

        The order only grows (a new permutation is appended when it runs out), so a track
        that the reader prefetched is found ahead of the one being heard.
        """
        for i in range(self.order_pos, len(self.order)):
            if self.order[i] == track:
                return i
        return self.order.index(track) if track in self.order else None

    def _shuffle_next(self, track):
        """Position in the shuffle order of the track after `track`."""
        i = self._order_index(track)
        if i is None:
            self._reshuffle(track)
            i = 0
        if i + 1 >= len(self.order):
            more = list(range(1, self._count() + 1))
            random.shuffle(more)
            if len(more) > 1 and more[0] == self.order[-1]:
                more.append(more.pop(0))
            self.order += more
        return i + 1

    def _next_track(self, track, manual):
        """Track after `track` (None only without a disc). The disc always wraps around."""
        n = self._count()
        if n == 0:
            return None
        if self.mode == "repeat_one" and not manual:
            return track
        if self.mode == "shuffle":
            return self.order[self._shuffle_next(track)]
        return track + 1 if track < n else 1

    def _advance_order(self, track):
        if self.mode == "shuffle":
            i = self._order_index(track)
            if i is not None:
                self.order_pos = i

    def _jump(self, track, offset=0, order_pos=None):
        self.gen += 1
        self.buffer.clear()
        self.buffered = 0
        self.track = track
        self.played = offset * SECTOR_BYTES
        self.playing_seq = self._new_job(track, offset)
        if order_pos is not None:
            self.order_pos = order_pos
        else:
            self._advance_order(track)
        self.lock.notify_all()

    def _new_job(self, track, offset=0):
        self.seq += 1
        self.job = (self.seq, track, offset)
        return self.seq

    def _halt_reader(self):
        self.gen += 1
        self.job = None
        self.buffer.clear()
        self.buffered = 0
        self.lock.notify_all()

    # -- commands -----------------------------------------------------------------------

    def play(self, track=None):
        with self.lock:
            if self.drive == "not_audio":
                raise Conflict("not an audio CD")
            if self.disc is None:
                raise Conflict("no disc")
            if track is not None and not 1 <= track <= self._count():
                raise BadRequest(f"track must be 1-{self._count()}")
        self.start_stream()
        with self.lock:
            if track is not None:
                self._jump(track)
            elif self.track is None:
                self._jump(self.order[0] if self.mode == "shuffle" and self.order else 1)
            elif self.job is None and self.reading is None and not self.buffer:
                self._jump(self.track, self.played // SECTOR_BYTES)
            self.playback = "playing"
            self.lock.notify_all()
        self.changed()

    def pause(self):
        with self.lock:
            if self.playback == "playing":
                self.playback = "paused"
        self.changed()

    def play_pause(self):
        if self.playback == "playing":
            self.pause()
        else:
            self.play()

    def next(self):
        with self.lock:
            if self.track is None or self.disc is None:
                return
            if self.mode == "shuffle":
                pos = self._shuffle_next(self.track)
                self._jump(self.order[pos], order_pos=pos)
            else:
                self._jump(self._next_track(self.track, manual=True))
        self.changed()

    def previous(self):
        with self.lock:
            if self.track is None or self.disc is None:
                return
            if self.position() > 3:
                self._jump(self.track, order_pos=self.order_pos)
            elif self.mode == "shuffle":
                pos = max(0, self.order_pos - 1)
                self._jump(self.order[pos], order_pos=pos)
            else:
                self._jump(self.track - 1 if self.track > 1 else self._count())
        self.changed()

    def seek(self, seconds):
        with self.lock:
            if self.track is None:
                return
            length = self.lengths[self.track - 1]
            offset = max(0, min(int(seconds * SECTORS_PER_SECOND), length - 1))
            self._jump(self.track, offset)
        self.changed()

    def set_mode(self, mode):
        if mode not in MODES:
            raise BadRequest(f"mode must be one of {', '.join(MODES)}")
        with self.lock:
            if mode == self.mode:
                return
            self.mode = mode
            if mode == "shuffle":
                self._reshuffle(self.track)
            # The reader may already have queued the track that the old mode chose next:
            # drop it and let the new mode decide.
            # Likewise when the reader has finished the current track and stopped.
            if self.track is not None and (
                self.reading not in (None, self.playing_seq)
                or (self.job is not None and self.job[0] != self.playing_seq)
                or any(q != self.playing_seq for q, _, _ in self.buffer)
                or (self.reading is None and self.job is None and self.buffer)
            ):
                kept = [c for c in self.buffer if c[0] == self.playing_seq]
                self.gen += 1
                self.buffer = collections.deque(kept)
                self.buffered = sum(len(c[2]) for c in kept)
                self.job = None
                nxt = self._next_track(self.track, manual=False)
                if nxt is not None:
                    self._new_job(nxt)
                self.lock.notify_all()
        self.changed()

    def stop(self):
        # Keeps the track and the position in it: playing again resumes there.
        with self.lock:
            self.playback = "stopped"
            self._halt_reader()
        self.stop_stream()
        self.changed()

    def eject(self):
        self.stop_stream()
        with self.lock:
            self._halt_reader()
            self.playback = "stopped"
            # The kernel refuses to eject (EBUSY) while anyone else has the drive open, and
            # the reader keeps it open for the whole disc: let it finish, then close it.
            deadline = time.monotonic() + 5
            while self.reading is not None and time.monotonic() < deadline:
                self.lock.wait(0.1)
            CDDA.close()
        try:
            eject_disc()
        except OSError as e:
            with self.lock:
                self.error = f"eject failed: {e}"
            self.changed()
            raise Conflict(self.error)
        self.disc_gone()

    # -- disc lifecycle ------------------------------------------------------------------

    def update_settings(self, changes):
        changed = SETTINGS.update(changes)
        if "read_speed" in changed:
            limit_speed()
        if "bluetooth_name" in changed and self.bt:
            self.bt._sync(self.bt.apply_name())
        with self.lock:
            if "read_mode" in changed and self.track is not None and (
                self.job is not None or self.reading is not None or self.buffer
            ):
                # Read again from where we are, the new way.
                self._jump(self.track, int(self.position() * SECTORS_PER_SECOND), self.order_pos)
            self.lock.notify_all()  # buffer_seconds may have grown
        self.changed()

    def set_drive(self, status):
        with self.lock:
            if status == self.drive:
                return
            self.drive = status
        if status != "ok":
            self.disc_gone()
        self.changed()

    def disc_gone(self):
        if self.streaming:
            self.stop_stream()
        with self.lock:
            self._halt_reader()
            self.disc_epoch += 1
            self.disc = None
            self.lengths = []
            self.cd_tracks = []
            self.track = None
            self.played = 0
            self.playback = "stopped"
            self.order = []
        self.changed()

    def disc_loaded(self, disc_id, lengths, cd_tracks, meta):
        tracks = []
        for i, sectors in enumerate(lengths):
            m = (meta or {}).get("tracks", [])
            t = m[i] if i < len(m) else {}
            tracks.append(
                {
                    "number": i + 1,
                    "title": t.get("title") or f"Track{i + 1:02d}",
                    "artist": t.get("artist") or (meta or {}).get("artist"),
                    "length": round(sectors / SECTORS_PER_SECOND, 1),
                }
            )
        with self.lock:
            same = self.disc is not None and self.disc["id"] == disc_id
            self.disc = {
                "id": disc_id,
                "album": (meta or {}).get("album"),
                "artist": (meta or {}).get("artist"),
                "cover_url": (meta or {}).get("cover_url"),
                "tracks": tracks,
            }
            if not same:
                self.lengths = lengths
                self.cd_tracks = cd_tracks
                self.track = None
                self.played = 0
                if self.mode == "shuffle":
                    self._reshuffle()
        self.changed()

    # -- nyxr ---------------------------------------------------------------------------

    def start_stream(self):
        with self.lock:
            if self.streaming:
                return
            self.streaming = True
            self.error = None
        cmd = nyxr_command()
        log.info("starting nyxr: %s", shlex.join(cmd))
        # Unbuffered: stop_stream can close stdin while the writer is blocked in a write.
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, env=nyxr_env, bufsize=0)
        try:
            fcntl.fcntl(proc.stdin.fileno(), 1031, PIPE_SIZE)  # F_SETPIPE_SZ
        except OSError:
            pass
        with self.lock:
            self.nyxr = proc
            self.stopping_nyxr = False
            self.lock.notify_all()
        threading.Thread(target=self._watch_nyxr, args=(proc,), daemon=True).start()
        self.changed()

    def stop_stream(self):
        with self.lock:
            proc = self.nyxr
            if proc is None:
                self.streaming = False
                return
            self.stopping_nyxr = True
            self.nyxr = None
            self.streaming = False
            if self.playback == "playing":
                self.playback = "paused"
            self.lock.notify_all()
        log.info("stopping nyxr")
        try:
            proc.stdin.close()
        except OSError:
            pass
        # End of input alone left nyxr running for seconds; SIGINT makes it TEARDOWN at once.
        proc.send_signal(signal.SIGINT)
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
        self.changed()

    def _watch_nyxr(self, proc):
        code = proc.wait()
        with self.lock:
            if self.nyxr is not proc:
                return  # stopped on purpose
            # The receiver dropped us (another source took over, network loss...). nyxr does
            # not retry, and neither do we: retrying would steal the receiver back.
            log.warning("nyxr exited with %s", code)
            self.nyxr = None
            self.streaming = False
            if self.playback == "playing":
                self.playback = "paused"
            self.error = f"nyxr exited with {code}"
            self.lock.notify_all()
        self.changed()

    # -- threads ------------------------------------------------------------------------

    def _reader_loop(self):
        while True:
            with self.lock:
                while self.job is None:
                    self.lock.wait()
                gen = self.gen
                seq, track, offset = self.job
                self.job = None
                self.reading = seq
                length = self.lengths[track - 1]
                cd_track = self.cd_tracks[track - 1]
                epoch = self.disc_epoch
            reader = TrackReader(cd_track, offset, length, epoch)
            carry = b""
            cancelled = False
            while True:
                data = reader.read(SECTOR_BYTES * 8)
                if not data:
                    break
                data = carry + data
                cut = len(data) - len(data) % FRAME_BYTES
                data, carry = data[:cut], data[cut:]
                with self.lock:
                    while self.gen == gen and self.buffered >= (
                        SETTINGS["buffer_seconds"] * BYTES_PER_SECOND
                    ):
                        self.lock.wait()
                    if self.gen != gen:
                        cancelled = True
                        break
                    self.buffer.append((seq, track, data))
                    self.buffered += len(data)
                    self.lock.notify_all()
            if cancelled:
                reader.kill()
                with self.lock:
                    if self.reading == seq:
                        self.reading = None
                continue
            err = reader.finish()
            with self.lock:
                self.reading = None
                if self.gen != gen:
                    continue
                if err:
                    # Skip to the next track rather than stall on an unreadable one; the drive
                    # monitor stops everything if the drive itself went away. Several failures
                    # in a row mean the drive is in trouble: pause rather than keep at it.
                    log.error("%s", err)
                    self.error = err
                    self.failed_tracks += 1
                    if self.failed_tracks >= MAX_FAILED_TRACKS:
                        log.error("%d tracks failed in a row, pausing", self.failed_tracks)
                        self.failed_tracks = 0
                        if self.playback == "playing":
                            self.playback = "paused"
                        self.changed()
                        continue  # play() restarts the read
                    self.changed()
                    self.lock.wait(1)
                    if self.gen != gen:
                        continue
                else:
                    self.failed_tracks = 0
                nxt = self._next_track(track, manual=False)
                if nxt is not None:
                    self._new_job(nxt)
                self.lock.notify_all()

    def _writer_loop(self):
        silence = bytes(WRITE_CHUNK)
        while True:
            with self.lock:
                while self.nyxr is None:
                    self.lock.wait()
                proc = self.nyxr
                chunk = silence
                if self.playback == "playing":
                    if self.buffer:
                        seq, track, data = self.buffer[0]
                        if seq != self.playing_seq:
                            self.playing_seq = seq
                            self.track = track
                            self.played = 0
                            self._advance_order(track)
                            self.changed()
                        chunk = data[:WRITE_CHUNK]
                        if len(data) > WRITE_CHUNK:
                            self.buffer[0] = (seq, track, data[WRITE_CHUNK:])
                        else:
                            self.buffer.popleft()
                        self.buffered -= len(chunk)
                        self.played += len(chunk)
                        self.lock.notify_all()
                    # else: the reader is behind (a jump, or retrying after an error);
                    # play silence meanwhile.
            try:
                view = memoryview(chunk)
                while view:
                    view = view[proc.stdin.write(view) :]
            except (BrokenPipeError, ValueError, OSError):
                time.sleep(0.05)  # nyxr is gone; _watch_nyxr cleans up


SETTINGS = Settings()

# ---------------------------------------------------------------------------------------
# Bluetooth: an A2DP sink through the host's BlueZ and a PulseAudio-compatible sound server
#
# The host runs bluetoothd; its sound server (PulseAudio on Home Assistant OS, PipeWire's
# pulse server on desktop Linux) takes the A2DP stream and exposes it as a capture source.
# We register the pairing agent, open and close the pairing window, find and pair nearby
# devices, and record that source with `parec` into `nyxr --input-live`.

HOST_BUS = env("NYXR_COMPANION_HOST_BUS", "unix:path=/run/dbus/system_bus_socket")
BT_ADAPTER = env("NYXR_COMPANION_BT_ADAPTER", "")  # e.g. "hci1"; empty: the first adapter
AGENT_PATH = "/org/nyxr/companion/agent"
PAIRING_SECONDS = 180
A2DP_SINK_UUID = "0000110b-0000-1000-8000-00805f9b34fb"
# Devices that can send us audio. Only these count as ours: other pairings on the adapter
# (Home Assistant's own BLE devices, say) are left alone.
A2DP_SOURCE_UUID = "0000110a-0000-1000-8000-00805f9b34fb"
# A paired device that connects and drops again this soon without its services resolving has
# lost our pairing on its side: bluetoothd turns down its new pairing silently (Home Assistant
# OS keeps JustWorksRepairing = never), and the phone gives up after a few seconds.
STALE_PAIRING_SECONDS = 10
SOURCES = ("cd", "bluetooth")
REJECTED = "org.bluez.Error.Rejected"
# Device properties that change with every advertisement; Home Assistant's BLE scanning on
# the same adapter makes many of these a second, and none of them matter here.
NOISY_PROPS = {"RSSI", "TxPower", "ManufacturerData", "ServiceData", "AdvertisingFlags",
               "AdvertisingData"}
# Major device classes of things that play music to a speaker: computer, phone, audio/video.
SOURCE_CLASSES = {0x01, 0x02, 0x04}
# What Android sends over AVRCP when the playing app gives it no metadata.
NO_TITLE = {"", "Not Provided"}


class AuthHostExternal(AuthExternal):
    """EXTERNAL authentication without claiming a uid.

    In an unprivileged container the host bus sees the container's root as another uid
    than the 0 the usual EXTERNAL handshake claims, and would refuse it. Without a claimed
    uid it uses the one it sees, which also works as plain root.
    """

    def _authentication_start(self, negotiate_unix_fd=False):
        self.negotiate_unix_fd = negotiate_unix_fd
        return "AUTH EXTERNAL"

    def _receive_line(self, line):
        if line.startswith("DATA"):
            return "DATA"
        return super()._receive_line(line)


class PairingAgent(ServiceInterface):
    """BlueZ pairing agent with no display or keyboard (Just Works), like a headless speaker:
    accepts new devices only while the pairing window is open."""

    def __init__(self, bt):
        super().__init__("org.bluez.Agent1")
        self.bt = bt

    def _allow(self, device, what):
        if not self.bt.pairing and device != self.bt.pairing_with:
            log.info("bluetooth: refused %s for %s (pairing is off)", what, device)
            raise DBusError(REJECTED, "pairing is off")
        log.info("bluetooth: accepted %s for %s", what, device)

    @method()
    def Release(self):
        pass

    @method()
    def RequestPinCode(self, device: "o") -> "s":
        self._allow(device, "PIN request")
        return "0000"

    @method()
    def DisplayPinCode(self, device: "o", pincode: "s"):
        pass

    @method()
    def RequestPasskey(self, device: "o") -> "u":
        self._allow(device, "passkey request")
        return 0

    @method()
    def DisplayPasskey(self, device: "o", passkey: "u", entered: "q"):
        pass

    @method()
    def RequestConfirmation(self, device: "o", passkey: "u"):
        self._allow(device, "pairing")

    @method()
    def RequestAuthorization(self, device: "o"):
        self._allow(device, "pairing")

    @method()
    def AuthorizeService(self, device: "o", uuid: "s"):
        # BlueZ only asks for devices that are not trusted yet; paired ones are trusted.
        self._allow(device, f"service {uuid}")

    @method()
    def Cancel(self):
        pass


def _plain(value):
    """dbus-next Variants (possibly nested) to plain Python values."""
    if isinstance(value, Variant):
        return _plain(value.value)
    if isinstance(value, dict):
        return {k: _plain(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_plain(v) for v in value]
    return value


def _sbc_rate(config):
    """Sampling rate of an SBC A2DP configuration (first byte, high nibble)."""
    rates = {0x80: 16000, 0x40: 32000, 0x20: 44100, 0x10: 48000}
    return rates.get(config[0] & 0xF0) if config else None


def pulse_source(address):
    """Name of the sound server's capture source for a device's A2DP stream, or None.
    PulseAudio names it bluez_source.AA_BB_..., PipeWire bluez_input.AA:BB:...."""
    try:
        out = subprocess.run(
            ["pactl", "list", "short", "sources"], capture_output=True, text=True, timeout=5
        ).stdout
    except (OSError, subprocess.TimeoutExpired):
        return None
    keys = (address.replace(":", "_"), address)
    for line in out.splitlines():
        fields = line.split("\t")
        if len(fields) > 1 and fields[1].startswith("bluez_") and any(k in fields[1] for k in keys):
            return fields[1]
    return None


class Bluetooth:
    """A2DP sink: pairing, devices, the audio stream and AVRCP for the active device.

    D-Bus work runs on the asyncio loop; the stream commands (start / stop, like the CD
    player's) are called from executor threads.
    """

    mode = None  # no play modes; see Player.mode

    def __init__(self, loop, player):
        self.loop = loop
        self.player = player
        self.lock = threading.Lock()
        self.bus = None
        self.available = False
        self.adapter = None  # object path
        self.adapter_props = {}
        self.devices = []  # paired devices: {"path", "address", "name", "connected"}
        self.nearby = []  # unpaired devices seen while scanning: {"path", "address", "name"}
        self.pairing_with = None  # device path we are pairing from here
        self.connecting = {}  # device path -> (time it connected, services resolved)
        self.transport = None  # A2DP stream of the connected device: {"path", "device", ...}
        self.media = None  # AVRCP player of the connected device: {"path", "status", "track"}
        self.track_seq = 0  # bumped when the AVRCP track changes (MPRIS track id)
        self.track_key = None
        self.streaming = False
        self.error = None
        self.nyxr = None
        self.parec = None  # records the sound server's source into nyxr's stdin
        self.stream_device = None  # device path being streamed
        self.dirty = asyncio.Event()

    @property
    def pairing(self):
        return bool(self.adapter_props.get("Discoverable"))

    # -- state --------------------------------------------------------------------------

    def connected_device(self):
        if self.transport is not None:
            for d in self.devices:
                if d["path"] == self.transport["device"]:
                    return d
        return next((d for d in self.devices if d["connected"]), None)

    @property
    def playback(self):
        status = (self.media or {}).get("status")
        if status in ("playing", "forward-seek", "reverse-seek"):
            return "playing"
        if status == "paused":
            return "paused"
        if self.transport is not None and self.transport["running"]:
            return "playing"
        return "paused" if self.streaming else "stopped"

    def position(self):
        return ((self.media or {}).get("position") or 0) / 1000

    def track_info(self):
        device = self.connected_device()
        if device is None:
            return None
        track = (self.media or {}).get("track") or {}
        duration = track.get("Duration")
        title = track.get("Title")
        if title in NO_TITLE:
            title = None
        return {
            "number": track.get("TrackNumber") or 1,
            # Without AVRCP track info the receiver at least shows what is playing.
            "title": title or device["name"],
            "artist": track.get("Artist"),
            "album": track.get("Album"),
            "length": duration / 1000 if duration else None,
        }

    def snapshot(self):
        device = self.connected_device()
        transport = self.transport or {}
        return {
            "available": self.available,
            "name": self.adapter_props.get("Alias"),
            "pairing": self.pairing,
            "scanning": bool(self.adapter_props.get("Discovering")) and self.pairing,
            "devices": [
                {"address": d["address"], "name": d["name"], "connected": d["connected"]}
                for d in self.devices
            ],
            "nearby": [{"address": d["address"], "name": d["name"]} for d in self.nearby],
            "connected": device["name"] if device else None,
            "playing": bool(transport.get("running")),
            "codec": transport.get("codec"),
            "rate": transport.get("rate"),
            "streaming": self.streaming,
            "playback": self.playback,
            "track": self.track_info(),
            "error": self.error,
        }

    # -- D-Bus --------------------------------------------------------------------------

    async def _call(self, dest, path, iface, member, signature="", body=()):
        reply = await self.bus.call(
            Message(
                destination=dest,
                path=path,
                interface=iface,
                member=member,
                signature=signature,
                body=list(body),
            )
        )
        if reply.message_type == MessageType.ERROR:
            raise DBusError(reply.error_name, reply.body[0] if reply.body else "")
        return reply

    async def _set(self, dest, path, iface, prop, variant):
        await self._call(
            dest, path, "org.freedesktop.DBus.Properties", "Set", "ssv", (iface, prop, variant)
        )

    async def _objects(self, dest, path):
        reply = await self._call(dest, path, "org.freedesktop.DBus.ObjectManager", "GetManagedObjects")
        return _plain(reply.body[0])

    async def run(self):
        """Connects to the host bus and keeps state in step with it; reconnects if it goes."""
        while True:
            try:
                await self._session()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.warning("bluetooth unavailable: %s", e)
            self.available = False
            self.adapter = None
            self.player.changed()
            await asyncio.sleep(30)

    async def _session(self):
        self.bus = await MessageBus(
            bus_address=HOST_BUS, auth=AuthHostExternal(), negotiate_unix_fd=True
        ).connect()
        try:
            self.bus.export(AGENT_PATH, PairingAgent(self))
            self.bus.add_message_handler(self._on_message)
            for rule in (
                "type='signal',sender='org.bluez'",
                "type='signal',member='NameOwnerChanged',arg0='org.bluez'",
            ):
                await self._call(
                    "org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus",
                    "AddMatch", "s", (rule,),
                )
            await self._setup_bluez()
            self.dirty.set()
            refresh = asyncio.ensure_future(self._refresh_loop())
            try:
                await self.bus.wait_for_disconnect()
            finally:
                refresh.cancel()
        finally:
            self.bus.disconnect()

    def _on_message(self, msg):
        if msg.message_type != MessageType.SIGNAL:
            return None
        if msg.member == "NameOwnerChanged" and msg.body and msg.body[0] == "org.bluez":
            if msg.body[2]:  # bluetoothd (re)started: it forgot our agent
                asyncio.ensure_future(self._setup_bluez_logged())
        if msg.member == "PropertiesChanged" and msg.body and msg.body[0] == "org.bluez.Device1":
            self._watch_connection(msg.path, msg.body[1])
        if (
            msg.member == "PropertiesChanged"
            and msg.body
            and msg.body[0] == "org.bluez.Device1"
            and set(msg.body[1]) <= NOISY_PROPS
            and not (self.pairing and "RSSI" in msg.body[1])  # the nearby list while scanning
        ):
            return None
        self.dirty.set()
        return None

    def _watch_connection(self, path, changed):
        """Spots a paired device whose side of the pairing is gone (see STALE_PAIRING_SECONDS)
        and, while pairing is open, forgets ours so that it can pair again."""
        connected = changed.get("Connected")
        resolved = changed.get("ServicesResolved")
        if connected is not None and connected.value:
            self.connecting[path] = (time.monotonic(), False)
        if resolved is not None and resolved.value and path in self.connecting:
            self.connecting[path] = (self.connecting[path][0], True)
        if connected is None or connected.value:
            return
        since, resolved_once = self.connecting.pop(path, (None, True))
        device = next((d for d in self.devices if d["path"] == path), None)
        if (
            device is not None
            and self.pairing
            and not resolved_once
            and time.monotonic() - since < STALE_PAIRING_SECONDS
        ):
            log.info(
                "bluetooth: %s (%s) dropped our pairing; forgetting it so it can pair again",
                device["name"], device["address"],
            )
            asyncio.ensure_future(self._remove_logged(path))

    async def _remove_logged(self, path):
        try:
            await self._call(
                "org.bluez", self.adapter, "org.bluez.Adapter1", "RemoveDevice", "o", (path,)
            )
        except DBusError as e:
            log.warning("bluetooth: forgetting %s failed: %s", path, e.text)
        self.dirty.set()

    async def _setup_bluez_logged(self):
        # bluetoothd takes its name before it has registered the adapter: retry a while.
        for attempt in range(15):
            try:
                await self._setup_bluez()
                return
            except Exception as e:
                if attempt == 14:
                    log.warning("bluetooth setup failed: %s", e)
            await asyncio.sleep(1)

    async def _setup_bluez(self):
        objects = await self._objects("org.bluez", "/")
        adapters = sorted(p for p, ifaces in objects.items() if "org.bluez.Adapter1" in ifaces)
        if BT_ADAPTER:
            adapters = [p for p in adapters if p.endswith("/" + BT_ADAPTER)]
        if not adapters:
            raise RuntimeError("no Bluetooth adapter")
        self.adapter = adapters[0]
        await self._call(
            "org.bluez", "/org/bluez", "org.bluez.AgentManager1", "RegisterAgent", "os",
            (AGENT_PATH, "NoInputNoOutput"),
        )
        await self._call(
            "org.bluez", "/org/bluez", "org.bluez.AgentManager1", "RequestDefaultAgent", "o",
            (AGENT_PATH,),
        )
        adapter = ("org.bluez", self.adapter, "org.bluez.Adapter1")
        await self._set(*adapter, "Powered", Variant("b", True))
        await self._set(*adapter, "Alias", Variant("s", SETTINGS["bluetooth_name"]))
        await self._set(*adapter, "Discoverable", Variant("b", False))
        await self._set(*adapter, "Pairable", Variant("b", False))
        self.available = True
        log.info("bluetooth: %s as %r", self.adapter, SETTINGS["bluetooth_name"])

    async def _refresh_loop(self):
        while True:
            await self.dirty.wait()
            await asyncio.sleep(0.5)  # one refresh for a burst of signals
            self.dirty.clear()
            try:
                await self._refresh()
            except Exception as e:
                log.warning("bluetooth refresh failed: %s", e)

    async def _refresh(self):
        if self.adapter is None:
            return
        objects = await self._objects("org.bluez", "/")
        self.adapter_props = objects.get(self.adapter, {}).get("org.bluez.Adapter1", {})
        before = {d["path"] for d in self.devices}
        devices, nearby, players, transports = [], [], {}, []
        for path, ifaces in sorted(objects.items()):
            dev = ifaces.get("org.bluez.Device1")
            if dev and dev.get("Adapter") == self.adapter:
                entry = {
                    "path": path,
                    "address": dev.get("Address"),
                    "name": dev.get("Alias") or dev.get("Name") or dev.get("Address"),
                    "connected": bool(dev.get("Connected")),
                    "trusted": bool(dev.get("Trusted")),
                }
                if dev.get("Paired") and A2DP_SOURCE_UUID in (dev.get("UUIDs") or []):
                    devices.append(entry)
                elif (
                    self.pairing
                    and "RSSI" in dev
                    and dev.get("Name")
                    and ((dev.get("Class") or 0) >> 8) & 0x1F in SOURCE_CLASSES
                ):
                    nearby.append(entry)  # a named Classic device that can play music
            mp = ifaces.get("org.bluez.MediaPlayer1")
            if mp:
                players[mp.get("Device")] = {
                    "path": path,
                    "status": mp.get("Status"),
                    "track": mp.get("Track") or {},
                    "position": mp.get("Position"),
                }
            mt = ifaces.get("org.bluez.MediaTransport1")
            if mt and mt.get("UUID") == A2DP_SINK_UUID:
                transports.append(
                    {
                        "path": path,
                        "device": mt.get("Device"),
                        "running": mt.get("State") == "active",
                        "codec": {0: "SBC", 2: "AAC"}.get(mt.get("Codec"), mt.get("Codec")),
                        "rate": _sbc_rate(mt.get("Configuration")) if mt.get("Codec") == 0 else None,
                    }
                )
        self.devices = devices
        self.nearby = nearby
        for d in devices:
            if not d["trusted"]:
                # Newly paired: trust it so it reconnects without asking, and close the
                # window, as a speaker does once something has paired.
                log.info("bluetooth: paired with %s (%s)", d["name"], d["address"])
                await self._set("org.bluez", d["path"], "org.bluez.Device1", "Trusted", Variant("b", True))
                if self.pairing:
                    await self.set_pairing(False)
            elif d["path"] not in before:
                log.info("bluetooth: known device %s (%s)", d["name"], d["address"])

        # The device we follow: the one sending audio; else the one we followed while it is
        # still connected (paused); else any connected one.
        paired = {d["path"] for d in devices}
        candidates = [t for t in transports if t["device"] in paired]
        old = self.transport
        kept = next((t for t in candidates if old and t["device"] == old["device"]), None)
        running = [t for t in candidates if t["running"]]
        transport = (
            (kept if kept and kept["running"] else None)
            or (running[0] if running else None)
            or kept
            or (candidates[0] if candidates else None)
        )
        self.transport = transport
        self.media = players.get(transport["device"]) if transport else None
        if self.media is None and devices:
            self.media = next((players[d["path"]] for d in devices if d["path"] in players), None)
        track = (self.media or {}).get("track") or {}
        key = (track.get("Title"), track.get("Artist"), track.get("Album"))
        if key != self.track_key:
            self.track_key = key
            self.track_seq += 1

        started = transport is not None and transport["running"] and not (
            old is not None and old["path"] == transport["path"] and old["running"]
        )
        if started:
            log.info(
                "bluetooth: %s started playing (%s, %s Hz)",
                transport["device"], transport["codec"], transport["rate"],
            )
            if self.streaming and self.stream_device not in (None, transport["device"]):
                log.info("bluetooth: another device took over, restarting the stream")
                self.loop.run_in_executor(None, self._restart)
            elif SETTINGS["bluetooth_auto_switch"]:
                self.loop.run_in_executor(None, self._auto_switch)
        elif self.streaming and self.stream_device not in {t["device"] for t in candidates}:
            log.info("bluetooth: device gone, stopping the stream")
            self.loop.run_in_executor(None, self.stop)
        self.player.changed()

    # -- commands (asyncio loop) --------------------------------------------------------

    def _require(self):
        if not self.available:
            raise Conflict("Bluetooth is not available")

    async def set_pairing(self, on):
        """Opens the pairing window: discoverable for phones that pair from their side, and
        scanning for devices to pair from here (pair())."""
        self._require()
        adapter = ("org.bluez", self.adapter, "org.bluez.Adapter1")
        if on:
            await self._set(*adapter, "PairableTimeout", Variant("u", PAIRING_SECONDS))
            await self._set(*adapter, "DiscoverableTimeout", Variant("u", PAIRING_SECONDS))
        await self._set(*adapter, "Pairable", Variant("b", on))
        await self._set(*adapter, "Discoverable", Variant("b", on))
        try:
            if on:
                await self._call(
                    *adapter, "SetDiscoveryFilter", "a{sv}", ({"Transport": Variant("s", "bredr")},)
                )
                await self._call(*adapter, "StartDiscovery")
            else:
                await self._call(*adapter, "StopDiscovery")
        except DBusError as e:
            log.debug("bluetooth discovery: %s", e)  # e.g. not discovering on our behalf
        log.info("bluetooth: pairing %s", "open for %d s" % PAIRING_SECONDS if on else "closed")
        self.adapter_props["Discoverable"] = on
        if on:
            asyncio.ensure_future(self._close_pairing_later())
        self.dirty.set()

    async def _close_pairing_later(self):
        # Discoverable times out by itself; scanning does not.
        await asyncio.sleep(PAIRING_SECONDS)
        if self.pairing and self.available:
            await self.set_pairing(False)

    async def pair(self, address):
        """Pairs with a nearby device from this side: for devices whose own pairing request
        does not get through (an iPod's weak radio reaches us when we call it, not the other
        way round)."""
        self._require()
        device = next((d for d in self.nearby if d["address"] == address), None)
        if device is None:
            raise BadRequest(f"no nearby device {address!r}; open pairing and wait for it")
        self.pairing_with = device["path"]
        log.info("bluetooth: pairing with %s (%s)", device["name"], address)
        try:
            await self._call("org.bluez", device["path"], "org.bluez.Device1", "Pair")
            await self._set("org.bluez", device["path"], "org.bluez.Device1", "Trusted", Variant("b", True))
            try:
                await self._call("org.bluez", device["path"], "org.bluez.Device1", "Connect")
            except DBusError as e:
                log.info("bluetooth: paired; connect it from the device (%s)", e.text)
        finally:
            self.pairing_with = None
        if self.pairing:
            await self.set_pairing(False)
        self.dirty.set()

    async def disconnect(self):
        self._require()
        for d in self.devices:
            if d["connected"]:
                await self._call("org.bluez", d["path"], "org.bluez.Device1", "Disconnect")
        self.dirty.set()

    async def forget(self, address=None):
        """Removes the pairing with one device, or with all of ours (devices that can send us
        audio; other pairings on the adapter stay)."""
        self._require()
        devices = [d for d in self.devices if address is None or d["address"] == address]
        if address is not None and not devices:
            raise BadRequest(f"no paired device {address!r}")
        for d in devices:
            await self._call(
                "org.bluez", self.adapter, "org.bluez.Adapter1", "RemoveDevice", "o", (d["path"],)
            )
        self.dirty.set()

    async def apply_name(self):
        if self.available:
            await self._set(
                "org.bluez", self.adapter, "org.bluez.Adapter1", "Alias",
                Variant("s", SETTINGS["bluetooth_name"]),
            )
            self.dirty.set()

    async def _avrcp(self, member):
        if self.media is not None:
            await self._call("org.bluez", self.media["path"], "org.bluez.MediaPlayer1", member)

    def _sync(self, coro, timeout=10):
        """Runs a D-Bus coroutine from an executor thread."""
        return asyncio.run_coroutine_threadsafe(coro, self.loop).result(timeout)

    # -- player commands (executor threads; same names as Player's) ---------------------

    def _auto_switch(self):
        try:
            self.player.sources.set_source("bluetooth")
            self.start_stream()
        except Conflict as e:
            log.warning("bluetooth auto switch: %s", e)

    def _restart(self):
        self.stop()
        self.start_stream()

    def start_stream(self):
        with self.lock:
            if self.streaming:
                return
            device = self.connected_device()
            if device is None:
                raise Conflict("no Bluetooth device is connected")
            self.streaming = True
            self.error = None
        source = None
        for _ in range(10):  # the sound server adds it a moment after the device connects
            source = pulse_source(device["address"])
            if source:
                break
            time.sleep(0.5)
        try:
            if source is None:
                raise Conflict("the sound server has no audio from the Bluetooth device")
            cmd = nyxr_command(44100, 2, SETTINGS["bluetooth_buffer_ms"])
        except Conflict as e:
            with self.lock:
                self.streaming = False
                self.error = str(e)
            self.player.changed()
            raise
        rec = [
            "parec", "--raw", "--format=s16le", "--rate=44100", "--channels=2",
            "--latency-msec=20", f"--device={source}",
        ]
        log.info("starting nyxr for bluetooth: %s | %s", shlex.join(rec), shlex.join(cmd))
        try:
            parec = subprocess.Popen(rec, stdout=subprocess.PIPE)
        except OSError:
            with self.lock:
                self.streaming = False
            raise
        try:
            proc = subprocess.Popen(cmd, stdin=parec.stdout, env=nyxr_env)
        except OSError:
            parec.kill()
            parec.wait()
            with self.lock:
                self.streaming = False
            raise
        finally:
            parec.stdout.close()  # nyxr has its own copy
        with self.lock:
            self.nyxr = proc
            self.parec = parec
            self.stream_device = device["path"]
        threading.Thread(target=self._watch_nyxr, args=(proc,), daemon=True).start()
        self.player.changed()

    @staticmethod
    def _end(proc, parec):
        if proc is not None:
            # SIGINT makes nyxr end the stream (TEARDOWN) as closing stdin does for the CD.
            proc.send_signal(signal.SIGINT)
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
        if parec is not None:
            parec.terminate()
            try:
                parec.wait(timeout=5)
            except subprocess.TimeoutExpired:
                parec.kill()
                parec.wait()

    def stop(self):
        """Ends the stream (the HA switch); the device stays connected."""
        with self.lock:
            proc, parec = self.nyxr, self.parec
            self.nyxr = self.parec = self.stream_device = None
            self.streaming = False
        if proc is not None:
            log.info("stopping nyxr for bluetooth")
        self._end(proc, parec)
        self.player.changed()

    def _watch_nyxr(self, proc):
        code = proc.wait()
        with self.lock:
            if self.nyxr is not proc:
                return  # stopped on purpose
            log.warning("nyxr for bluetooth exited with %s", code)
            parec = self.parec
            self.nyxr = self.parec = self.stream_device = None
            self.streaming = False
            self.error = f"nyxr exited with {code}"
        self._end(None, parec)
        self.player.changed()

    def play(self, track=None):
        self.start_stream()
        if self.playback != "playing":
            self._sync(self._avrcp("Play"))

    def pause(self):
        self._sync(self._avrcp("Pause"))

    def play_pause(self):
        if self.playback == "playing":
            self.pause()
        else:
            self.play()

    def next(self):
        self._sync(self._avrcp("Next"))

    def previous(self):
        self._sync(self._avrcp("Previous"))

    def seek(self, seconds):
        pass  # AVRCP has no absolute seek


class Sources:
    """Routes player commands to the selected source and switches between them: one stream
    at a time, so selecting one ends the other's."""

    def __init__(self, player, bt):
        self.player = player
        self.bt = bt
        player.sources = self

    @property
    def active(self):
        return self.bt if self.player.source == "bluetooth" else self.player

    def call(self, name, *args):
        return getattr(self.active, name)(*args)

    def set_source(self, source):
        if source not in SOURCES:
            raise BadRequest(f"source must be one of {', '.join(SOURCES)}")
        if source == self.player.source:
            return
        log.info("source: %s", source)
        if source == "bluetooth":
            self.player.stop()  # keeps the CD position
        else:
            self.bt.stop()
        with self.player.lock:
            self.player.source = source
        self.player.changed()


# ---------------------------------------------------------------------------------------
# MPRIS

NO_TRACK = "/org/mpris/MediaPlayer2/TrackList/NoTrack"
LOOP_OF_MODE = {"repeat_one": "Track", "repeat_all": "Playlist", "shuffle": "Playlist"}


class MprisRoot(ServiceInterface):
    def __init__(self):
        super().__init__("org.mpris.MediaPlayer2")

    @method()
    def Raise(self):
        pass

    @method()
    def Quit(self):
        pass

    @dbus_property(access=PropertyAccess.READ)
    def CanQuit(self) -> "b":
        return False

    @dbus_property(access=PropertyAccess.READ)
    def CanRaise(self) -> "b":
        return False

    @dbus_property(access=PropertyAccess.READ)
    def HasTrackList(self) -> "b":
        return False

    @dbus_property(access=PropertyAccess.READ)
    def Identity(self) -> "s":
        return "NyxR Companion"

    @dbus_property(access=PropertyAccess.READ)
    def SupportedUriSchemes(self) -> "as":
        return []

    @dbus_property(access=PropertyAccess.READ)
    def SupportedMimeTypes(self) -> "as":
        return []


class MprisPlayer(ServiceInterface):
    """The selected source (CD or Bluetooth) as one MPRIS player for nyxr."""

    def __init__(self, sources):
        super().__init__("org.mpris.MediaPlayer2.Player")
        self.s = sources
        self.p = sources.player
        self.last = None

    def _run(self, name, *args):
        # Player commands may block briefly (starting / stopping nyxr); keep D-Bus responsive.
        def run():
            try:
                self.s.call(name, *args)
            except (BadRequest, Conflict) as e:
                log.warning("MPRIS %s: %s", name, e)

        self.p.loop.run_in_executor(None, run)

    def _on_cd(self):
        return self.s.active is self.p

    @method()
    def Play(self):
        self._run("play")

    @method()
    def Pause(self):
        self._run("pause")

    @method()
    def PlayPause(self):
        self._run("play_pause")

    @method()
    def Stop(self):
        self._run("pause")  # keep the stream; the HA switch ends it

    @method()
    def Next(self):
        self._run("next")

    @method()
    def Previous(self):
        self._run("previous")

    @method()
    def Seek(self, offset: "x"):
        self._run("seek", self.s.active.position() + offset / 1e6)

    @method()
    def SetPosition(self, track_id: "o", position: "x"):
        self._run("seek", position / 1e6)

    @method()
    def OpenUri(self, uri: "s"):
        pass

    @dbus_signal()
    def Seeked(self) -> "x":
        return int(self.s.active.position() * 1e6)

    @dbus_property(access=PropertyAccess.READ)
    def PlaybackStatus(self) -> "s":
        return {"playing": "Playing", "paused": "Paused"}.get(self.s.active.playback, "Stopped")

    @dbus_property()
    def LoopStatus(self) -> "s":
        return LOOP_OF_MODE[self.p.mode] if self._on_cd() else "None"

    @LoopStatus.setter
    def LoopStatus(self, value: "s"):
        # A CD always wraps around, so "None" is the same as "Playlist".
        mode = {"None": "repeat_all", "Track": "repeat_one", "Playlist": "repeat_all"}.get(value)
        if mode and self._on_cd() and self.p.mode != "shuffle":
            self.p.set_mode(mode)

    @dbus_property()
    def Shuffle(self) -> "b":
        return self._on_cd() and self.p.mode == "shuffle"

    @Shuffle.setter
    def Shuffle(self, value: "b"):
        if not self._on_cd():
            return
        if value:
            self.p.set_mode("shuffle")
        elif self.p.mode == "shuffle":
            self.p.set_mode("repeat_all")

    def _track_id(self):
        if self._on_cd():
            return f"/org/nyx/cd/track/{self.p.track or 0}"
        return f"/org/nyx/cd/bluetooth/{self.s.bt.track_seq}"

    @dbus_property(access=PropertyAccess.READ)
    def Metadata(self) -> "a{sv}":
        info = self.s.active.track_info()
        if info is None:
            return {"mpris:trackid": Variant("o", NO_TRACK)}
        meta = {
            "mpris:trackid": Variant("o", self._track_id()),
            "xesam:title": Variant("s", info["title"]),
            "xesam:trackNumber": Variant("i", info["number"]),
        }
        if info.get("length"):
            meta["mpris:length"] = Variant("x", int(info["length"] * 1e6))
        if info.get("artist"):
            meta["xesam:artist"] = Variant("as", [info["artist"]])
        if info.get("album"):
            meta["xesam:album"] = Variant("s", info["album"])
        if info.get("album_artist"):
            meta["xesam:albumArtist"] = Variant("as", [info["album_artist"]])
        if info.get("cover_url"):
            meta["mpris:artUrl"] = Variant("s", info["cover_url"])
        return meta

    @dbus_property(access=PropertyAccess.READ)
    def Position(self) -> "x":
        return int(self.s.active.position() * 1e6)

    @dbus_property(access=PropertyAccess.READ)
    def Volume(self) -> "d":
        return 1.0

    @dbus_property(access=PropertyAccess.READ)
    def Rate(self) -> "d":
        return 1.0

    @dbus_property(access=PropertyAccess.READ)
    def MinimumRate(self) -> "d":
        return 1.0

    @dbus_property(access=PropertyAccess.READ)
    def MaximumRate(self) -> "d":
        return 1.0

    def _has_disc(self):
        if not self._on_cd():
            return self.s.bt.connected_device() is not None
        return self.p.disc is not None

    @dbus_property(access=PropertyAccess.READ)
    def CanGoNext(self) -> "b":
        return self._has_disc()

    @dbus_property(access=PropertyAccess.READ)
    def CanGoPrevious(self) -> "b":
        return self._has_disc()

    @dbus_property(access=PropertyAccess.READ)
    def CanPlay(self) -> "b":
        return self._has_disc()

    @dbus_property(access=PropertyAccess.READ)
    def CanPause(self) -> "b":
        return self._has_disc()

    @dbus_property(access=PropertyAccess.READ)
    def CanSeek(self) -> "b":
        return self._on_cd() and self._has_disc()

    @dbus_property(access=PropertyAccess.READ)
    def CanControl(self) -> "b":
        return True

    def refresh(self):
        state = (
            self.PlaybackStatus,
            self.LoopStatus,
            self.Shuffle,
            self._track_id(),
            self.p.disc,
            repr(self.s.active.track_info()),
        )
        if state == self.last:
            return
        self.last = state
        self.emit_properties_changed(
            {
                "PlaybackStatus": self.PlaybackStatus,
                "LoopStatus": self.LoopStatus,
                "Shuffle": self.Shuffle,
                "Metadata": self.Metadata,
                "CanGoNext": self.CanGoNext,
                "CanGoPrevious": self.CanGoPrevious,
                "CanPlay": self.CanPlay,
                "CanPause": self.CanPause,
                "CanSeek": self.CanSeek,
            }
        )


nyxr_env = dict(os.environ)


async def start_mpris(sources):
    """Starts a private session bus for us and nyxr, and exports MPRIS on it."""
    os.makedirs(RUN_DIR, exist_ok=True)
    sock = os.path.join(RUN_DIR, "bus")
    if os.path.exists(sock):
        os.unlink(sock)
    address = f"unix:path={sock}"
    daemon = subprocess.Popen(
        ["dbus-daemon", "--session", "--nofork", "--nopidfile", f"--address={address}"]
    )
    for _ in range(50):
        if os.path.exists(sock):
            break
        await asyncio.sleep(0.1)
    nyxr_env["DBUS_SESSION_BUS_ADDRESS"] = address
    bus = await MessageBus(bus_address=address).connect()
    iface = MprisPlayer(sources)
    bus.export("/org/mpris/MediaPlayer2", MprisRoot())
    bus.export("/org/mpris/MediaPlayer2", iface)
    await bus.request_name("org.mpris.MediaPlayer2.nyxr_companion")
    sources.player.listeners.append(iface.refresh)
    return daemon, bus


# ---------------------------------------------------------------------------------------
# HTTP API

def make_app(sources, token):
    player, bt = sources.player, sources.bt
    sockets = set()

    @web.middleware
    async def require_token(request, handler):
        if request.headers.get("Authorization", "") != f"Bearer {token}":
            return web.json_response({"error": "missing or wrong API token"}, status=401)
        return await handler(request)

    def broadcast():
        if not sockets:
            return
        text = json.dumps(player.snapshot(), ensure_ascii=False)
        for ws in list(sockets):
            asyncio.ensure_future(ws.send_str(text))

    player.listeners.append(broadcast)

    async def ticker():
        while True:
            await asyncio.sleep(5)
            if sources.active.playback == "playing":
                broadcast()

    async def on_startup(app):
        app["ticker"] = asyncio.ensure_future(ticker())

    async def on_shutdown(app):
        # Otherwise cleanup waits for clients (HA) to hang up.
        for ws in list(sockets):
            await ws.close(code=WSCloseCode.GOING_AWAY)

    async def state(request):
        return web.json_response(player.snapshot(), dumps=dumps)

    async def ws_handler(request):
        ws = web.WebSocketResponse(heartbeat=30)
        await ws.prepare(request)
        await ws.send_str(json.dumps(player.snapshot(), ensure_ascii=False))
        sockets.add(ws)
        try:
            async for msg in ws:
                if msg.type == WSMsgType.ERROR:
                    break
        finally:
            sockets.discard(ws)
        return ws

    def command(fn):
        async def handler(request):
            try:
                body = await request.json() if request.can_read_body else {}
            except ValueError:
                return web.json_response({"error": "invalid JSON"}, status=400)
            if not isinstance(body, dict):
                return web.json_response({"error": "body must be an object"}, status=400)
            try:
                await asyncio.get_running_loop().run_in_executor(None, fn, body)
            except BadRequest as e:
                return web.json_response({"error": str(e)}, status=400)
            except Conflict as e:
                return web.json_response({"error": str(e)}, status=409)
            return web.json_response(player.snapshot(), dumps=dumps)

        return handler

    def bt_command(fn):
        """Like command(), for Bluetooth coroutines run on the loop."""

        async def handler(request):
            try:
                body = await request.json() if request.can_read_body else {}
            except ValueError:
                return web.json_response({"error": "invalid JSON"}, status=400)
            if not isinstance(body, dict):
                return web.json_response({"error": "body must be an object"}, status=400)
            try:
                await fn(body)
            except BadRequest as e:
                return web.json_response({"error": str(e)}, status=400)
            except Conflict as e:
                return web.json_response({"error": str(e)}, status=409)
            except DBusError as e:
                return web.json_response({"error": f"bluetooth: {e.text}"}, status=409)
            return web.json_response(player.snapshot(), dumps=dumps)

        return handler

    async def pairing(body):
        if not isinstance(body.get("on"), bool):
            raise BadRequest('"on" must be true or false')
        await bt.set_pairing(body["on"])

    async def forget(body):
        address = body.get("address")
        if address is not None and not isinstance(address, str):
            raise BadRequest('"address" must be a Bluetooth address')
        await bt.forget(address)

    async def pair(body):
        if not isinstance(body.get("address"), str):
            raise BadRequest('"address" must be a Bluetooth address')
        await bt.pair(body["address"])

    def stream(body):
        if not isinstance(body.get("on"), bool):
            raise BadRequest('"on" must be true or false')
        sources.call("play" if body["on"] else "stop")

    def play(body):
        track = body.get("track")
        if track is not None and not isinstance(track, int):
            raise BadRequest('"track" must be an integer')
        sources.call("play", track)

    async def refresh_receivers(request):
        await find_receivers(player)
        return web.json_response(player.snapshot(), dumps=dumps)

    app = web.Application(middlewares=[require_token])
    app.on_startup.append(on_startup)
    app.on_shutdown.append(on_shutdown)
    app.router.add_get("/api/state", state)
    app.router.add_get("/api/ws", ws_handler)
    app.router.add_post("/api/stream", command(stream))
    app.router.add_post("/api/mode", command(lambda b: player.set_mode(b.get("mode"))))
    app.router.add_post("/api/eject", command(lambda b: player.eject()))
    app.router.add_post("/api/play", command(play))
    app.router.add_post("/api/pause", command(lambda b: sources.call("pause")))
    app.router.add_post("/api/next", command(lambda b: sources.call("next")))
    app.router.add_post("/api/previous", command(lambda b: sources.call("previous")))
    app.router.add_post("/api/settings", command(player.update_settings))
    app.router.add_post("/api/receivers/refresh", refresh_receivers)
    app.router.add_post("/api/source", command(lambda b: sources.set_source(b.get("source"))))
    app.router.add_post("/api/bluetooth/pairing", bt_command(pairing))
    app.router.add_post("/api/bluetooth/pair", bt_command(pair))
    app.router.add_post("/api/bluetooth/disconnect", bt_command(lambda b: bt.disconnect()))
    app.router.add_post("/api/bluetooth/forget", bt_command(forget))
    return app


def dumps(obj):
    return json.dumps(obj, ensure_ascii=False)


# ---------------------------------------------------------------------------------------
# Drive monitor

def in_daemon_thread(fn, *args):
    """Like run_in_executor, but the thread does not hold up shutdown: musicbrainzngs keeps
    retrying for over a minute while MusicBrainz answers 503."""
    loop = asyncio.get_running_loop()
    future = loop.create_future()

    def settle(result, error):
        if not future.done():
            future.set_exception(error) if error else future.set_result(result)

    def run():
        try:
            result, error = fn(*args), None
        except Exception as e:
            result, error = None, e
        try:
            loop.call_soon_threadsafe(settle, result, error)
        except RuntimeError:
            pass  # the loop is closed: shutting down

    threading.Thread(target=run, daemon=True).start()
    return future


NOT_AUDIO = object()


async def monitor_drive(player):
    loop = asyncio.get_running_loop()
    loaded = None  # disc id we have read the TOC for, or NOT_AUDIO
    failures = 0  # TOC reads that failed in a row
    last_status, last_limit = None, 0.0
    while True:
        status = await loop.run_in_executor(None, drive_status)
        if status != "missing" and (status != last_status or time.monotonic() - last_limit > 30):
            await loop.run_in_executor(None, limit_speed)
            last_limit = time.monotonic()
        last_status = status
        if status == "not_ready" and loaded is not None:
            await asyncio.sleep(2)
            continue  # spinning up or busy reading; the disc has not changed
        if status != "ok":
            loaded, failures = None, 0
        elif loaded is None:
            try:
                disc_id, lengths, cd_tracks = await loop.run_in_executor(None, read_toc)
            except NotAudio:
                log.info("not an audio CD")
                loaded = NOT_AUDIO
            except Exception as e:  # libdiscid raises its own error types
                failures += 1
                log.warning("reading the TOC failed (%d): %s", failures, e)
                if failures >= 3:
                    loaded = NOT_AUDIO  # stop retrying until the disc changes
            else:
                loaded = disc_id
                log.info("disc %s, audio tracks %s", disc_id, cd_tracks)
                player.set_drive("ok")
                # Shown at once; MusicBrainz replaces it when it knows the disc.
                try:
                    cdtext = await loop.run_in_executor(None, read_cdtext, cd_tracks)
                except Exception as e:
                    log.warning("reading CD-Text failed: %s", e)
                    cdtext = None
                if cdtext:
                    log.info("CD-Text: %s / %s", cdtext.get("artist"), cdtext.get("album"))
                player.disc_loaded(disc_id, lengths, cd_tracks, cdtext)
                try:
                    meta = await asyncio.wait_for(
                        in_daemon_thread(lookup_metadata, disc_id, len(lengths)), 30
                    )
                except Exception as e:
                    log.warning("MusicBrainz lookup failed: %s", e)
                    meta = None
                if meta:
                    log.info("disc is %s / %s", meta.get("artist"), meta.get("album"))
                    player.disc_loaded(disc_id, lengths, cd_tracks, meta)
        player.set_drive("not_audio" if loaded == NOT_AUDIO else status)
        await asyncio.sleep(2)


async def find_receivers(player):
    try:
        receivers = await asyncio.get_running_loop().run_in_executor(None, discover_receivers)
    except (OSError, subprocess.SubprocessError) as e:
        log.warning("looking for AirPlay receivers failed: %s", e)
        return
    with player.lock:
        changed = receivers != player.receivers
        player.receivers = receivers
    if changed:
        log.info("AirPlay receivers: %s", ", ".join(f"{r['name']} ({r['address']})" for r in receivers) or "none")
        player.changed()


async def watch_receivers(player):
    while True:
        await find_receivers(player)
        await asyncio.sleep(300)


async def announce(host, port, token):
    """Tells Home Assistant about the API through Supervisor discovery (in the add-on), so the
    integration sets itself up with the token."""
    supervisor_token = os.environ.get("SUPERVISOR_TOKEN")
    if not supervisor_token:
        return
    config = {"host": "127.0.0.1" if host in ("", "0.0.0.0", "::") else host, "port": port, "token": token}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                "http://supervisor/discovery",
                json={"service": "nyxr_companion", "config": config},
                headers={"Authorization": f"Bearer {supervisor_token}"},
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status != 200:
                    log.warning("Supervisor discovery failed: %s %s", resp.status, await resp.text())
                    return
        log.info("announced to Home Assistant through Supervisor discovery")
    except (aiohttp.ClientError, TimeoutError) as e:
        log.warning("Supervisor discovery failed: %s", e)


async def main():
    logging.basicConfig(
        level=env("NYXR_COMPANION_LOG", "INFO").upper(), format="%(asctime)s %(levelname)s %(message)s"
    )
    loop = asyncio.get_running_loop()
    player = Player(loop)
    bt = Bluetooth(loop, player)
    player.bt = bt
    sources = Sources(player, bt)
    bus_daemon, bus = await start_mpris(sources)
    bluetooth = asyncio.ensure_future(bt.run())

    token = api_token()
    runner = web.AppRunner(make_app(sources, token))
    await runner.setup()
    host, _, port = LISTEN.rpartition(":")
    await web.TCPSite(runner, host or "0.0.0.0", int(port)).start()
    log.info("listening on %s", LISTEN)
    await announce(host, int(port), token)
    receivers = asyncio.ensure_future(watch_receivers(player))

    stop = asyncio.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)
    monitor = asyncio.ensure_future(monitor_drive(player))
    await stop.wait()

    log.info("shutting down")
    monitor.cancel()
    receivers.cancel()
    await loop.run_in_executor(None, player.stop_stream)
    await loop.run_in_executor(None, bt.stop)
    bluetooth.cancel()
    await runner.cleanup()
    bus.disconnect()
    try:
        await bus.wait_for_disconnect()
    except Exception:
        pass  # systemd has signalled dbus-daemon too, so the bus may already be gone
    bus_daemon.terminate()


if __name__ == "__main__":
    asyncio.run(main())
