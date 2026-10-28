"""The daemon's drive monitor: a disc put in plays at once, one already in the drive when
the daemon starts does not."""
import asyncio
import importlib.util
import os
import tempfile
import unittest
from unittest import mock

STATE = tempfile.mkdtemp()
os.environ["NYXR_COMPANION_STATE"] = STATE
os.environ["NYXR_COMPANION_RUN"] = STATE

try:
    spec = importlib.util.spec_from_file_location(
        "nyxr_companion",
        os.path.join(os.path.dirname(__file__), "..", "nyxr_companion", "nyxr_companion.py"),
    )
    daemon = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(daemon)
except ImportError:  # the daemon's own dependencies (dbus-next) are not installed
    daemon = None


class Done(Exception):
    pass


class FakePlayer:
    def __init__(self):
        self.playback = "stopped"
        self.last_read = 0.0
        self.loaded = []
        self.auto_played = 0

    def set_drive(self, status):
        pass

    def disc_loaded(self, disc_id, lengths, cd_tracks, meta):
        self.loaded.append(disc_id)

    def auto_play(self):
        self.auto_played += 1


@unittest.skipIf(daemon is None, "daemon dependencies missing")
class CdInsertTest(unittest.TestCase):
    def run_drive(self, statuses):
        player = FakePlayer()
        statuses = iter(statuses)

        def drive_status():
            try:
                return next(statuses)
            except StopIteration:
                raise Done from None

        real_sleep = asyncio.sleep
        with (
            mock.patch.object(daemon, "drive_status", drive_status),
            mock.patch.object(daemon, "limit_speed", lambda: None),
            mock.patch.object(daemon, "spin_down", lambda: None),
            mock.patch.object(daemon, "read_toc", lambda: ("disc-1", [7500], [1])),
            mock.patch.object(daemon, "read_cdtext", lambda tracks: None),
            mock.patch.object(daemon, "lookup_metadata", lambda disc_id, count: None),
            mock.patch.object(daemon.asyncio, "sleep", lambda s: real_sleep(0)),
        ):

            async def run():
                with self.assertRaises(Done):
                    await daemon.monitor_drive(player)
                await real_sleep(0.05)  # let the executor call auto_play

            asyncio.run(run())
        return player

    def test_disc_in_at_start_does_not_play(self):
        daemon.SETTINGS.values["cd_auto_play"] = True
        player = self.run_drive(["ok", "ok", "ok"])
        self.assertEqual(player.loaded, ["disc-1"])
        self.assertEqual(player.auto_played, 0)

    def test_inserted_disc_plays(self):
        daemon.SETTINGS.values["cd_auto_play"] = True
        player = self.run_drive(["no_disc", "tray_open", "not_ready", "ok", "ok"])
        self.assertEqual(player.auto_played, 1)
        # Ejected and put in again: plays again.
        player = self.run_drive(["ok", "tray_open", "not_ready", "ok", "ok"])
        self.assertEqual(player.auto_played, 1)

    def test_setting_off(self):
        daemon.SETTINGS.values["cd_auto_play"] = False
        try:
            player = self.run_drive(["no_disc", "ok"])
        finally:
            daemon.SETTINGS.values["cd_auto_play"] = True
        self.assertEqual(player.auto_played, 0)


if __name__ == "__main__":
    unittest.main()
