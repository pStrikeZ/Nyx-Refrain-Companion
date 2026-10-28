<p align="center">
  <img src="custom_components/nyxr_companion/brand/icon.png" width="160" alt="NyxR Companion">
</p>

<h1 align="center">NyxR Companion</h1>

<p align="center">Turn the machine running Home Assistant into a CD player, a Bluetooth receiver and an AirPlay relay for your AirPlay 2 speaker.</p>

<p align="center">English | <a href="README.zh-cn.md">简体中文</a></p>

NyxR Companion is a Home Assistant add-on and integration. The add-on plays audio CDs from an optical drive, receives Bluetooth audio from phones and music players (A2DP), and is an AirPlay receiver for iPhones, iPads and Macs; it streams any one of them to an AirPlay 2 speaker such as a HomePod through [nyxr](https://github.com/pStrikeZ/Nyx-Refrain), the command-line sender of Nyx Refrain. The integration controls it all from Home Assistant, like a headless CD player and Bluetooth speaker.

> Current version: 0.2.0, with nyxr from Nyx Refrain 0.9.5. Tested mainly with a HomePod mini on Home Assistant OS (amd64, in a Proxmox VM); reports from other setups are welcome.

> **LLM Disclaimer**
>
> All code in this project is 100% AI-generated. The maintainer may keep the project usable, but cannot guarantee code quality. The maintainer may also answer user questions with 100% copy-pasted replies; this is normal, please be aware.

---

## Features

- **CD player**: reads the disc directly, like a CD player does, and starts a track in about half a second. 16-bit 44.1 kHz audio reaches the speaker bit for bit. Track names and covers come from MusicBrainz, then CD-Text. Repeat all, repeat one and shuffle.
- **Bluetooth receiver**: phones and music players pair with it like with a speaker (no PIN), and it switches to Bluetooth by itself when one starts playing. Track info and play / pause / next / previous go over AVRCP.
- **Pairing from the receiver's side**: for devices whose own pairing request does not get through (old iPods with a weak radio), pick them from a list of nearby devices.
- **AirPlay relay**: shows up in the AirPlay list of iPhones, iPads and Macs, and passes what they play on to the speaker, with track info and cover. It switches to AirPlay by itself when a sender starts playing. On a HomePod this also keeps you clear of a tvOS 27 bug that stops its own Apple Music playback now and then (see [AirPlay](#airplay)).
- **One stream at a time**: the source selector switches between the CD, Bluetooth and AirPlay; the CD remembers where it was.
- **Controls on the speaker**: play / pause / next / previous on the HomePod or its Home Assistant card reach the CD player or the Bluetooth device.
- **Leaves the speaker's volume alone**: streams start at whatever volume the speaker is at.
- **Set up by itself**: the add-on announces itself to Home Assistant with its API token, and lists the AirPlay receivers it finds on the network.

## Requirements

- **Home Assistant OS** (or a Supervised install) on amd64 or aarch64 (e.g. a Raspberry Pi 4 / 5).
- **An AirPlay 2 receiver** that takes realtime audio, e.g. a HomePod or HomePod mini.
- For CDs: **an optical drive** that Home Assistant OS sees as `/dev/sr0` (USB, or SATA on a PC).
- For Bluetooth: **a Bluetooth adapter** (Classic / BR/EDR). Best kept for this alone; see [Bluetooth](#bluetooth).

## Installation

1. **Add-on**: in Home Assistant, go to **Settings → Apps → App store**, open the menu (⋮) → **Repositories**, and add `https://github.com/pStrikeZ/Nyx-Refrain-Companion`. Install **NyxR Companion** and start it.
2. **Integration**: in HACS, add `https://github.com/pStrikeZ/Nyx-Refrain-Companion` as a custom repository of type *Integration* and download **NyxR Companion**. Without HACS, copy `custom_components/nyxr_companion` into your configuration folder's `custom_components`. Restart Home Assistant.
3. **Set up**: Home Assistant shows NyxR Companion under **Settings → Devices & services → Discovered**; click **Add**. The add-on fills in the address and token.
4. **Pick the speaker**: in the device's configuration, choose it under **AirPlay receiver** (receivers found on the network), or type its `IP` / `IP:PORT` in **Target receiver**.

Put a CD in and turn on **Stream**; turn on **Bluetooth pairing** and pair your phone with *NyxR Companion*; or pick *NyxR Companion* in an iPhone's AirPlay list.

## Entities

| Entity | What it does |
|---|---|
| Stream (switch) | Connects to the speaker and plays the selected source; off stops and remembers the CD position |
| Source (select) | `cd`, `bluetooth` or `airplay`; only one streams at a time |
| Play mode (select) | Repeat all, repeat one, shuffle (CD) |
| Eject (button) | Stops and ejects the disc |
| Status (sensor) | Drive and playback state; attributes carry the disc, track and errors |
| Bluetooth pairing (switch) | Opens pairing for 3 minutes: phones can pair, and nearby devices are listed. Closes when something pairs |
| Pair with nearby device (select) | Devices found while pairing is open; picking one pairs with it from this side |
| Bluetooth device (sensor) | The connected device; attributes list paired devices, codec, sample rate and track |
| Disconnect Bluetooth device (button) | Disconnects the current device; it stays paired |
| Forget paired device (select) | Removes the pairing with the device you pick |
| AirPlay sender (sensor) | The connected AirPlay sender; attributes carry its track |

Configuration entities: AirPlay receiver, Target receiver, Latency profile (`low` ≈ 100 ms / `balanced` ≈ 140 ms / `stable` ≈ 240 ms end to end), Read mode, Read speed, Read-ahead buffer, Bluetooth name, Switch to Bluetooth automatically, Bluetooth buffer, AirPlay name, Switch to AirPlay automatically.

Play, pause and track changes have no entities of their own: use the speaker's media player, whose controls reach NyxR Companion.

## Bluetooth

- **How it fits together**: on Home Assistant OS, BlueZ runs every Bluetooth adapter, and the built-in sound server (PulseAudio) receives A2DP audio on all of them. The add-on works with these two directly: it pairs devices through BlueZ and records their audio from the sound server. Home Assistant's own Bluetooth integration is a separate thing, which only uses adapters for Bluetooth Low Energy; the add-on does not need it. Nothing on the host needs changing.
- **Home Assistant's Bluetooth integration** offers to use the adapter too. Choose **Ignore**: BlueZ is a poor fit for Home Assistant's BLE devices (ESPHome Bluetooth proxies do that job better), and BLE scanning would share the radio with your music. If you use it anyway, the add-on leaves its BLE pairings alone: only devices that can send audio count as the add-on's.
- **Volume**: the phone's volume and the speaker's add up. Phones that send their volume to the receiver (most Android phones) lower the recorded audio by it; others lower it themselves. Turn the phone up and set the volume on the speaker.
- **A device that removed the pairing on its side**: Home Assistant OS turns down a new pairing from a device it still knows. Open **Bluetooth pairing** and pair from the device: the first attempt fails, the add-on sees the device connect and drop without its services and forgets the old pairing, and the second attempt works.
- Range: Bluetooth audio needs a good signal. A USB extension cable that brings the adapter out of a metal case helps a lot.

## AirPlay

- **How it fits together**: the add-on runs [shairport-sync](https://github.com/mikebrady/shairport-sync) (its official image is the add-on's base) with its own mDNS responder (avahi) and PTP clock (nqptp). What a sender plays arrives as buffered AirPlay 2, is decoded, and goes on to the speaker through nyxr as a realtime stream, like the CD. The stream to the speaker opens when a sender starts playing and ends 10 s after it stops or leaves.
- **Why on a HomePod**: since tvOS 27, a HomePod playing Apple Music by itself now and then stops around a track change and loses its queue (a deadlock in its audio daemon, `audiomxd`, which then restarts). Played from the iPhone to NyxR Companion, the music reaches the HomePod as one uninterrupted realtime stream instead, which this has never cut.
- **Quality**: iPhones send AAC to receivers that are not Apple's own, so AirPlay through the add-on is not lossless.
- **Volume and controls**: the phone's volume slider is ignored (full-scale audio is passed on); set the volume on the speaker. Buffered AirPlay 2 gives a receiver no way back to the sender, so pause and track changes stay on the phone; the speaker's own controls do nothing for AirPlay.
- **Network**: the add-on answers mDNS as `nyxr-companion.local` beside Home Assistant's own mDNS, and needs UDP ports 319 and 320 for nqptp: another AirPlay 2 receiver on the same host (a second shairport-sync, say) cannot run alongside it.

## CD

- Read modes: **Raw** (default) reads sectors directly; **Raw + alignment** lines up each block on the previous one for drives whose reads are not sample-accurate; **cd-paranoia (overlap / full)** verifies every read for scratched discs, but takes 3–7 s to start after every jump.
- The drive is capped at 4x by default (**Read speed**): quieter, and some slim drives drop off the SATA link when they spin up to full speed.
- Mixed-mode and CD-Extra discs play their audio tracks; data discs and DVDs are refused.

## Running Home Assistant OS in a virtual machine

Pass the Bluetooth adapter through as a USB device. The optical drive has to be passed through as a raw SCSI device, so that the VM can read audio tracks: an emulated CD-ROM drive cannot. In Proxmox VE, find the drive's SCSI generic device (`ls /sys/block/sr0/device/scsi_generic/`, e.g. `sg2`) and add to the VM's configuration (`/etc/pve/qemu-server/<vmid>.conf`):

```
args: -device virtio-scsi-pci,id=cdscsi -blockdev driver=host_device,filename=/dev/sg2,node-name=cdpt -device scsi-generic,bus=cdscsi.0,drive=cdpt
```

The web interface does not show `args`. The VM needs a full stop and start for it to apply.

## Known limitations

- One AirPlay receiver per stream; no multi-room.
- Bluetooth: SBC (and whatever else the host's sound server supports); no seeking; AVRCP track info only from players that provide it.
- AirPlay: AAC from iPhones; no control of the sender from the speaker; the AirPlay latency (about 2 s) comes on top of the stream's.
- The HTTP API (see [API.md](API.md)) listens on `127.0.0.1` and wants the token; it is meant for the integration.

## Development

```sh
python3 -m unittest discover -s tests
```

The tests run the integration against a fake add-on (`tests/fake_server.py`, which follows [API.md](API.md)) with stand-ins for the Home Assistant modules; `tests/test_airplay.py` runs the add-on's AirPlay source with a fake shairport-sync and nyxr (it needs `aiohttp` and `dbus-next`). To try changes to the add-on, copy `nyxr_companion/` into the host's `/mnt/data/supervisor/apps/local/`, reload the app store and install it; the image takes nyxr from the Nyx Refrain release (`NYXR_VERSION` in the Dockerfile). Raise `version` in `config.yaml` when its permissions change: Supervisor applies them on an update only.

---

## License

[MIT](LICENSE) © 2026 pStrikeZ and contributors

The icon is the Nyx Refrain icon, from [Microsoft Fluent Emoji](https://github.com/microsoft/fluentui-emoji) (MIT); see [NOTICE](NOTICE) and [LICENSE-fluentui-emoji](LICENSE-fluentui-emoji).
