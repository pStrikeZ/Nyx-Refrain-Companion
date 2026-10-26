# NyxR Companion HTTP API

English | [简体中文](API.zh-cn.md)

The add-on's daemon listens on `127.0.0.1:8765` by default (add-on option `api_listen`, or the `NYXR_COMPANION_LISTEN` environment variable). All requests and responses are JSON (UTF-8).

## Authentication

Every request, the WebSocket included, needs `Authorization: Bearer <token>`; without it the answer is `401`. The token is made at random on first start and kept in `/data/api_token` (or set with `NYXR_COMPANION_TOKEN`). As a Home Assistant add-on, the daemon tells Home Assistant its `host`, `port` and `token` through Supervisor discovery (service `nyxr_companion`) when it starts; the integration sets itself up from that, and an existing entry for the same address only takes the new token.

## State object

```json
{
  "source": "cd",
  "drive": "ok",
  "disc": {
    "id": "MusicBrainz disc ID",
    "album": "Album",
    "artist": "Album artist",
    "cover_url": "https://coverartarchive.org/release/<mbid>/front-500",
    "tracks": [
      {"number": 1, "title": "Title", "artist": "Artist", "length": 223.4}
    ]
  },
  "streaming": true,
  "playback": "playing",
  "track": 3,
  "position": 12.5,
  "mode": "repeat_all",
  "error": null,
  "bluetooth": {
    "available": true,
    "name": "NyxR Companion",
    "pairing": false,
    "scanning": false,
    "devices": [{"address": "00:11:22:33:44:55", "name": "iPod", "connected": true}],
    "nearby": [],
    "connected": "iPod",
    "playing": true,
    "codec": "SBC",
    "rate": 44100,
    "streaming": true,
    "playback": "playing",
    "track": {"number": 1, "title": "Title", "artist": "Artist", "album": "Album", "length": 223.4},
    "error": null
  },
  "receivers": [
    {"name": "Living Room", "address": "192.0.2.10:7000", "model": "AudioAccessory5,1"}
  ],
  "settings": {
    "read_mode": "raw",
    "read_speed": 4,
    "buffer_seconds": 10.0,
    "latency_profile": "balanced",
    "target": "192.0.2.10:7000",
    "bluetooth_name": "NyxR Companion",
    "bluetooth_auto_switch": true,
    "bluetooth_buffer_ms": 150
  }
}
```

| Field | Value |
|---|---|
| `source` | What is streamed: `cd` (default) or `bluetooth`. One at a time; `streaming`, `playback` and `error` are the selected source's |
| `drive` | `ok` (a disc is in), `no_disc`, `tray_open`, `not_ready` (reading the disc), `not_audio` (no audio tracks: a data disc, a DVD, or a table of contents that could not be read 3 times in a row; playing is refused), `missing` (no drive, e.g. unplugged) |
| `disc` | Not `null` only while `drive == "ok"` and the table of contents has been read. Audio tracks only: the data tracks of mixed-mode and CD-Extra discs are neither listed nor played, and `number` counts from 1. Metadata comes from MusicBrainz, else the disc's CD-Text (no cover); without either, `album` / `artist` / `cover_url` are `null` and titles are `Track01`, `Track02`… |
| `streaming` | Whether nyxr is connected to the receiver |
| `playback` | `stopped`, `playing`, `paused` |
| `track` | Current track number (from 1); `null` when stopped with no track selected |
| `position` | Seconds into the current track |
| `mode` | `repeat_all` (default; after the last track comes the first), `repeat_one`, `shuffle` (all tracks in random order, reshuffled at the end) |
| `error` | What went wrong last, or `null` |
| `receivers` | AirPlay receivers found on the network (`nyxr discover`, at start and every 5 minutes): `{"name", "address", "model"}`, `model` is `null` when unknown |
| `bluetooth` | The Bluetooth receiver, see below. `available` is `false` when the host has no D-Bus, BlueZ or adapter to use |
| `settings` | Settings, see below |

### Settings

