"""Tests for ddc.py: the parts that don't need a real monitor.

Run from the project root:  python3 -m unittest discover -s tests -v
"""

import os
import sys
import tempfile
import threading
import time
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

# Keep the tests away from your real settings: ddc reads these when imported.
_home = tempfile.mkdtemp(prefix="dimly-test-")
os.environ["XDG_CONFIG_HOME"] = os.path.join(_home, "config")
os.environ["XDG_CACHE_HOME"] = os.path.join(_home, "cache")
os.environ["XDG_STATE_HOME"] = os.path.join(_home, "state")
os.environ["XDG_RUNTIME_DIR"] = os.path.join(_home, "run")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import ddc  # noqa: E402

DETECT_OUTPUT = """Display 1
   I2C bus:  /dev/i2c-5
   Monitor:             GSM:LG FULL HD:

Invalid display
   I2C bus:  /dev/i2c-8
   Monitor:             BOE::

Display 2
   I2C bus:  /dev/i2c-7
   Monitor:             DEL::
"""


def ddcutil_result(stdout, returncode=0):
    return SimpleNamespace(stdout=stdout, returncode=returncode)


class FreshFiles(unittest.TestCase):
    def setUp(self):
        for path in (ddc.CONFIG_FILE, ddc.STATE_FILE, ddc.OLD_STATE_FILE):
            path.unlink(missing_ok=True)


class ParseTimeTest(unittest.TestCase):
    def test_valid(self):
        self.assertEqual(ddc.parse_time("8:00"), "08:00")
        self.assertEqual(ddc.parse_time(" 23:59 "), "23:59")
        self.assertEqual(ddc.parse_time("00:00"), "00:00")

    def test_invalid(self):
        for text in ("24:00", "12:60", "7:5", "noon", "", "12"):
            with self.subTest(text=text):
                self.assertIsNone(ddc.parse_time(text))


class CurrentSlotTest(unittest.TestCase):
    schedule = [["10:00", "Day"], ["19:00", "Evening"], ["21:00", "Night"]]

    def slot(self, when, schedule=None):
        return ddc.current_slot(schedule or self.schedule, datetime.fromisoformat(when))

    def test_during_the_day(self):
        self.assertEqual(self.slot("2026-11-09 14:30"), ("2026-11-09 10:00", "Day"))

    def test_exactly_at_a_switch(self):
        self.assertEqual(self.slot("2026-11-09 19:00"), ("2026-11-09 19:00", "Evening"))

    def test_before_the_first_switch_uses_yesterdays_last(self):
        self.assertEqual(self.slot("2026-11-10 07:00"), ("2026-11-09 21:00", "Night"))

    def test_order_in_the_schedule_does_not_matter(self):
        shuffled = [self.schedule[2], self.schedule[0], self.schedule[1]]
        self.assertEqual(self.slot("2026-11-09 20:00", shuffled), ("2026-11-09 19:00", "Evening"))

    def test_bad_entries_are_skipped(self):
        schedule = [["bad", "Day"], ["09:00", "NoSuchPreset"], ["12:00", "Day"]]
        self.assertEqual(self.slot("2026-11-09 13:00", schedule), ("2026-11-09 12:00", "Day"))
        self.assertIsNone(self.slot("2026-11-09 13:00", [["bad", "Day"]]))


class DdcutilOutputTest(FreshFiles):
    def test_detect_keeps_only_monitors_with_ddc(self):
        with mock.patch.object(ddc, "ddcutil", return_value=ddcutil_result(DETECT_OUTPUT)):
            displays = ddc.detect_displays()
        self.assertEqual(displays, [(5, "LG FULL HD"), (7, "Monitor on bus 7")])
        self.assertEqual(ddc.load_state()["buses"], [5, 7])

    def test_read_vcp(self):
        with mock.patch.object(ddc, "ddcutil", return_value=ddcutil_result("VCP 10 C 35 100\n")):
            self.assertEqual(ddc.read_vcp(5, ddc.BRIGHTNESS), (35, 100))
        with mock.patch.object(ddc, "ddcutil", return_value=ddcutil_result("VCP 10 ERR\n", 1)):
            self.assertIsNone(ddc.read_vcp(5, ddc.BRIGHTNESS))

    def test_level_scales_to_the_monitors_maximum(self):
        self.assertEqual(ddc.level(35)(0, 100), 35)
        self.assertEqual(ddc.level(50)(0, 255), 128)


