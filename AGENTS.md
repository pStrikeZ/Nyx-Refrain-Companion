# AGENTS.md — NyxR Companion

Notes for coding agents (and people) working in this repository. `CLAUDE.md` is a symlink to this file.

## Layout

- `nyxr_companion/`: the Home Assistant add-on. One Python daemon (`nyxr_companion.py`) runs the CD player, the Bluetooth receiver (BlueZ + the host's PulseAudio) and the AirPlay receiver (shairport-sync, avahi, nqptp), and feeds one `nyxr` at a time. The image is built from shairport-sync's official image (Alpine), pinned by digest; `nyxr` comes from the Nyx Refrain release (`NYXR_VERSION`), checked against its `SHA256SUMS`.
- `custom_components/nyxr_companion/`: the integration (HACS), talking to the daemon's HTTP + WebSocket API (`API.md`, `API.zh-cn.md`).
- `tests/`: the integration against a fake add-on (`fake_server.py`, which follows `API.md`), and the daemon's AirPlay source against a fake shairport-sync and nyxr (`test_airplay.py`).

## Checks

- `python3 -m unittest discover -s tests`. `test_airplay.py` needs the daemon's dependencies (`aiohttp`, `dbus-next`) and skips without them; it also runs in the add-on image: `docker build -t nyxr-companion-test nyxr_companion && docker run --rm -v $PWD:/src:ro -w /src --entrypoint python3 nyxr-companion-test -m unittest tests.test_airplay`.
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

## Conventions

- The add-on never changes the speaker's volume (`nyxr --keep-volume`): Home Assistant users already have the speaker's own volume control.
- Settings a user may want to change are entities in Home Assistant, kept by the add-on in `/data/settings.json`, rather than add-on options or files.
- Raise `version` in `config.yaml`, `manifest.json` and the changelog together for a release.
- Commit messages wrap at about 72 columns; PR descriptions and release notes are not wrapped by hand.
