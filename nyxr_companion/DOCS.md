# NyxR Companion

The add-on plays audio CDs, receives Bluetooth audio (A2DP) and is an AirPlay receiver for iPhones, iPads and Macs, and streams them to an AirPlay 2 speaker through nyxr. You control it with the **NyxR Companion integration** (install it with HACS from the [project page](https://github.com/pStrikeZ/Nyx-Refrain-Companion)).

## Getting started

1. Start the add-on.
2. Install the NyxR Companion integration and restart Home Assistant.
3. Under **Settings → Devices & services**, NyxR Companion shows up as discovered: add it. The add-on gives it its address and API token.
4. In the device's configuration, pick your speaker under **AirPlay receiver** (or type its address in **Target receiver**).

Then put a CD in and turn on **Stream**, turn on **Bluetooth pairing** and pair your phone, or pick *NyxR Companion* in an iPhone's AirPlay list.

## Options

| Option | Default | |
|---|---|---|
| `cd_device` | `/dev/sr0` | The optical drive. Another device also has to be listed under `devices` in the add-on's configuration. |
| `bluetooth_adapter` | (empty) | The adapter to use, e.g. `hci1`. Empty uses the first one. |
| `api_listen` | `127.0.0.1:8765` | Where the HTTP API listens. Home Assistant reaches it on `127.0.0.1`; other addresses expose it to your network (it still needs the token). |
| `log_level` | `info` | `debug`, `info`, `warning` or `error`. |

Everything else (receiver, read speed, latency profile, Bluetooth and AirPlay names...) is set from the integration's entities and kept by the add-on.

## Bluetooth

- The phone's volume and the speaker's add up: turn the phone up and set the volume on the speaker.
- A phone that removed the pairing on its side: turn on **Bluetooth pairing** and pair from the phone twice. The first attempt fails, the add-on forgets the old pairing, the second works. To drop a device for good, use **Forget paired device**.
- If a device's own pairing request fails (an old iPod, say), turn on **Bluetooth pairing**, keep the device in its Bluetooth settings, and pick it under **Pair with nearby device**.
- Home Assistant's Bluetooth integration offers to use the same adapter: choose **Ignore**. The add-on does not need it (it works with BlueZ and the sound server directly), and BLE belongs on ESPHome Bluetooth proxies rather than BlueZ.

## AirPlay

- The phone's volume slider is ignored: set the volume on the speaker. Pause and track changes stay on the phone.
- iPhones send AAC to this receiver, so AirPlay is not lossless.
- The add-on needs UDP ports 319 and 320 (PTP): no other AirPlay 2 receiver can run on the same host.

## Troubleshooting

- **No sound, `nyxr exited`**: check that the receiver's address is right and that it is on the same network. The add-on uses the host network.
- **Bluetooth audio drops out**: raise **Bluetooth buffer** (e.g. 300 ms), or bring the adapter closer, out of the case on a USB extension cable.
- **The drive is `missing` in a virtual machine**: the drive has to be passed through as a raw SCSI device; see the project page.
