# Changelog

## 0.1.0

First release.

- CD player: reads the disc directly, MusicBrainz and CD-Text metadata, repeat and shuffle.
- Bluetooth receiver (A2DP) through the host's BlueZ and sound server, with pairing from either side; a device that removed the pairing on its side can pair again by itself.
- One stream at a time to an AirPlay 2 receiver through nyxr (Nyx Refrain 0.9.5), without changing its volume.
- API token and Supervisor discovery, so the integration sets itself up; AirPlay receivers found on the network to pick from.