class SetBrightnessTest(FreshFiles):
    def test_uses_known_monitors_and_records_levels(self):
        ddc.update_state(buses=[5])
        writes = []
        with mock.patch.object(ddc, "read_vcp", return_value=(20, 100)), \
                mock.patch.object(ddc, "write_vcp", side_effect=lambda bus, code, v: writes.append((bus, v)) or True), \
                mock.patch.object(ddc, "detect_displays") as detect:
            results = ddc.set_brightness(lambda current, _: current + 10, sync_laptop=False)
        self.assertEqual(results, [(30, 100)])
        self.assertEqual(writes, [(5, 30)])
        detect.assert_not_called()
        self.assertEqual(ddc.load_state()["brightness"], {"5": 30})

    def test_clamps_to_the_monitors_range(self):
        ddc.update_state(buses=[5])
        with mock.patch.object(ddc, "read_vcp", return_value=(95, 100)), \
                mock.patch.object(ddc, "write_vcp", return_value=True):
            self.assertEqual(ddc.set_brightness(lambda c, _: c + 10, sync_laptop=False), [(100, 100)])

    def test_searches_again_when_no_known_monitor_answers(self):
        ddc.update_state(buses=[3])
        with mock.patch.object(ddc, "read_vcp", side_effect=lambda bus, code: (20, 100) if bus == 5 else None), \
                mock.patch.object(ddc, "write_vcp", return_value=True), \
                mock.patch.object(ddc, "detect_displays", return_value=[(5, "LG")]) as detect:
            self.assertEqual(ddc.set_brightness(ddc.level(40), sync_laptop=False), [(40, 100)])
        detect.assert_called_once()


class WriterTest(FreshFiles):
    def test_sends_only_the_newest_value_while_busy(self):
        written = []

        def slow_write(bus, code, value):
            time.sleep(0.05)
            written.append(value)
            return True

        with mock.patch.object(ddc, "write_vcp", side_effect=slow_write):
            writer = ddc.Writer(5)
            for value in range(10, 60, 5):
                writer.request(ddc.BRIGHTNESS, value)
            deadline = time.monotonic() + 2
            while ddc.load_state().get("brightness") != {"5": 55} and time.monotonic() < deadline:
                time.sleep(0.01)
        self.assertEqual(written[-1], 55)
        self.assertLess(len(written), 10)
        self.assertEqual(ddc.load_state()["brightness"], {"5": 55})

    def test_reports_a_value_the_monitor_keeps_refusing(self):
        refused = threading.Event()
        with mock.patch.object(ddc, "write_vcp", return_value=False), mock.patch.object(ddc.time, "sleep"):
            writer = ddc.Writer(5, on_error=lambda code, value: refused.set())
            writer.request(ddc.CONTRAST, 50)
            self.assertTrue(refused.wait(2))


class NextSwitchTest(unittest.TestCase):
    schedule = [["10:00", "Day"], ["19:00", "Evening"], ["21:00", "Night"]]

    def wait(self, when, schedule=None):
        return ddc.seconds_until_next_switch(schedule or self.schedule, datetime.fromisoformat(when))

    def test_later_today(self):
        self.assertEqual(self.wait("2026-11-09 18:30:00"), 30 * 60)

    def test_after_the_last_switch_waits_for_tomorrow(self):
        self.assertEqual(self.wait("2026-11-09 22:00:00"), 12 * 3600)

    def test_exactly_at_a_switch_waits_for_the_next(self):
        self.assertEqual(self.wait("2026-11-09 19:00:00"), 2 * 3600)

    def test_no_valid_times(self):
        self.assertIsNone(self.wait("2026-11-09 12:00:00", [["nope", "Day"]]))


