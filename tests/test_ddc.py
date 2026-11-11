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
        ddc.update_config(presets={"Day": {"brightness": 35, "contrast": 35, "warmth": 0}})
        self.assertEqual(ddc.presets()["Day"], {"brightness": 35, "contrast": 35, "warmth": 0})
        self.assertEqual(ddc.presets()["Night"], ddc.DEFAULT_CONFIG["presets"]["Night"])

    def test_presets_from_before_0_5_keep_their_brightness(self):
        ddc.update_config(presets={"Night": 10, "Evening": 25, "Day": 35, "Max": 100})
        presets = ddc.presets()
        for name, brightness in (("Night", 10), ("Evening", 25), ("Day", 35), ("Max", 100)):
            self.assertEqual(presets[name], {**ddc.DEFAULT_CONFIG["presets"][name], "brightness": brightness})

    def test_a_preset_can_keep_contrast_and_warmth_but_not_brightness(self):
        ddc.update_config(presets={"Evening": {"brightness": None, "contrast": None, "warmth": None},
                                   "Day": {"brightness": 150, "contrast": -3, "warmth": "warm", "extra": 1}})
        presets = ddc.presets()
        self.assertEqual(presets["Evening"], {"brightness": ddc.DEFAULT_CONFIG["presets"]["Evening"]["brightness"],
                                              "contrast": None, "warmth": None})
        self.assertEqual(presets["Day"], {"brightness": 100, "contrast": 0,
                                          "warmth": ddc.DEFAULT_CONFIG["presets"]["Day"]["warmth"]})

    def test_describing_a_preset(self):
        self.assertEqual(ddc.describe({"brightness": 30, "contrast": None, "warmth": 20}), "Brightness 30 · warmth 20")

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
        self.assertEqual(config["presets"], {"Night": {**ddc.DEFAULT_CONFIG["presets"]["Night"], "brightness": 5}})
        self.assertEqual(config["schedule"], [["09:00", "Day"]])
        ddc.CONFIG_FILE.write_text("[1, 2, 3]")
        self.assertEqual(ddc.load_config(), ddc.DEFAULT_CONFIG)

    def test_defaults_are_not_shared_between_loads(self):
        ddc.load_config()["schedule"].append(["01:00", "Max"])
        self.assertEqual(len(ddc.load_config()["schedule"]), len(ddc.DEFAULT_CONFIG["schedule"]))
        ddc.presets()["Day"]["brightness"] = 1
        self.assertEqual(ddc.presets()["Day"], ddc.DEFAULT_CONFIG["presets"]["Day"])


class ApplyPresetTest(FreshFiles):
    """Nothing here reaches a real monitor or screen: every setter is replaced."""

    def test_a_preset_sets_brightness_contrast_and_warmth(self):
        import service
        ddc.update_config(presets={"Night": {"brightness": 10, "contrast": 30, "warmth": 50},
                                   "Max": {"brightness": 100, "contrast": None, "warmth": None}})
        with mock.patch.object(ddc, "set_brightness", return_value=[(10, 100)]) as brightness, \
                mock.patch.object(ddc, "set_contrast") as contrast, \
                mock.patch.object(service.warmth, "set_level") as warmth:
            self.assertEqual(service.apply_preset("Night"), [(10, 100)])
            self.assertEqual(brightness.call_args[0][0](0, 255), 26)  # 10% of the monitor's range
            self.assertEqual(contrast.call_args[0][0](0, 100), 30)
            warmth.assert_called_once_with(50, now=True)
            for setter in (brightness, contrast, warmth):
                setter.reset_mock()
            service.apply_preset("Max")
            brightness.assert_called_once()
            contrast.assert_not_called()
            warmth.assert_not_called()



class FakeMonitor:
    """Answers ddcutil commands like a monitor, in memory. Nothing reaches a real one."""

    CAPABILITIES = """Model: W2252
   Feature: 14 (Select color preset)
      Values:
         05: 6500 K
         06: 7500 K
         0b: User 1
   Feature: 16 (Video gain: Red)
"""

    def __init__(self, **values):
        # A factory reset: full brightness, factory contrast, the 6500 K preset, even gains.
        self.factory = {0x10: 100, 0x12: 70, 0x14: 0x05, 0x16: 50, 0x18: 50, 0x1A: 50}
        self.values = {**self.factory, **{int(k[1:], 16): v for k, v in values.items()}}
        self.ignore = 0  # how many writes after a reset are ignored

    def __call__(self, *args, timeout=15):
        args = list(args[2:])  # drop "--bus", "5"
        if args[0] == "capabilities":
            return ddcutil_result(self.CAPABILITIES)
        if args[0] == "getvcp":
            code = int(args[-1], 16)
            if code not in self.values:
                return ddcutil_result("", 1)
            if code == 0x14:
                return ddcutil_result(f"VCP 14 SNC x{self.values[code]:02x}\n")
            return ddcutil_result(f"VCP {code:02X} C {self.values[code]} 100\n")
        code, value = int(args[1], 16), int(args[2])
        if code == ddc.FACTORY_RESET:
            self.values.update({k: v for k, v in self.factory.items() if k in self.values})
            self.ignore = 2
        elif self.ignore:
            self.ignore -= 1  # still resetting: accepted, then forgotten
        elif code in self.values:
            self.values[code] = value
        return ddcutil_result("")


