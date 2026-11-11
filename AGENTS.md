# AGENTS.md — NyxR Companion

Notes for coding agents (and people) working in this repository. `CLAUDE.md` is a symlink to this file.

## Layout

- `nyxr_companion/`: the Home Assistant add-on. One Python daemon (`nyxr_companion.py`) runs the CD player (audio CDs, and data discs of music files, read without mounting by `discfs.py` and decoded by ffmpeg), the Bluetooth receiver (BlueZ + the host's PulseAudio) and the AirPlay receiver (shairport-sync, avahi, nqptp), and feeds one `nyxr` at a time. The image is built from shairport-sync's official image (Alpine), pinned by digest; `nyxr` comes from the Nyx Refrain release (`NYXR_VERSION`), checked against its `SHA256SUMS`.
- `custom_components/nyxr_companion/`: the integration (HACS), talking to the daemon's HTTP + WebSocket API (`API.md`, `API.zh-cn.md`).
- `tests/`: the integration against a fake add-on (`fake_server.py`, which follows `API.md`), the daemon's AirPlay source against a fake shairport-sync and nyxr (`test_airplay.py`), the disc file system reader against real images (`test_discfs.py`, `fixtures/discfs/`), and the daemon playing a data disc with a directory for the disc and the real ffmpeg (`test_data_disc.py`).

## Checks

- `python3 -m unittest discover -s tests`. The daemon's tests need its dependencies (`aiohttp`, `dbus-next`; `test_data_disc.py` also `ffmpeg`) and skip without them; they also run in the add-on image: `docker build -t nyxr-companion-test nyxr_companion && docker run --rm -v $PWD:/src:ro -w /src --entrypoint python3 nyxr-companion-test -m unittest discover -s tests`.
- CI (`.github/workflows/validate.yml`) runs HACS validation, hassfest, the add-on linter and the tests.
- hassfest wants the manifest keys as `domain`, `name`, then the rest in alphabetical order. The add-on linter rejects options set to their default (`startup: application`, `boot: auto`).
- Keep `API.md` and `API.zh-cn.md`, `README.md` and `README.zh-cn.md`, and the three translation files (`strings.json`, `translations/en.json`, `translations/zh-Hans.json`) in step.

## Home Assistant OS facts the code relies on

- Supervisor renamed add-ons to apps: local ones go in `/mnt/data/supervisor/apps/local/<slug>`; `build.yaml`'s `build_from` is ignored, so the Dockerfile names its base itself.
- **A change to `config.yaml`'s permissions (`hassio_api`, `discovery`, devices, privileges) needs a higher `version` and an update**: a rebuild or restart at the same version keeps the old ones. When the integration and the add-on change together, update the integration and restart Home Assistant first, then the add-on, or entities created by the new discovery come up without their translated names.
- The add-on cannot own a name on the host's system bus (bluealsa could not), so Bluetooth audio is recorded from the host's PulseAudio (`parec`), and avahi talks over the daemon's private bus (`DBUS_SYSTEM_BUS_ADDRESS`), never the mounted host bus.
- The host already answers mDNS for its own name (systemd-resolved), so avahi uses `nyxr-companion.local`, and only on interfaces with a default route (not Supervisor's `hassio` bridge).
- HAOS's BlueZ cannot be configured (`JustWorksRepairing` is `never`): a device that removed its pairing is turned down silently; the daemon notices it connecting and dropping without services and forgets the old pairing.
- In a VM, an optical drive passed through as a raw SCSI device refuses `CDROM_SELECT_SPEED`; the daemon falls back to SET CD SPEED over SG_IO (`SYS_RAWIO`).

## Data discs

- **Nothing is mounted.** Home Assistant OS's kernel has ISO 9660 but no UDF, and mounting from an add-on needs `SYS_ADMIN` and no AppArmor. `discfs.py` reads ISO 9660 (Joliet, Rock Ridge, multi-extent, multisession) and UDF 1.02–2.60 (plain, sparable, virtual / VAT, metadata partitions) itself, in pure Python, and gives each file's place on the disc. 7-Zip and libcdio's libudf only read plain UDF up to 2.01.
- ffmpeg is pointed at the drive with its `subfile` protocol (offset and length of the file on the device), so it seeks by itself; a file in several pieces is piped to it instead. A file's tags, length and picture are read only when it is about to be played.
- On a disc with both ISO 9660 and UDF, Rock Ridge names win (genisoimage cuts the names in its UDF short), then UDF, then Joliet (64 characters) and plain ISO 9660.
- The base image has its own small FFmpeg libraries in `/usr/local/lib` for shairport-sync, which the loader finds before Alpine's: `ffmpeg` and `ffprobe` in `/usr/local/bin` are wrappers that set `LD_LIBRARY_PATH=/usr/lib`.
- Fixtures: `tests/fixtures/discfs/mktree.py` writes the file tree (a file's content follows from its size, and sizes are unique, so a test can check any file whatever its name became); `mkudf.sh` builds the UDF images and needs root and the `udf` kernel module. Linux writes UDF only up to 2.01 and no VAT, so the 2.50 and VAT images are empty volumes, and the metadata partition test builds its volume by hand. A volume written by Windows (BD-RE: 2.50 with sparing and metadata; "like a USB flash drive" on DVD-R: VAT) has not been tested.
- **In a VM the guest kernel's view of the disc goes stale.** With the drive passed through as a raw SCSI device, `/sys/block/sr0/size` (and so `lseek(END)` on `/dev/sr0`) and `CDROMMULTISESSION` keep the previous disc's values after a swap, while the drive answers correctly (READ CAPACITY, READ DISC INFORMATION). Observed: stale with the add-on stopped, right after the add-on opened the device with the disc ready. A reading of `drivers/scsi/sr.c`, not confirmed: the capacity is read only when the device is opened after the kernel noticed a media change, and that read is skipped if the drive is not ready yet, which a disc still spinning up is; the media-change event is spent either way. So the daemon polls the drive on one descriptor kept open (opening `/dev/sr0` is what triggers the check), lets the kernel note an empty drive (`CDROM_MEDIA_CHANGED`), and opens the device afresh once the drive is ready. It also copes with what is left: blocks past the kernel's size are read with READ (12) over SG_IO (`discfs.Device.extend`), the last session's start comes from READ DISC INFORMATION / READ TRACK INFORMATION rather than `CDROMMULTISESSION`, and a file that cannot be read directly is piped to ffmpeg. The MP4 family (moov at the end) cannot be piped, so such a file is copied to a temporary file first. The kernel cannot be refreshed from the add-on: no `CAP_SYS_ADMIN` for `BLKRRPART`, and `rescan` in sysfs does nothing.
- **The VM's own udev probes a new disc for tens of seconds** (blkid reads the end of the disc, in 4 KB pieces). Reads of the daemon's at the same time make the head swing between the two, every read takes a quarter of a second, and a one second ffprobe takes fifteen. The daemon waits until nobody else has read the drive for a moment before it scans a new data disc (`wait_for_quiet_drive`, which watches `/sys/block/sr0/stat`). A block-layer trace (`/sys/kernel/tracing/events/block/block_rq_issue`) in the VM shows who reads where.
- `ffmpeg -an -map 0:v:0 -frames:v 1` reads the whole file for a picture found in the first kilobytes, in small requests (62 MB file: 134 MB read, 34 s), which also starves the song being played. A FLAC file's picture is read from its metadata blocks (`flac_picture`); other formats still go through ffmpeg, after the sound has started.
- **Folder**: picking a folder keeps playing to it (`scope`): next, previous, the end of a track and shuffle stay inside it. Shuffle is a bag: the pool is shuffled, played through, and a new shuffle is appended (the first of it never equal to the last of the old one).
- The disc's music files are numbered over the whole disc, but the state object lists only the current folder's tracks (a disc may hold thousands, and the state is sent whole on every change).

## Conventions

- The add-on never changes the speaker's volume (`nyxr --keep-volume`): Home Assistant users already have the speaker's own volume control.
- Settings a user may want to change are entities in Home Assistant, kept by the add-on in `/data/settings.json`, rather than add-on options or files.
- Raise `version` in `config.yaml`, `manifest.json` and the changelog together for a release.
- Commit messages wrap at about 72 columns; PR descriptions and release notes are not wrapped by hand.