class LockTest(unittest.TestCase):
    def test_two_threads_never_hold_it_together(self):
        inside = []
        overlaps = []

        def work():
            for _ in range(20):
                with ddc.monitor_lock():
                    inside.append(1)
                    if len(inside) > 1:
                        overlaps.append(True)
                    with ddc.monitor_lock():  # re-entrant within a thread
                        time.sleep(0.001)
                    inside.pop()

        threads = [threading.Thread(target=work) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(overlaps, [])


class StateTest(FreshFiles):
    def test_changes_made_at_the_same_moment_all_survive(self):
        def record(bus):
            for value in range(10):
                ddc.record_levels(ddc.BRIGHTNESS, {bus: value})

        threads = [threading.Thread(target=record, args=(bus,)) for bus in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(ddc.load_state()["brightness"], {str(bus): 9 for bus in range(8)})
        leftovers = [f for f in ddc.STATE_DIR.iterdir() if f.name.endswith(".tmp")]
        self.assertEqual(leftovers, [])

    def test_maxima_are_kept_per_feature(self):
        ddc.record_levels(ddc.BRIGHTNESS, {5: 40}, {5: 255})
        ddc.record_levels(ddc.CONTRAST, {5: 50}, {5: 100})
        state = ddc.load_state()
        self.assertEqual(ddc.recorded_maximum(state, 5, ddc.BRIGHTNESS), 255)
        self.assertEqual(ddc.recorded_maximum(state, 5, ddc.CONTRAST), 100)
        self.assertEqual(ddc.recorded_maximum(state, 9, ddc.BRIGHTNESS), 100)

    def test_moves_from_the_old_cache_location(self):
        ddc.OLD_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        ddc.OLD_STATE_FILE.write_text('{"setup_version": 2, "applied_slot": "2026-11-09 10:00"}')
        self.assertEqual(ddc.load_state()["setup_version"], 2)
        ddc.update_state(buses=[5])
        self.assertFalse(ddc.OLD_STATE_FILE.exists())
        self.assertEqual(ddc.load_state()["applied_slot"], "2026-11-09 10:00")

    def test_percent_conversions(self):
        self.assertEqual(ddc.to_percent(128, 255), 50)
        self.assertEqual(ddc.from_percent(50, 255), 128)
        self.assertEqual(ddc.to_percent(30, 0), 30)


class SettingsTest(FreshFiles):
    def test_defaults_when_there_is_no_file(self):
        self.assertEqual(ddc.load_config(), ddc.DEFAULT_CONFIG)

    def test_presets_fill_in_missing_names(self):
        ddc.update_config(presets={"Day": 35})
        self.assertEqual(ddc.presets()["Day"], 35)
        self.assertEqual(ddc.presets()["Night"], ddc.DEFAULT_CONFIG["presets"]["Night"])

    def test_a_broken_file_falls_back_to_defaults(self):
        ddc.CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
        ddc.CONFIG_FILE.write_text("{ not json")
        self.assertEqual(ddc.load_config(), ddc.DEFAULT_CONFIG)

    def test_wrong_shapes_fall_back_to_defaults(self):
        ddc.CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
        ddc.CONFIG_FILE.write_text('{"step": "ten", "sync_laptop": 1, "presets": {"Day": "bright", "Night": 5}, '
                                   '"schedule": [["09:00", "Day"], "oops", ["10:00"]]}')
        config = ddc.load_config()
        self.assertEqual(config["step"], ddc.DEFAULT_CONFIG["step"])
        self.assertEqual(config["sync_laptop"], ddc.DEFAULT_CONFIG["sync_laptop"])
        self.assertEqual(config["presets"], {"Night": 5})
        self.assertEqual(config["schedule"], [["09:00", "Day"]])
        ddc.CONFIG_FILE.write_text("[1, 2, 3]")
        self.assertEqual(ddc.load_config(), ddc.DEFAULT_CONFIG)

    def test_defaults_are_not_shared_between_loads(self):
        ddc.load_config()["schedule"].append(["01:00", "Max"])
        self.assertEqual(len(ddc.load_config()["schedule"]), len(ddc.DEFAULT_CONFIG["schedule"]))


if __name__ == "__main__":
    unittest.main()