class ColourTest(FreshFiles):
    base = {"red": 48, "blue": 66, "red_max": 100, "blue_max": 100}

    def test_read_setting_reads_levels_and_choices(self):
        for output, value in (("VCP 14 SNC x0b\n", 11), ("VCP 16 C 48 100\n", 48), ("VCP 14 ERR\n", None)):
            with mock.patch.object(ddc, "ddcutil", return_value=ddcutil_result(output)):
                self.assertEqual(ddc.read_setting(5, ddc.COLOUR_PRESET), value)

    def test_factory_reset_keeps_levels_and_colour(self):
        monitor = FakeMonitor(x10=35, x12=35, x14=0x0B, x16=48, x18=58, x1A=66)
        before = dict(monitor.values)
        with mock.patch.object(ddc, "ddcutil", monitor), mock.patch.object(ddc.time, "sleep"):
            self.assertTrue(ddc.factory_reset(5))
        self.assertEqual(monitor.values, before)

    def test_factory_reset_skips_what_the_monitor_doesnt_report(self):
        monitor = FakeMonitor(x10=35)
        for code in (0x14, 0x16, 0x18, 0x1A):
            del monitor.values[code]
        with mock.patch.object(ddc, "ddcutil", monitor), mock.patch.object(ddc.time, "sleep"):
            self.assertTrue(ddc.factory_reset(5))
        self.assertEqual(monitor.values, {0x10: 35, 0x12: 70})

    def test_no_reset_for_a_monitor_that_doesnt_answer(self):
        with mock.patch.object(ddc, "ddcutil", return_value=ddcutil_result("", 1)) as ddcutil:
            self.assertFalse(ddc.factory_reset(5))
        self.assertNotIn("04", [call.args[-2] for call in ddcutil.call_args_list if "setvcp" in call.args])

    def test_balance_moves_red_and_blue_apart_from_the_base(self):
        self.assertEqual(ddc.colour_gains(self.base, 0), (48, 66))
        self.assertEqual(ddc.colour_gains(self.base, 20), (58, 53))  # warmer: more red, less blue
        self.assertEqual(ddc.colour_gains(self.base, -20), (38, 79))
        self.assertEqual(ddc.colour_gains(self.base, 999), ddc.colour_gains(self.base, ddc.COLOUR_RANGE))
        self.assertEqual(ddc.colour_gains({**self.base, "blue": 90}, -50), (24, 100))  # never past the maximum

    def test_finds_the_user_colour_mode(self):
        self.assertEqual(ddc.user_colour_preset(FakeMonitor.CAPABILITIES), 0x0B)
        self.assertIsNone(ddc.user_colour_preset("Feature: 14 (Select color preset)\n   Values:\n      05: 6500 K\n"))
        self.assertIsNone(ddc.user_colour_preset(""))

    def test_first_use_switches_to_the_user_mode_and_saves_the_base(self):
        monitor = FakeMonitor()
        with mock.patch.object(ddc, "ddcutil", monitor), mock.patch.object(ddc.time, "sleep"):
            base = ddc.colour_base(5)
        self.assertEqual(monitor.values[0x14], 0x0B)
        self.assertEqual(base, {"red": 50, "blue": 50, "red_max": 100, "blue_max": 100, "balance": 0})
        ddc.save_colour_balance(5, 15)
        with mock.patch.object(ddc, "ddcutil") as ddcutil:
            self.assertEqual(ddc.colour_base(5)["balance"], 15)  # saved: the monitor isn't asked again
        ddcutil.assert_not_called()

    def test_already_in_a_user_mode_stays_there(self):
        monitor = FakeMonitor(x14=0x0B, x16=48, x1A=66)
        with mock.patch.object(ddc, "ddcutil", monitor), mock.patch.object(ddc, "write_vcp") as write:
            self.assertEqual(ddc.colour_base(5)["red"], 48)
        write.assert_not_called()

    def test_gain_writes_dont_count_as_brightness_or_contrast(self):
        with mock.patch.object(ddc, "write_vcp", return_value=True):
            self.assertTrue(ddc.Writer(5)._write(ddc.RED_GAIN, 40))
        self.assertNotIn("brightness", ddc.load_state())