Kept in `/data/settings.json` (the add-on's data folder) and changed with `POST /api/settings`.

| Name | Value | Takes effect |
|---|---|---|
| `read_mode` | `raw` (default: sectors read directly), `raw_aligned` (each block lined up on the previous one, for drives whose reads are not sample-accurate), `paranoia_overlap`, `paranoia_full` (verified by cd-paranoia; 3–7 s to start after every jump) | At once: the current position is read again the new way |
| `read_speed` | Whole number from 1 to 24 (times CD speed). Default 4: some slim drives drop off the SATA link when they spin up to full speed | At once |
| `buffer_seconds` | 2–60, audio read ahead | At once |
| `latency_profile` | `low`, `balanced`, `stable` | When the stream next starts |
| `target` | The receiver's `IP` or `IP:PORT`; `null` at first, and nothing streams until it is set | When the stream next starts |
| `bluetooth_name` | 1–64 characters, the name phones see; default `NyxR Companion` | At once |
| `bluetooth_auto_switch` | `true` (default) / `false`: switch to Bluetooth and stream when a connected device starts playing | At once |
| `bluetooth_buffer_ms` | Whole number from 20 to 2000, jitter buffer for Bluetooth audio in milliseconds; default 150 | When the Bluetooth stream next starts |

### Bluetooth

| Field | Value |
|---|---|
| `pairing` | Whether pairing is open (discoverable, accepting new devices, and looking for nearby devices). It closes after 3 minutes, or once a device has paired |
| `scanning` | Looking for nearby devices while pairing is open |
| `nearby` | Unpaired devices found that can play music (computers, phones, audio devices; Classic Bluetooth, with a name): `{"address", "name"}`. Empty unless pairing is open |
| `devices` | Paired devices that can send audio (A2DP source); other pairings on the adapter, such as Home Assistant's BLE devices, are not listed or touched |
| `connected` | Name of the connected device, or `null` |
| `playing` | The device is sending audio (its BlueZ A2DP transport is `active`) |
| `codec`, `rate` | Codec and sample rate of the connection (e.g. `SBC`, `44100`); `null` without one |
| `streaming` | Whether the Bluetooth stream's nyxr is connected to the receiver |
| `playback` | The device's AVRCP playback state; guessed from whether it sends audio when it does not say |
| `track` | AVRCP track info; without a title (or with Android's "Not Provided"), `title` is the device name |

## Endpoints

| Method | Path | Body | What it does |
|---|---|---|---|
| GET | `/api/state` | — | Returns the state object |
| GET | `/api/ws` | — | WebSocket. Sends the state object at once, then the whole state object on every change (text frames, JSON); while playing, every 5 s for `position`. Clients need not send anything |
| POST | `/api/stream` | `{"on": true}` | On: connect to the receiver and play (the current track, else the first). Off: stop and disconnect, keeping the track and position to resume from (ejecting starts over). `409` when turned on with no disc, or before `target` is set |
| POST | `/api/mode` | `{"mode": "shuffle"}` | Sets the play mode |
| POST | `/api/eject` | `{}` | Stops the stream and ejects the disc |
| POST | `/api/play` | `{"track": 3}` | Plays a track (turning the stream on); without `track`, resumes |
| POST | `/api/pause` | `{}` | Pauses (the stream stays connected) |
| POST | `/api/next` | `{}` | Next track |
| POST | `/api/previous` | `{}` | Previous track (back to the start of this one after 3 s) |
| POST | `/api/settings` | `{"read_speed": 8, ...}` | Changes one or more settings; an unknown name or a bad value gives `400` and changes nothing |
| POST | `/api/receivers/refresh` | `{}` | Looks for AirPlay receivers now (about 3 s); returns the state object |
| POST | `/api/source` | `{"source": "bluetooth"}` | Switches the source. The other one stops (the CD keeps its track and position); switching to Bluetooth while the device is sending audio starts the stream |
| POST | `/api/bluetooth/pairing` | `{"on": true}` | Opens / closes pairing (Just Works, no PIN; paired devices are trusted) |
| POST | `/api/bluetooth/pair` | `{"address": "AA:BB:CC:DD:EE:FF"}` | Pairs with a device in `nearby` from this side (for devices whose own pairing request fails, like an iPod with a weak radio); it is then trusted, connected if it can be, and pairing closes. `400` for an address not in `nearby` |
| POST | `/api/bluetooth/disconnect` | `{}` | Disconnects the connected device (it stays paired) |
| POST | `/api/bluetooth/forget` | `{"address": "AA:BB:CC:DD:EE:FF"}` | Removes the pairing with that device; without `address`, with all of `devices`. `400` for an address not in `devices`. A device that removed the pairing on its side needs none of this: while pairing is open, a paired device that connects and drops within 10 s without its services resolving is forgotten by itself, and can then pair again |

`/api/stream`, `/api/play`, `/api/pause`, `/api/next` and `/api/previous` act on the selected source. For Bluetooth: turning the stream on needs a connected device (else `409`); play / pause / next / previous go to the device over AVRCP, and `track` is ignored. `/api/bluetooth/*` gives `409` while Bluetooth is not available.

A successful POST returns `200` and the new state object; a bad request `400`, a request the current state does not allow (no disc, say) `409`, a missing or wrong token `401`, each with `{"error": "what went wrong"}`.
