# Changelog

## 0.2.1

- CD: putting a disc in plays it from the first track, like a CD player, switching from Bluetooth or AirPlay if they are playing. A disc already in the drive when the add-on starts is not played. New setting *Play inserted discs automatically* (on by default).

## 0.2.0

- AirPlay receiver: iPhones, iPads and Macs can play to NyxR Companion, and it passes their audio on to the speaker, with track info and cover. It switches to AirPlay when a sender starts playing (can be turned off), and its name in the AirPlay list can be changed.
- The add-on is now built on shairport-sync's official image (Alpine) instead of Debian.
- CD: the disc stops spinning after a minute without playing; it spins up again on its own (about 3 s).

## 0.1.0

First release.

- CD player: reads the disc directly, MusicBrainz and CD-Text metadata, repeat and shuffle.
- Bluetooth receiver (A2DP) through the host's BlueZ and sound server, with pairing from either side; a device that removed the pairing on its side can pair again by itself.
- One stream at a time to an AirPlay 2 receiver through nyxr (Nyx Refrain 0.9.5), without changing its volume.
- API token and Supervisor discovery, so the integration sets itself up; AirPlay receivers found on the network to pick from.