class LaptopOffsetTest(FreshFiles):
    def test_offset_both_ways_and_clamped(self):
        self.assertEqual((ddc.laptop_level(40), ddc.monitor_level(40)), (40, 40))
        ddc.update_config(laptop_offset=15)
        self.assertEqual((ddc.laptop_level(40), ddc.monitor_level(55)), (55, 40))
        self.assertEqual((ddc.laptop_level(95), ddc.monitor_level(10)), (100, 0))
        ddc.update_config(laptop_offset=-80)
        self.assertEqual(ddc.load_config()["laptop_offset"], -50)

    def test_the_laptop_follows_the_monitor_with_the_offset(self):
        ddc.update_config(laptop_offset=10)
        ddc.update_state(buses=[5])
        laptop = mock.Mock()
        with mock.patch.dict(sys.modules, laptop=laptop), \
                mock.patch.object(ddc, "read_vcp", return_value=(20, 100)), \
                mock.patch.object(ddc, "write_vcp", return_value=True):
            ddc.set_brightness(ddc.level(35))
        laptop.set_brightness.assert_called_once_with(45)

    def test_the_service_applies_the_offset_both_ways(self):
        import service
        ddc.update_config(laptop_offset=10)
        ddc.update_state(buses=[5], maxima={"5": {"brightness": 100}})
        with mock.patch.object(service.laptop, "get_brightness", return_value=45):
            self.assertEqual(service.current_level(), 35)
            with mock.patch.object(ddc, "read_vcp", return_value=(20, 100)), \
                    mock.patch.object(ddc, "write_vcp", return_value=True) as write:
                service.Service.follow_laptop_now(None)
            write.assert_called_once_with(5, ddc.BRIGHTNESS, 35)
        with mock.patch.object(service.laptop, "set_brightness") as set_laptop:
            service.Service.set_brightness(SimpleNamespace(request_all=lambda *a: None), 30)
        set_laptop.assert_called_once_with(40, wait=False)


class MonitorSyncTest(FreshFiles):
    def test_on_by_default(self):
        self.assertTrue(ddc.load_config()["sync_monitors"])

    def test_a_new_monitor_takes_the_current_level_only_when_in_sync(self):
        import service
        ddc.update_state(buses=[5])
        displays = [(5, "LG"), (7, "Dell")]
        for in_sync, expected in ((True, 1), (False, 0)):
            ddc.update_config(sync_monitors=in_sync)
            ddc.update_state(buses=[5])
            with self.subTest(in_sync=in_sync), \
                    mock.patch.object(ddc, "detect_displays", return_value=displays), \
                    mock.patch.object(service, "read_levels"), \
                    mock.patch.object(service, "current_level", return_value=40), \
                    mock.patch.object(ddc, "set_brightness_on") as set_new:
                service.add_new_monitors()
            self.assertEqual(set_new.call_count, expected)

    def test_one_slider_moves_the_other_monitors_on_their_own_range(self):
        import gui

        class FakeScale:
            def __init__(self, upper):
                self.upper, self.value = upper, None

            def get_adjustment(self):
                return SimpleNamespace(get_upper=lambda: self.upper)

            def handler_block(self, _handler):
                return mock.MagicMock()

            def set_value(self, value):
                self.value = value

        def monitor(bus, upper):
            group = SimpleNamespace(writer=SimpleNamespace(bus=bus, request=mock.Mock()),
                                    scales={ddc.BRIGHTNESS: FakeScale(upper)}, handlers={ddc.BRIGHTNESS: 1})
            group.follow = lambda code, percent: gui.MonitorGroup.follow(group, code, percent)
            return group

        lg, dell, old = monitor(5, 100), monitor(7, 255), monitor(9, 100)
        window = SimpleNamespace(monitor_groups={5: lg, 7: dell, 9: old})
        gui.DimlyWindow.sync_monitors(window, lg, ddc.BRIGHTNESS, 40)
        lg.writer.request.assert_not_called()  # it set its own level already
        dell.writer.request.assert_called_once_with(ddc.BRIGHTNESS, 102)  # 40% of 255
        old.writer.request.assert_called_once_with(ddc.BRIGHTNESS, 40)
        self.assertEqual(dell.scales[ddc.BRIGHTNESS].value, 102)
        gui.DimlyWindow.sync_monitors(window, lg, ddc.CONTRAST, 30)  # none of them shows contrast
        dell.writer.request.assert_called_once()

if __name__ == "__main__":
    unittest.main()
