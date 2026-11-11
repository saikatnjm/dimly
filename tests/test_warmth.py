"""Tests for warmth.py: the desktop's night light settings, and Dimly's own tint without one."""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

_home = tempfile.mkdtemp(prefix="dimly-test-")
for name in ("XDG_CONFIG_HOME", "XDG_CACHE_HOME", "XDG_STATE_HOME", "XDG_RUNTIME_DIR"):
    os.environ[name] = os.path.join(_home, name.lower())
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import warmth  # noqa: E402


class FakeNightLight:
    """Stands in for Gio.Settings, with the night light's keys."""

    def __init__(self, **values):
        self.values = {"night-light-enabled": False, "night-light-temperature": 4000,
                       "night-light-schedule-automatic": True,
                       "night-light-schedule-from": 20.0, "night-light-schedule-to": 6.0, **values}

    def get_boolean(self, key):
        return self.values[key]

    get_uint = get_double = get_boolean

    def set_boolean(self, key, value):
        self.values[key] = value

    set_uint = set_double = set_boolean


class WarmthTest(unittest.TestCase):
    def test_levels_map_onto_colour_temperatures(self):
        self.assertEqual(warmth.to_kelvin(0), warmth.COOLEST)
        self.assertEqual(warmth.to_kelvin(100), warmth.WARMEST)
        for level in (1, 25, 50, 99):
            self.assertEqual(warmth.from_kelvin(warmth.to_kelvin(level)), level)

    def test_zero_turns_the_night_light_off(self):
        night_light = FakeNightLight(**{"night-light-enabled": True})
        warmth.set_warmth(night_light, 0)
        self.assertEqual(warmth.get_warmth(night_light), 0)
        warmth.set_warmth(night_light, 40)
        self.assertTrue(night_light.values["night-light-enabled"])
        self.assertEqual(warmth.get_warmth(night_light), 40)

    def setUp(self):
        warmth.ddc.STATE_FILE.unlink(missing_ok=True)

    def test_all_the_time_and_back_to_sunset(self):
        night_light = FakeNightLight()
        warmth.set_when(night_light, "always")
        self.assertEqual(warmth.get_when(night_light), "always")
        warmth.set_when(night_light, "usual")
        self.assertEqual(warmth.get_when(night_light), "usual")
        self.assertTrue(night_light.values["night-light-schedule-automatic"])

    def test_your_own_hours_come_back_after_all_the_time(self):
        night_light = FakeNightLight(**{"night-light-schedule-automatic": False,
                                        "night-light-schedule-from": 19.5, "night-light-schedule-to": 7.0})
        self.assertEqual(warmth.get_when(night_light), "usual")
        warmth.set_when(night_light, "always")
        self.assertEqual(warmth.get_when(night_light), "always")
        warmth.set_when(night_light, "usual")
        self.assertEqual((night_light.values["night-light-schedule-automatic"],
                          night_light.values["night-light-schedule-from"],
                          night_light.values["night-light-schedule-to"]), (False, 19.5, 7.0))

    def test_all_the_time_stays_within_the_settings_hour_range(self):
        night_light = FakeNightLight()
        warmth.set_when(night_light, "always")
        self.assertEqual(night_light.values["night-light-schedule-to"], 0.0)

    def test_a_preset_shows_its_warmth_at_once(self):
        night_light = FakeNightLight()
        with mock.patch.object(warmth, "settings", return_value=night_light):
            warmth.set_level(30)
            self.assertEqual(warmth.get_when(night_light), "usual")  # the slider leaves the hours alone
            warmth.set_level(30, now=True)
            self.assertEqual(warmth.get_when(night_light), "always")
            self.assertEqual(warmth.get_level(), 30)


class OwnTintTest(unittest.TestCase):
    def setUp(self):
        warmth.ddc.STATE_FILE.unlink(missing_ok=True)

    def test_no_tint_at_warmth_zero(self):
        self.assertEqual(warmth.white_point(warmth.to_kelvin(0)), (1.0, 1.0, 1.0))
        self.assertEqual(warmth.ramp(4, 1.0), [0, 21845, 43690, 65535])

    def test_warmer_takes_away_blue_first_then_green(self):
        points = [warmth.white_point(warmth.to_kelvin(level)) for level in range(0, 101, 10)]
        for cooler, warmer in zip(points, points[1:]):
            self.assertEqual(warmer[0], 1.0)
            self.assertLess(warmer[1], cooler[1])
            self.assertLess(warmer[2], cooler[2])
            self.assertLessEqual(warmer[2], warmer[1])
        self.assertGreater(points[-1][2], 0)  # the warmest still shows blue things, dimly

    def test_without_a_night_light_dimly_tints_the_screens_itself(self):
        with mock.patch.object(warmth, "settings", return_value=None), \
                mock.patch.object(warmth, "_x11", return_value=object()), \
                mock.patch.object(warmth, "_set_ramps", return_value=True) as set_ramps:
            self.assertTrue(warmth.available())
            self.assertEqual(warmth.get_level(), 0)
            warmth.set_level(40, now=True)
            set_ramps.assert_called_once_with(40)
            self.assertEqual(warmth.get_level(), 40)
            set_ramps.reset_mock()
            warmth.restore()  # after logging in again
            set_ramps.assert_called_once_with(40)

    def test_nothing_to_change_on_wayland_without_a_night_light(self):
        with mock.patch.object(warmth, "settings", return_value=None), \
                mock.patch.dict(os.environ, {"XDG_SESSION_TYPE": "wayland"}):
            self.assertFalse(warmth.available())
            self.assertIsNone(warmth.get_level())
            warmth.set_level(40)
            self.assertNotIn("warmth", warmth.ddc.load_state())

if __name__ == "__main__":
    unittest.main()
