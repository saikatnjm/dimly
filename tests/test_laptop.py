"""Tests for laptop.py's echo detection. Needs PyGObject, as the app does."""

import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

_home = tempfile.mkdtemp(prefix="dimly-test-")
for name in ("XDG_CONFIG_HOME", "XDG_CACHE_HOME", "XDG_STATE_HOME", "XDG_RUNTIME_DIR"):
    os.environ[name] = os.path.join(_home, name.lower())
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import ddc  # noqa: E402
import laptop  # noqa: E402


class EchoTest(unittest.TestCase):
    def setUp(self):
        ddc.STATE_FILE.unlink(missing_ok=True)

    def test_own_changes_are_recognised_with_a_little_rounding(self):
        with mock.patch.object(laptop, "session_bus"):
            laptop.set_brightness(0)  # clamped to the minimum, as GNOME will report it
        self.assertTrue(laptop.is_own_change(laptop.MINIMUM))
        self.assertTrue(laptop.is_own_change(laptop.MINIMUM + laptop.ECHO_TOLERANCE))
        self.assertFalse(laptop.is_own_change(50))

    def test_old_changes_no_longer_count(self):
        ddc.update_state(laptop_echoes=[[40, time.time() - laptop.ECHO_SECONDS - 1]])
        self.assertFalse(laptop.is_own_change(40))

    def test_only_recent_changes_are_kept(self):
        with mock.patch.object(laptop, "session_bus"):
            for value in range(10, 60, 2):
                laptop.set_brightness(value)
        self.assertLessEqual(len(ddc.load_state()["laptop_echoes"]), 10)


if __name__ == "__main__":
    unittest.main()
